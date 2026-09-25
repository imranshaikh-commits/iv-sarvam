"""
Sprint 5 Pass 4 — architecture diagram framework (MVP).

Dynamic, approval-gated architecture diagrams that can be embedded into the
generated proposal DOCX.

Security / safety model (non-negotiable):
  * The LLM emits a STRUCTURED ``DiagramSpec`` (typed nodes/edges), never raw
    DOT. This module builds DOT deterministically from the validated spec, so a
    model (or user) can never inject DOT / shell / attribute payloads. Every
    label is escaped; node/edge counts and label lengths are capped to prevent
    runaway renders.
  * Rendering is a DETERMINISTIC local Graphviz call (`dot`) only. No Mermaid CLI
    (Chromium dep), no Kroki (leaks client data), no image-generation model. If
    `dot` is unavailable the render FAILS SOFT (returns None) so it can never
    break core proposal generation.

Design rules (mirrors document_engine / supabase_client):
  * This module MUST NOT import app.py. The shared ``_structured_with_fallback``
    LLM helper is INJECTED as a callable, keeping this module importable in a
    keyless environment (CI / smoke tests) with no secrets and no network.
"""

from __future__ import annotations

import logging
import shutil
import os
import re
import subprocess
from typing import Awaitable, Callable, Literal, Optional

from pydantic import BaseModel, Field, field_validator

log = logging.getLogger("shilpi-brain.diagram-engine")

# --- hard caps (anti-runaway / anti-injection) ------------------------------
MAX_NODES = 40
MAX_EDGES = 80
MAX_LABEL_LEN = 80
MAX_TITLE_LEN = 120
MAX_ID_LEN = 64

# Per-call LLM budget for diagram-spec generation. Was 1500; a reasoning model
# spends much of that before writing any JSON, so the spec came back cut off
# and the call fell through to the fallback. Billing is on tokens generated.
DIAGRAM_SPEC_MAX_TOKENS = int(os.environ.get("SHILPI_DIAGRAM_SPEC_MAX_TOKENS", "4000"))
DIAGRAM_REASONING_EFFORT = os.environ.get("SHILPI_DIAGRAM_REASONING_EFFORT", "low").strip()

# READABILITY limits, below the hard caps above. ESNAD 09-24's future-state and
# joiner diagrams carried ~40 edges each and rendered as a tangle that is
# unreadable at page width; IV's own diagrams hold roughly a dozen elements.
# Over these, the spec is sent back once to be simplified.
SOFT_MAX_NODES = int(os.environ.get("SHILPI_DIAGRAM_SOFT_MAX_NODES", "14"))
SOFT_MAX_EDGES = int(os.environ.get("SHILPI_DIAGRAM_SOFT_MAX_EDGES", "18"))

# Allowlisted diagram types. Anything else is coerced to "architecture".
DIAGRAM_TYPES = ("architecture", "flow", "sequence", "network", "data_flow", "component",
                 "stack")

# Graphviz rankdir per diagram type.
_RANKDIR = {
    "architecture": "TB",
    "component": "TB",
    "flow": "TB",
    "data_flow": "LR",
    "sequence": "LR",
    "network": "LR",
    "stack": "LR",
}

# Approval state machine. draft -> needs_review -> approved | rejected;
# rejected -> draft re-draft (iteration bump). Any other transition is invalid.
DIAGRAM_STATES = ("draft", "needs_review", "approved", "rejected")
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"needs_review"},
    "needs_review": {"approved", "rejected"},
    "rejected": {"draft"},
    "approved": set(),  # terminal
}


# ---------------------------------------------------------------------------
# DiagramSpec schema (the ONLY thing the LLM is allowed to emit)
# ---------------------------------------------------------------------------

# Node roles -> D2 shapes. IV's own workflow diagrams use a decision diamond
# ("Employee? -> YES / CONTRACTOR") and distinguish stores from process steps.
# Everything Shilpi drew before was a rectangle, so a joiner flow read as a
# chain of boxes with no branch point.
NODE_SHAPES = {
    "process": "rectangle",
    "decision": "diamond",
    "datastore": "cylinder",
    "external": "package",
    "start": "oval",
    "end": "oval",
    "person": "person",
    "queue": "queue",
}
DEFAULT_NODE_SHAPE = "process"


class DiagramNode(BaseModel):
    id: str = Field(..., description="Stable node identifier, e.g. 'idp' or 'hr_source'. Short, alphanumeric.")
    label: str = Field(..., description="Human-readable node label shown in the diagram.")
    group: Optional[str] = Field(
        None,
        description="Logical grouping. In an architecture or network diagram this is a "
                    "zone (e.g. 'DMZ', 'Production Data Centre'). In a FLOW diagram it is "
                    "the SWIMLANE: the system or actor that performs this step "
                    "(e.g. 'HRMS', 'SailPoint IdentityIQ', 'Manager', 'Active Directory').",
    )
    shape: str = Field(
        DEFAULT_NODE_SHAPE,
        description="Node role, one of: " + ", ".join(NODE_SHAPES) +
                    ". Use 'decision' for any branch point (a question with two or more "
                    "outcomes), 'datastore' for databases and directories, 'external' for "
                    "third-party systems, 'start'/'end' for flow terminators.",
    )

    @field_validator("shape")
    @classmethod
    def _coerce_shape(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in NODE_SHAPES else DEFAULT_NODE_SHAPE


class DiagramEdge(BaseModel):
    source: str = Field(..., description="id of the source node (must match a node id).")
    target: str = Field(..., description="id of the target node (must match a node id).")
    label: Optional[str] = Field(None, description="Optional edge label, e.g. 'SCIM', 'SAML'.")


class DiagramSpec(BaseModel):
    """Structured, safe representation of an architecture diagram.

    The renderer builds DOT from THIS — raw DOT is never accepted from the LLM
    or a user.
    """

    diagram_type: str = Field(
        "architecture",
        description="One of: " + ", ".join(DIAGRAM_TYPES),
    )
    title: str = Field(..., description="Diagram title.")
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)

    @field_validator("diagram_type")
    @classmethod
    def _coerce_type(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in DIAGRAM_TYPES else "architecture"


# ---------------------------------------------------------------------------
# Sanitization — enforce caps, escape labels, drop dangling edges
# ---------------------------------------------------------------------------

GROUP_REF = "group:"


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())  # collapse whitespace/newlines
    return text[:limit].strip()


def _safe_node_id(raw: str, index: int) -> str:
    """Reduce an arbitrary id to a DOT-safe token; never trust the LLM's id."""
    token = "".join(ch for ch in (raw or "") if ch.isalnum() or ch == "_")[:MAX_ID_LEN]
    return token or f"n{index}"


def _escape_label(text: str) -> str:
    r"""Escape a label for a DOT double-quoted string.

    Backslash first, then double-quote. Newlines/tabs are already collapsed by
    ``_clip``. This is what prevents attribute/DOT injection via labels."""
    return (text or "").replace("\\", "\\\\").replace('"', '\\"')


def sanitize_spec(spec: DiagramSpec) -> DiagramSpec:
    """Return a NEW DiagramSpec that is safe to render.

    - caps node/edge counts and label/title lengths
    - rewrites node ids to DOT-safe unique tokens
    - drops edges that reference unknown nodes
    - de-duplicates node ids
    """
    title = _clip(spec.title, MAX_TITLE_LEN) or "Architecture Diagram"

    safe_nodes: list[DiagramNode] = []
    id_map: dict[str, str] = {}  # original id -> safe id
    used: set[str] = set()
    for i, node in enumerate(spec.nodes[:MAX_NODES]):
        safe_id = _safe_node_id(node.id, i)
        # ensure uniqueness after sanitization
        base, k = safe_id, 1
        while safe_id in used:
            safe_id = f"{base}_{k}"
            k += 1
        used.add(safe_id)
        # first occurrence of an original id wins the mapping
        id_map.setdefault(node.id, safe_id)
        safe_nodes.append(
            DiagramNode(
                id=safe_id,
                label=_clip(node.label, MAX_LABEL_LEN) or safe_id,
                group=_clip(node.group, MAX_LABEL_LEN) or None if node.group else None,
                # Dropping this rendered every decision diamond, datastore and
                # person as a rectangle ("MFA Required?" in ESNAD 09-24).
                shape=node.shape,
            )
        )

    valid_ids = {n.id for n in safe_nodes}
    # "group:<name>" connects to a whole container (the deterministic solution
    # stack points SSO at every client application at once, not nine arrows).
    valid_ids |= {f"{GROUP_REF}{n.group}" for n in safe_nodes if n.group}
    safe_edges: list[DiagramEdge] = []
    for edge in spec.edges[:MAX_EDGES]:
        src = edge.source if edge.source.startswith(GROUP_REF) else \
            id_map.get(edge.source, _safe_node_id(edge.source, -1))
        tgt = edge.target if edge.target.startswith(GROUP_REF) else \
            id_map.get(edge.target, _safe_node_id(edge.target, -1))
        if src not in valid_ids or tgt not in valid_ids:
            continue  # drop dangling edge rather than inventing a node
        safe_edges.append(
            DiagramEdge(source=src, target=tgt, label=_clip(edge.label, MAX_LABEL_LEN) or None)
        )

    return DiagramSpec(
        diagram_type=spec.diagram_type, title=title, nodes=safe_nodes, edges=safe_edges
    )


# ---------------------------------------------------------------------------
# Deterministic DOT builder
# ---------------------------------------------------------------------------

def build_dot(spec: DiagramSpec) -> str:
    """Build a Graphviz DOT document from a (sanitized) DiagramSpec.

    Always sanitizes first, so callers cannot bypass the caps/escaping.
    """
    spec = sanitize_spec(spec)
    rankdir = _RANKDIR.get(spec.diagram_type, "TB")

    lines: list[str] = [
        "digraph shilpi_diagram {",
        f'  rankdir={rankdir};',
        '  graph [fontname="Helvetica", labelloc="t", '
        f'label="{_escape_label(spec.title)}"];',
        '  node [shape=box, style="rounded,filled", fillcolor="#EEF2FB", '
        'color="#3B4A6B", fontname="Helvetica", fontsize=10];',
        '  edge [color="#3B4A6B", fontname="Helvetica", fontsize=9];',
    ]

    # Group nodes into clusters when a group is present (deterministic order).
    grouped: dict[str, list[DiagramNode]] = {}
    ungrouped: list[DiagramNode] = []
    for node in spec.nodes:
        if node.group:
            grouped.setdefault(node.group, []).append(node)
        else:
            ungrouped.append(node)

    def _emit_node(n: DiagramNode) -> str:
        return f'  "{n.id}" [label="{_escape_label(n.label)}"];'

    for n in ungrouped:
        lines.append(_emit_node(n))

    for ci, (group, members) in enumerate(grouped.items()):
        lines.append(f"  subgraph cluster_{ci} {{")
        lines.append(f'    label="{_escape_label(group)}";')
        lines.append('    style="rounded"; color="#B8C2D9";')
        for n in members:
            lines.append("  " + _emit_node(n))
        lines.append("  }")

    first_in = {}
    for n in spec.nodes:
        if n.group:
            first_in.setdefault(f"{GROUP_REF}{n.group}", n.id)
    for e in spec.edges:
        attr = f' [label="{_escape_label(e.label)}"]' if e.label else ""
        src, tgt = first_in.get(e.source, e.source), first_in.get(e.target, e.target)
        lines.append(f'  "{src}" -> "{tgt}"{attr};')

    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic Graphviz render (fail-soft)
# ---------------------------------------------------------------------------

def dot_available() -> bool:
    """True iff the local Graphviz `dot` binary is on PATH."""
    return shutil.which("dot") is not None


def d2_available() -> bool:
    """True when the `d2` binary is on PATH."""
    return shutil.which("d2") is not None


def _d2_id(raw: str) -> str:
    """Sanitise an id for D2 (no dots — they mean nesting)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", raw or "n") or "n"


# Domain acronyms that must not be title-cased into "Iam" / "Dmz" / "Sso".
_ACRONYMS = {
    "iam": "IAM", "ciam": "CIAM", "pam": "PAM", "dmz": "DMZ", "sso": "SSO",
    "mfa": "MFA", "idp": "IdP", "hrms": "HRMS", "siem": "SIEM", "ad": "AD",
    "api": "API", "vpc": "VPC", "ha": "HA", "dr": "DR", "kms": "KMS",
    "hsm": "HSM", "waf": "WAF", "vip": "VIP", "lb": "LB", "sp": "SP",
}


def _pretty_group(raw: str) -> str:
    """Turn a snake_case group id into a readable heading.

    Belt-and-braces for the prompt rule: the model emitted group names like
    'identity_source_layer' and '_monitoring_layer', which are shown to a human
    reviewer and read as machine output.
    """
    txt = (raw or "").strip().strip("_")
    if "_" in txt or (txt and txt == txt.lower() and " " not in txt):
        txt = txt.replace("_", " ").strip()
        words = [w for w in txt.split() if w]
        if words and words[-1].lower() in ("layer", "tier", "group"):
            words = words[:-1] or words
        txt = " ".join(_ACRONYMS.get(w.lower(), w if w.isupper() else w.capitalize())
                       for w in words)
    return txt or raw


def _d2_label(raw: str) -> str:
    """D2 labels are quoted; escape the quote character."""
    return (raw or "").replace('"', "'").strip()


# --- Inspirit Vision brand (light theme) ------------------------------------
# From the IV brand guide. Deliberately LIGHT-weight: IV's own sample decks are
# mostly white with restrained accent colour, and D2's stock themes fill
# containers with heavy saturated blocks that bury the labels.
IV_CLEARANCE = "#DC5220"        # accent / emphasis (orange)
IV_COSMOS = "#1F0A4A"           # primary dark (deep purple) — text, node strokes
IV_WHITE = "#FFFFFF"
IV_PAPER = "#F5F5F5"            # container fill
IV_ASH = "#8A8A8A"              # edge labels, container hairlines
IV_SLATE = "#4A4A4A"            # edges
IV_CLEARANCE_TINT = "#FFF5F0"   # accent node fill

# Nodes matching these get the accent treatment, so the eye lands on the IAM
# platform itself rather than on peripheral systems.
_ACCENT_HINTS = (
    "pingfederate", "pingam", "pingid", "pingdirectory", "pingidm", "pinggateway",
    "keycloak", "sailpoint", "forgerock", "okta", "entra", "identityiq",
    "identitynow", "iam platform", "idp",
)


def _is_accent(label: str) -> bool:
    low = (label or "").lower()
    return any(h in low for h in _ACCENT_HINTS)


# Colour by the vendor that owns a component, so a reader can tell Ping from
# Saviynt at a glance: ESNAD 09-24's diagrams were all grey and IV's own use a
# colour per platform. (keywords, name for the key, fill, stroke)
_VENDOR_PALETTE: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    (("ping", "forgerock", "aic"), "blue", "#E8F0FE", "#1A56DB"),
    (("saviynt",), "green", "#E6F4EA", "#1E8E3E"),
    (("sailpoint", "identityiq", "identitynow"), "teal", "#E0F2F1", "#00796B"),
    (("okta",), "navy", "#E8EAF6", "#283593"),
    (("cyberark",), "purple", "#F3E5F5", "#6A1B9A"),
)


def _vendor_colour(*texts: Optional[str]):
    low = " ".join(t or "" for t in texts).lower()
    for keys, _name, fill, stroke in _VENDOR_PALETTE:
        if any(re.search(rf"\b{k}", low) for k in keys):
            return fill, stroke
    return None


def legend_for(spec: "DiagramSpec") -> str:
    """One-line colour key for the caption under the diagram."""
    parts, seen = [], set()
    for n in spec.nodes:
        low = f"{n.label} {n.group or ''}".lower()
        for keys, name, _f, _s in _VENDOR_PALETTE:
            hit = next((k for k in keys if re.search(rf"\b{k}", low)), None)
            if hit and name not in seen:
                seen.add(name)
                vendor = {"ping": "Ping Identity", "forgerock": "Ping Identity",
                          "aic": "Ping Identity"}.get(hit, hit.title())
                parts.append(f"{name} = {vendor}")
    if not parts:
        return ""
    parts.append("white = client systems")
    if any(n.shape == "decision" for n in spec.nodes):
        parts.append("diamond = decision")
    return "Colour key: " + "; ".join(parts) + "."


def _iv_style(indent: str, *, fill: str, stroke: str, font_color: str,
              bold: bool = False, stroke_width: int = 2,
              font_size: Optional[int] = None, rounded: bool = True) -> list[str]:
    out = [f"{indent}style: {{",
           f'{indent}  fill: "{fill}"',
           f'{indent}  stroke: "{stroke}"',
           f"{indent}  stroke-width: {stroke_width}",
           f'{indent}  font-color: "{font_color}"']
    # border-radius is a rectangle property; never set it on the diamonds,
    # cylinders and people that sanitize_spec now lets through.
    if rounded:
        out.append(f"{indent}  border-radius: 4")
    if font_size:
        out.append(f"{indent}  font-size: {font_size}")
    if bold:
        out.append(f"{indent}  bold: true")
    out.append(f"{indent}}}")
    return out


# Diagram types whose groups are SWIMLANES (an actor per lane) rather than
# zones. IV's joiner-flow diagram is four horizontal lanes -- HRMS, SailPoint
# IIQ, Manager, Active Directory -- with the process running left to right and
# edges crossing between lanes.
_LANE_TYPES = frozenset({"flow", "sequence"})
_WIDE_TYPES = frozenset({"architecture", "component", "network", "stack"})
# Larger than D2's defaults (16 / 11): a diagram is scaled to a 6in column, and
# ESNAD 09-24's edge labels printed at roughly 4pt.
D2_NODE_FONT = int(os.environ.get("SHILPI_D2_NODE_FONT", "20"))
D2_EDGE_FONT = int(os.environ.get("SHILPI_D2_EDGE_FONT", "15"))


def _stack_label(label: str) -> str:
    """Two short lines for a stack card: 'IGA: a, b, c' -> 'IGA' / 'a · b · c';
    'Workforce users (5,000)' -> 'Workforce users' / '5,000'."""
    m = re.match(r"^(.*?)\s*\(([\d,]+(?: accounts)?)\)$", label)
    if m:
        return f"{m.group(1)}\\n{m.group(2)}"
    head, sep, tail = label.partition(": ")
    if sep:
        return f"{head}\\n" + " · ".join(p.strip() for p in tail.split(","))
    return label


# The stack is the one diagram read as a whole page, so it gets bigger type.
_STACK_FONT = int(os.environ.get("SHILPI_STACK_FONT", "26"))


def build_stack_d2(spec: DiagramSpec) -> str:
    """The solution stack as a fixed grid, in IV's layout: users, one column per
    platform, the client's applications, and a band of identity sources below.

    Graph layout (ELK) put the sources on the far side and looped arrows round
    the whole picture, at a width that printed near 5pt. A grid keeps columns in
    reading order and only draws arrows between neighbouring columns, so no
    arrow crosses a column.
    """
    order: list[str] = []
    members: dict[str, list[DiagramNode]] = {}
    for n in spec.nodes:
        if n.group not in members:
            order.append(n.group or "Other")
            members[n.group or "Other"] = []
        members[n.group or "Other"].append(n)
    sources = [g for g in order if g.lower().startswith("identity sources")]
    users = [g for g in order if g == "Users"]
    vendors = [g for g in order if _vendor_colour(g)]
    apps = [g for g in order if g not in sources + users + vendors]
    columns = users + vendors + apps

    def container(gid: str, group: str, cols: int, indent: str) -> list[str]:
        tint = _vendor_colour(group)
        # "Ping Identity: PingOne Advanced Identity Cloud (SaaS)" -> two lines,
        # and the product line split again so the title never overflows.
        title = group.replace(": ", "\\n", 1).replace(" Identity Cloud", "\\nIdentity Cloud")
        out = [f'{indent}{gid}: "{_d2_label(title)}" {{', f"{indent}  grid-columns: {cols}",
               f"{indent}  grid-gap: 24"]
        out += _iv_style(indent + "  ", fill=IV_PAPER, stroke=tint[1] if tint else IV_ASH,
                         font_color=IV_COSMOS, bold=True, stroke_width=2 if tint else 1,
                         font_size=_STACK_FONT + 2)
        for i, n in enumerate(members[group]):
            fill, stroke = tint or (IV_WHITE, IV_COSMOS)
            out.append(f'{indent}  n{i}: "{_d2_label(_stack_label(n.label))}" {{')
            out += _iv_style(indent + "    ", fill=fill if tint else IV_WHITE, stroke=stroke,
                             font_color=IV_COSMOS, font_size=_STACK_FONT)
            out.append(f"{indent}  }}")
        out.append(f"{indent}}}")
        return out

    lines = ["grid-columns: 1", "grid-gap: 40", "style: {", f'  fill: "{IV_WHITE}"', "}",
             "main: \"\" {", f"  grid-columns: {max(1, len(columns))}", "  grid-gap: 190",
             "  style: {", "    stroke-width: 0", f'    fill: "{IV_WHITE}"', "  }"]
    ids = []
    for i, g in enumerate(columns):
        ids.append(f"c{i}")
        # One column (build_stack_spec caps applications at ten): a taller,
        # narrower page prints larger than a wide one (3140px printed ~5pt).
        lines += container(f"c{i}", g, 1, "  ")
    lines.append("}")
    for g in sources:
        lines += container("sources", g, max(1, len(members[g])), "")
    # Arrows between neighbouring columns only, labelled by what crosses.
    labels = {("Users", "vendor"): "sign-in", ("vendor", "vendor"): "identity data",
              ("vendor", "apps"): "SSO · provisioning"}
    kind = lambda g: "Users" if g in users else "vendor" if g in vendors else "apps"
    for a, b, ga, gb in zip(ids, ids[1:], columns, columns[1:]):
        lab = labels.get((kind(ga), kind(gb)), "")
        lines += [f'main.{a} -> main.{b}: "{lab}" {{', "  style: {",
                  f'    stroke: "{IV_SLATE}"', "    stroke-width: 2",
                  f'    font-color: "{IV_SLATE}"', f"    font-size: {_STACK_FONT - 4}", "  }", "}"]
    # Each platform to the sources band, dashed, as IV's "supporting
    # integration" lines: the band otherwise floated with no connection.
    if sources:
        for cid, g in zip(ids, columns):
            if g not in vendors:
                continue
            labs = [n.label.lower() for n in members[g]]
            access = any(l.startswith(("workforce access", "customer identity")) for l in labs)
            govern = any(l.startswith(("identity governance", "privileged access")) for l in labs)
            lab = " · ".join(x for x, on in (("federation · directory", access),
                                             ("HR feed · accounts · audit", govern)) if on)
            lines += [f'main.{cid} <-> sources: "{lab}" {{', "  style: {",
                      f'    stroke: "{IV_ASH}"', "    stroke-width: 2", "    stroke-dash: 5",
                      f'    font-color: "{IV_SLATE}"', f"    font-size: {_STACK_FONT - 6}",
                      "  }", "}"]
    return "\n".join(lines) + "\n"


def build_d2(spec: DiagramSpec, *, direction: Optional[str] = None) -> str:
    """Render a DiagramSpec as D2 source, styled to the IV light theme.

    D2 is used in preference to Graphviz because it draws ``group`` as a real
    nested container — which is exactly what an IAM deployment diagram needs
    (DMZ / secure zone / data zone). Graphviz clusters exist but lay out poorly.

    Styling is applied per-object rather than via a D2 theme because the stock
    themes paint containers as saturated slabs; IV's own decks are near-white
    with restrained accent colour, and the labels have to stay legible.
    """
    if spec.diagram_type == "stack":
        return build_stack_d2(spec)
    lanes = spec.diagram_type in _LANE_TYPES
    # Lane diagrams stack lanes DOWN and run the process RIGHT inside each one.
    # Structural views read left to right (users -> platforms -> systems), as
    # IV's do. `direction` overrides this: the renderer re-runs a diagram that
    # came out too tall for the page with the axis flipped, and keeps whichever
    # fits.
    root_dir = direction or ("right" if spec.diagram_type in _WIDE_TYPES else "down")
    lines: list[str] = [
        f"direction: {root_dir}",
        "",
        "style: {",
        f'  fill: "{IV_WHITE}"',
        "}",
        "",
    ]

    grouped: dict[str, list[DiagramNode]] = {}
    loose: list[DiagramNode] = []
    for n in spec.nodes:
        if n.group:
            grouped.setdefault(n.group, []).append(n)
        else:
            loose.append(n)

    def emit_node(n: DiagramNode, indent: str) -> list[str]:
        decision = n.shape == "decision"
        vendor = None if decision else _vendor_colour(n.label, n.group)
        out = [f'{indent}{_d2_id(n.id)}: "{_d2_label(n.label)}" {{']
        d2_shape = NODE_SHAPES.get(n.shape, "rectangle")
        if d2_shape != "rectangle":
            out.append(f"{indent}  shape: {d2_shape}")
        fill, stroke = vendor or ((IV_CLEARANCE_TINT, IV_CLEARANCE) if decision
                                  else (IV_WHITE, IV_COSMOS))
        out += _iv_style(indent + "  ", fill=fill, stroke=stroke,
                         font_color=IV_COSMOS, bold=bool(vendor) or decision,
                         font_size=D2_NODE_FONT, rounded=d2_shape == "rectangle")
        out.append(f"{indent}}}")
        return out

    path: dict[str, str] = {}
    for group, members in grouped.items():
        gid = _d2_id(group)
        path[f"{GROUP_REF}{group}"] = gid
        lines.append(f'{gid}: "{_d2_label(_pretty_group(group))}" {{')
        if lanes:
            # The lane itself runs across the page; the lanes stack down.
            lines.append(f"  direction: {'right' if root_dir == 'down' else 'down'}")
        # A zone should read as a boundary, not a coloured slab competing with
        # its own contents: near-white fill, hairline border.
        tint = _vendor_colour(group)
        lines += _iv_style("  ", fill=IV_PAPER, stroke=tint[1] if tint else IV_ASH,
                           font_color=IV_COSMOS, bold=True,
                           stroke_width=2 if tint else 1)
        for n in members:
            path[n.id] = f"{gid}.{_d2_id(n.id)}"
            lines += emit_node(n, "  ")
        lines.append("}")
    for n in loose:
        path[n.id] = _d2_id(n.id)
        lines += emit_node(n, "")

    lines.append("")
    for e in spec.edges:
        src, tgt = path.get(e.source), path.get(e.target)
        if not src or not tgt:
            continue
        label = f': "{_d2_label(e.label)}"' if e.label else ""
        lines.append(f"{src} -> {tgt}{label} {{")
        lines.append("  style: {")
        lines.append(f'    stroke: "{IV_SLATE}"')
        lines.append("    stroke-width: 1")
        lines.append(f'    font-color: "{IV_SLATE}"')
        lines.append(f"    font-size: {D2_EDGE_FONT}")
        lines.append("  }")
        lines.append("}")

    return "\n".join(lines) + "\n"


def _svg_to_png(svg: bytes, width: int) -> Optional[bytes]:
    """Rasterise SVG via librsvg (`rsvg-convert`).

    NOT cairosvg. D2 embeds its fonts as base64 WOFF inside an ``@font-face``
    rule, which cairosvg does not support — it silently renders every glyph as a
    filled black box, producing a diagram of unreadable bars. librsvg resolves
    the embedded fonts correctly (measured: ~28% anti-aliased midtone pixels vs
    cairosvg's ~2%, i.e. real text vs solid rectangles).
    """
    if not shutil.which("rsvg-convert"):
        log.warning("rsvg-convert not found; cannot rasterise SVG (fail-soft).")
        return None
    try:
        proc = subprocess.run(
            ["rsvg-convert", "-w", str(width), "-b", "white", "-f", "png"],
            input=svg, capture_output=True, timeout=30, check=True,
        )
        return proc.stdout or None
    except (subprocess.SubprocessError, OSError) as e:  # noqa: BLE001
        log.warning("rsvg-convert failed (%s); falling back to Graphviz.", e)
        return None


_SVG_SIZE_RE = re.compile(rb'<svg[^>]*?width="(\d+(?:\.\d+)?)"[^>]*?height="(\d+(?:\.\d+)?)"')

# A letter page with IV's margins leaves a text column about 5.9in wide and 9in
# tall, so anything past ~1.5:1 tall-to-wide renders as a sliver. Amlak runs 3
# to 5 embedded diagrams at 1800x4217 and 1800x3644 (2.3:1 and 2.0:1), which
# scaled down to 3.2in wide to fit the height. Unreadable.
MAX_ASPECT_RATIO = float(os.environ.get("SHILPI_DIAGRAM_MAX_ASPECT", "1.5"))
# The flip can overshoot badly: an 8-zone deployment diagram went from 3.49
# (a sliver) to 0.09 (an 11:1 strip that scales to nothing). Both are unusable,
# so candidates are scored against a BAND rather than "smaller wins".
MIN_ASPECT_RATIO = float(os.environ.get("SHILPI_DIAGRAM_MIN_ASPECT", "0.4"))


def _aspect_penalty(aspect: Optional[float]) -> float:
    """How far outside the usable band this aspect sits. 0.0 means it fits."""
    if aspect is None:
        return float("inf")
    if aspect > MAX_ASPECT_RATIO:
        return aspect / MAX_ASPECT_RATIO
    if aspect < MIN_ASPECT_RATIO:
        return MIN_ASPECT_RATIO / aspect
    return 0.0


def _svg_aspect(svg: bytes) -> Optional[float]:
    """Height / width of a rendered SVG, or None if it cannot be measured."""
    m = _SVG_SIZE_RE.search(svg[:2000])
    if not m:
        return None
    w, h = float(m.group(1)), float(m.group(2))
    return (h / w) if w else None


# Measure text with the font the PNG is drawn in. D2 sizes boxes with its own
# Source Sans; librsvg on the server draws with DejaVu, which is wider, so text
# ran into box edges (ESNAD 09-25). Pointing D2 at DejaVu makes both agree.
# Only passed when the file exists (the container has fonts-dejavu-core).
_D2_FONTS = tuple(
    (flag, path) for flag, path in (
        ("--font-regular", os.environ.get("SHILPI_D2_FONT_REGULAR",
                                          "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")),
        ("--font-bold", os.environ.get("SHILPI_D2_FONT_BOLD",
                                       "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
    ) if path and os.path.exists(path))


def _d2_run(source: str, timeout: float, layout: Optional[str] = None) -> Optional[bytes]:
    try:
        fonts = [x for pair in _D2_FONTS for x in pair]
        proc = subprocess.run(
            ["d2", "--theme", D2_THEME, "--layout", layout or D2_LAYOUT,
             "--pad", "40", *fonts, "-", "-"],
            input=source.encode("utf-8"),
            capture_output=True, timeout=timeout, check=True,
        )
        return proc.stdout
    except (subprocess.SubprocessError, OSError) as e:  # noqa: BLE001
        log.warning("D2 render failed (%s); falling back to Graphviz.", e)
        return None


def _render_with_d2(spec: DiagramSpec, fmt: str, timeout: float) -> Optional[bytes]:
    """Render via D2. SVG is native; PNG goes through librsvg.

    D2's own PNG export shells out to a headless browser, which we deliberately
    avoid in the container — SVG plus librsvg keeps the image path dependency-
    light and offline.

    A diagram that comes out too tall for the page is re-laid-out with the axis
    flipped and the WIDER result kept. This is measured, not guessed: the aspect
    ratio is read from the rendered SVG, both candidates are compared, and the
    flip is discarded when it does not actually help.
    """
    svg = _d2_run(build_d2(spec), timeout)
    if svg is None:
        return None

    aspect = _svg_aspect(svg)
    best_penalty = _aspect_penalty(aspect)
    # A grid (the solution stack) ignores direction and layout engine, so
    # re-rendering it can only return the same picture.
    if best_penalty > 0 and spec.diagram_type != "stack":
        # Try a small set of alternative layouts and KEEP THE BEST MEASURED one.
        # Flipping the axis is not reliably an improvement: an 8-zone chain goes
        # from 3.49 (sliver) to 0.09 (an 11:1 strip), which is worse. Scoring
        # every candidate against the band and keeping the winner means a
        # candidate that does not help is simply discarded.
        # Try the OTHER axis: structural views default to left-to-right, and
        # the old list only ever tried "right", so a too-wide architecture
        # diagram (ESNAD 09-25: aspect 0.23-0.29) could never flip.
        flip = "down" if spec.diagram_type in _WIDE_TYPES else "right"
        for alt_dir, alt_layout in ((flip, None), (None, "dagre"), (flip, "dagre")):
            alt = _d2_run(build_d2(spec, direction=alt_dir), timeout, layout=alt_layout)
            if alt is None:
                continue
            alt_aspect = _svg_aspect(alt)
            penalty = _aspect_penalty(alt_aspect)
            if penalty < best_penalty:
                svg, aspect, best_penalty = alt, alt_aspect, penalty
            if best_penalty == 0:
                break
        if best_penalty > 0:
            # Honest log rather than a silent near-miss: some diagrams simply
            # have too many zones in a chain for ANY layout to fit the page.
            # That is a spec-content problem, not a renderer one.
            log.warning("diagram '%s' still outside the usable aspect band "
                        "(%.2f); consider fewer zones per diagram",
                        spec.title, aspect or -1)
        else:
            log.info("diagram '%s' re-laid out to aspect %.2f", spec.title, aspect)

    if fmt == "svg":
        return svg
    return _svg_to_png(svg, D2_PNG_WIDTH)


D2_THEME = os.environ.get("SHILPI_D2_THEME", "4")       # 4 = neutral corporate
# ELK (Eclipse Layout Kernel) over the default dagre: more mature, actively
# maintained, and it produces clean orthogonal edge routing with far better
# handling of nested containers — which is what zone-based IAM deployment
# diagrams are. Both are bundled in the D2 binary and free (MPL-2.0); TALA is
# Terrastruct's paid engine and is deliberately NOT used (commercial use needs a
# licence, and unlicensed renders carry a watermark).
D2_LAYOUT = os.environ.get("SHILPI_D2_LAYOUT", "elk")

# Per-part prompt budgets for spec generation. Deliberately modest: the prompt
# was only ever ~4k and the model still stalled, so size is not the bottleneck —
# a bigger prompt would just cost more for no gain.
_SPEC_CONTEXT_BUDGET = int(os.environ.get("SHILPI_SPEC_CONTEXT_BUDGET", "3500"))
# A spec with no nodes is schema-VALID (nodes defaults to []) but useless — the
# model returned exactly that in a live run and we rendered a diagram containing
# only its own title. Content is therefore checked explicitly.
#
# The bar is deliberately LOW: this guard exists to catch broken output, not to
# enforce richness (that is the guidance prompt's job). A two-node spec
# (source -> target) is a legitimate minimal diagram, so rejecting it would trade
# one failure mode for another.
MIN_SPEC_NODES = int(os.environ.get("SHILPI_MIN_SPEC_NODES", "2"))

# A diagram is a GRAPH. A spec with plenty of nodes and almost no edges renders
# as a grouped list, which is exactly what happened live: 12 nodes, 1 edge. A
# connected graph needs at least n-1 edges, so that is the bar — and orphan
# nodes (no incident edge at all) are counted separately because a few hub-and-
# spoke shapes can satisfy the count while still leaving components floating.
MIN_EDGE_RATIO = float(os.environ.get("SHILPI_MIN_EDGE_RATIO", "0.6"))
MAX_ORPHAN_RATIO = float(os.environ.get("SHILPI_MAX_ORPHAN_RATIO", "0.34"))


# Labels that read as OUTCOMES of a choice rather than destinations. A node with
# two edges labelled "Yes"/"No" is a decision; a node with two edges labelled
# "to Active Directory"/"to Target Applications" is a provisioning step that
# fans out.
_OUTCOME_LABEL_RE = re.compile(
    r"^\s*(yes|no|y|n|true|false|approved|rejected|denied|success|failure|failed|"
    r"pass|fail|valid|invalid|match|no match|found|not found|exists|does not exist|"
    r"employee|contractor|new|existing|internal|external|"
    r"if\s|when\s|otherwise|else)\b",
    re.I)


def _is_branch_point(node: "DiagramNode", outgoing: list) -> bool:
    """Is this node a DECISION, or just a step that fans out?

    Run 12 lost its joiner-flow diagram twice because the rule was "more than
    one labelled outgoing edge means decision". That is false for provisioning:
    "Provision Accounts & Entitlements", "Provision AD Account & Groups" and
    "Set Initial Password & Propagate" all fan out to several targets and are
    not choices. The corrective retry then told the model to mark them as
    decisions, it complied, and a DIFFERENT fan-out node tripped the rule --
    two rounds, four different nodes, no convergence.

    A question label is the reliable signal, and it is what the model already
    produces correctly ("Identity Already Exists?", "Manager Approval
    Required?"). Multiple edges only count when their labels read as OUTCOMES
    of a choice rather than as destinations.
    """
    if node.label.strip().endswith("?"):
        return True
    labelled = [(e.label or "").strip() for e in outgoing if (e.label or "").strip()]
    if len(labelled) < 2:
        return False
    return sum(1 for lab in labelled if _OUTCOME_LABEL_RE.match(lab)) >= 2


def spec_shortfall(spec: "DiagramSpec") -> str | None:
    """Describe why a spec is unusable as a diagram, or None if it is fine."""
    n = len(spec.nodes)
    if n < MIN_SPEC_NODES:
        return f"it contained {n} node(s)"
    e = len(spec.edges)
    if e < max(1, int(round((n - 1) * MIN_EDGE_RATIO))):
        return (f"it contained {n} nodes but only {e} edge(s), so the components are "
                "not connected to each other")
    connected = set()
    for edge in spec.edges:
        connected.add(edge.source)
        connected.add(edge.target)
    orphans = [node.id for node in spec.nodes if node.id not in connected]
    if orphans and len(orphans) / n > MAX_ORPHAN_RATIO:
        return (f"{len(orphans)} of {n} components had no connections at all "
                f"({', '.join(orphans[:6])})")

    # A branch point drawn as a rectangle. The model reliably WRITES branch
    # logic into flow diagrams -- run 6 produced "Identity Already Exists?" and
    # "Manager Approval Required?" with correctly labelled Yes/No edges -- and
    # then marks every node `process`, so the diamonds that make a flow
    # readable never render. Two runs of prompt instruction did not move it,
    # while the swimlane half of the same instruction landed immediately.
    #
    # So this is checked and retried rather than asked for again: a node whose
    # label is a question, or which has multiple labelled outgoing edges, is a
    # decision node whatever the model called it.
    if n > SOFT_MAX_NODES or e > SOFT_MAX_EDGES:
        return (f"it is too complex to read at page width ({n} nodes, {e} edges; "
                f"the limit is {SOFT_MAX_NODES} nodes and {SOFT_MAX_EDGES} edges)")
    if spec.diagram_type in _LANE_TYPES:
        by_source: dict[str, list] = {}
        for edge in spec.edges:
            by_source.setdefault(edge.source, []).append(edge)
        undeclared = []
        for node in spec.nodes:
            if node.shape == "decision":
                continue
            if _is_branch_point(node, by_source.get(node.id, [])):
                undeclared.append(node.label.strip()[:40])
        if undeclared:
            return ("branch points were not marked as decisions, so they would "
                    "render as plain rectangles instead of diamonds: "
                    + "; ".join(f'"{u}"' for u in undeclared[:4])
                    + '. Set shape="decision" on each of those nodes')
    return None
_SPEC_EVIDENCE_BUDGET = int(os.environ.get("SHILPI_SPEC_EVIDENCE_BUDGET", "1500"))
D2_PNG_WIDTH = int(os.environ.get("SHILPI_D2_PNG_WIDTH", "1800"))
DIAGRAM_RENDERER = os.environ.get("SHILPI_DIAGRAM_RENDERER", "auto")  # auto|d2|graphviz


def render_spec(
    spec: DiagramSpec,
    fmt: Literal["png", "svg"] = "png",
    timeout: float = 25.0,
    renderer: Optional[str] = None,
) -> Optional[bytes]:
    """Render a DiagramSpec to image bytes.

    Prefers D2 (real nested containers, cleaner routing) and falls back to the
    local `dot` binary. Fail-soft throughout: returns None when nothing can
    render, so proposal generation is NEVER broken by a missing renderer.
    """
    if fmt not in ("png", "svg"):
        fmt = "png"
    choice = (renderer or DIAGRAM_RENDERER or "auto").lower()

    if choice in ("auto", "d2") and d2_available():
        out = _render_with_d2(spec, fmt, timeout)
        if out:
            return out
        if choice == "d2":
            return None  # explicitly requested d2 and it failed

    if not dot_available():
        log.warning("Neither `d2` nor Graphviz `dot` found on PATH; skipping render.")
        return None
    dot_source = build_dot(spec)
    try:
        proc = subprocess.run(
            ["dot", f"-T{fmt}"],
            input=dot_source.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            check=True,
        )
        return proc.stdout
    except (subprocess.SubprocessError, OSError) as e:  # noqa: BLE001 — fail soft
        log.error("Graphviz render failed (fail-soft, skipping embed): %s", e)
        return None


# ---------------------------------------------------------------------------
# Approval state machine
# ---------------------------------------------------------------------------

class InvalidTransition(ValueError):
    """Raised when an approval-state transition is not allowed."""


def can_transition(current: str, target: str) -> bool:
    return target in _VALID_TRANSITIONS.get((current or "").strip(), set())


def validate_transition(current: str, target: str) -> None:
    """Raise InvalidTransition if current -> target is not allowed."""
    current = (current or "").strip()
    target = (target or "").strip()
    if target not in DIAGRAM_STATES:
        raise InvalidTransition(f"unknown target status '{target}'")
    if not can_transition(current, target):
        raise InvalidTransition(f"cannot move diagram from '{current}' to '{target}'")


def apply_transition(row: dict, target: str, *, rejection_comment: Optional[str] = None) -> dict:
    """Compute the DB field changes for a valid status transition.

    Returns a patch dict (only the columns that change). Does NOT touch the DB —
    the caller persists it. Enforces:
      * rejection requires a non-empty comment (appended to rejection_comments);
      * rejected -> draft bumps ``iteration`` (a re-draft attempt);
      * approval stamps ``approved``/``approved_at`` (approved_by set by caller).
    """
    current = (row.get("status") or "draft").strip()
    validate_transition(current, target)
    patch: dict = {"status": target}

    if target == "approved":
        patch["approved"] = True
    elif target == "rejected":
        if not (rejection_comment or "").strip():
            raise InvalidTransition("rejection requires a non-empty rejection comment")
        existing = list(row.get("rejection_comments") or [])
        existing.append(rejection_comment.strip())
        patch["rejection_comments"] = existing
        patch["approved"] = False
    elif target == "draft":  # re-draft after rejection
        patch["iteration"] = int(row.get("iteration") or 1) + 1
        patch["approved"] = False

    return patch


# ---------------------------------------------------------------------------
# LLM spec generation (injected structured helper — no app import)
# ---------------------------------------------------------------------------

# structured_fn signature mirrors app._structured_with_fallback:
#   async def structured_fn(response_model, messages, **kwargs) -> response_model
StructuredFn = Callable[..., Awaitable[object]]

_SPEC_SYSTEM_PROMPT = """You design architecture diagrams for InspiritVision, an IAM consulting firm.
You output a STRUCTURED diagram specification (typed nodes and edges) — never raw diagram code.

RULES:
1. Model the solution as a small set of clear nodes (systems, identity sources, IAM platform,
   target applications, users) connected by directed edges that show data/identity flow.
2. Keep it readable at page width: at most 14 nodes and 18 edges, and ONE purpose
   per diagram (a structure view OR a process flow, never both).
3. Use short, stable node ids (lowercase, alphanumeric/underscore) and concise human labels.
4. Every edge's source and target MUST reference a node id you defined.
5. Ground the diagram in the provided context; do NOT invent specific product versions,
   vendors, or integrations that are not implied by the context.
6. Choose an appropriate diagram_type from the allowed list.
7. The ENGAGEMENT FACTS are authoritative. Name products exactly as they say,
   and label each platform node with its vendor's product name. Where the
   diagram shows applications, use the client's named systems rather than generic
   boxes such as "Enterprise Apps" or "Cloud SaaS Applications".
"""


# Corrective guidance, matched to the shortfall that was actually detected.
#
# `spec_shortfall` returns a human-readable reason; this turns that reason into
# an instruction the model can act on. A generic correction is worse than none:
# it consumes the single retry without addressing the fault. Live in run 9 the
# Integration / Joiner Flow diagram was rejected for unmarked decision nodes and
# then told to add more edges, so it failed the same check twice and the diagram
# was abandoned.
_GRAPH_CORRECTION = (
    "A diagram is a GRAPH, not a list of boxes. Return the components AND the "
    "connections between them: roughly as many edges as nodes, every node "
    "connected to at least one other, and each edge labelled with the protocol, "
    "action or data that flows along it."
)

_DECISION_CORRECTION = (
    "Set shape=\"decision\" on every node that is a branch point -- any node "
    "whose label is a question, or that has more than one labelled outgoing "
    "edge. Keep the node text and the edges exactly as they are; only the "
    "`shape` field needs to change. Valid shapes are: process, decision, "
    "datastore, external, start, end, person, queue. A flow diagram in which "
    "every node is `process` is missing its decision points."
)


# Shortfalls that make a diagram genuinely unusable, as opposed to imperfect.
# An empty spec or one with no connections is not a diagram; wrong node shapes
# still communicate the flow.
# Only ONE shortfall is cosmetic: node shapes. Everything else spec_shortfall
# can report -- too few nodes, too few edges, disconnected components -- means
# the spec is not a diagram. Whitelisting the cosmetic case rather than
# blacklisting the fatal ones means a NEW check added later defaults to fatal,
# which is the safe direction: the first version of this matched on the words
# "no nodes"/"no edges" and missed the actual wordings ("it contained 2
# node(s)", "only 1 edge(s)"), so an empty spec would have been accepted.
_COSMETIC_SHORTFALL_RE = re.compile(r"branch points were not marked|too complex to read", re.I)


def _is_fatal_shortfall(shortfall: str) -> bool:
    return not _COSMETIC_SHORTFALL_RE.search(shortfall or "")


_SIMPLIFY_CORRECTION = (
    "Simplify it: keep ONE purpose for this diagram, merge minor steps and "
    "peripheral systems into a single node, and drop edges that repeat a path "
    "already shown. Stay within the limit."
)


def _correction_for(shortfall: str) -> str:
    """Turn a rejection reason into an instruction aimed at that reason."""
    lead = f"\n\nIMPORTANT: your previous answer was rejected because {shortfall}. "
    if "too complex" in shortfall.lower():
        return lead + _SIMPLIFY_CORRECTION
    if "decision" in shortfall.lower():
        return lead + _DECISION_CORRECTION
    return lead + _GRAPH_CORRECTION


async def generate_diagram_spec(
    structured_fn: StructuredFn,
    *,
    title: str,
    diagram_type: str = "architecture",
    context_text: str = "",
    client_name: str = "the client",
    iam_vendor: Optional[str] = None,
    vendor_scope_map: Optional[dict] = None,
    guidance: str = "",
    evidence_text: str = "",
    models: list[str] | None = None,
    facts: str = "",
) -> DiagramSpec:
    """Ask the LLM for a DiagramSpec via the shared structured helper, then sanitize.

    Uses the task-mandated caps: max_tokens<=1500, frequency_penalty=0.2,
    max_retries=1. The model/fallback selection lives inside ``structured_fn``
    (app._structured_with_fallback) — this module does not pick models.

    The prompt is assembled in PRIORITY ORDER with a per-part budget, because a
    single blunt ``[:4000]`` slice silently destroyed the most important part:
    callers appended per-diagram-type guidance to the end of ``context_text``,
    and the slice cut it off, so a "deployment" diagram was never actually told
    to show zones, load balancing or HA. Guidance now has its own parameter and
    is never truncated; discovery answers come next; retrieved evidence is
    trimmed last because it is the most replaceable input.

    vendor_scope_map (multi-vendor engagements only): {"Ping Identity":
    "Access Management, CIAM", "Saviynt": "IGA, PAM"}. Splitting headings and
    retrieval per vendor in the drafted TEXT is not enough on its own — the
    architecture DIAGRAM needs the same explicit signal, or the model draws
    one undifferentiated box instead of correctly attributing each
    node/zone/component to the vendor that owns it.
    """
    vendor_clause = f" using {iam_vendor}" if iam_vendor else ""
    parts = [
        f"Design a '{diagram_type}' architecture diagram titled \"{title}\" "
        f"for {client_name}{vendor_clause}."
    ]
    if vendor_scope_map and len(vendor_scope_map) > 1:
        split = "; ".join(f"{v} owns {s}" for v, s in vendor_scope_map.items())
        parts.append(
            f"\nMULTI-VENDOR ENGAGEMENT — {split}. Label every node, zone or "
            f"component with the vendor that owns it. Do not draw a single "
            f"undifferentiated block for both vendors' capabilities.")
    if guidance.strip():
        parts.append(f"\nWHAT THIS DIAGRAM MUST SHOW (follow this closely):\n{guidance.strip()}")
    if facts.strip():
        # Never truncated. ESNAD 09-24's diagrams drew Ping DS / IDM / AM for a
        # PingOne Advanced Identity Cloud (SaaS) engagement and left out every
        # named client system: the text had these rules, the diagrams did not.
        parts.append(f"\nENGAGEMENT FACTS (authoritative; the diagram must agree):\n"
                     f"{facts.strip()}")
    if context_text.strip():
        parts.append(f"\nDISCOVERY ANSWERS FOR THIS ENGAGEMENT:\n"
                     f"{context_text.strip()[:_SPEC_CONTEXT_BUDGET]}")
    if evidence_text.strip():
        parts.append(f"\nIV PAST-PROPOSAL EVIDENCE (mirror this house style):\n"
                     f"{evidence_text.strip()[:_SPEC_EVIDENCE_BUDGET]}")
    user_prompt = "\n".join(parts)
    async def _attempt(prompt: str) -> DiagramSpec:
        return await structured_fn(
            DiagramSpec,
            messages=[
                {"role": "system", "content": _SPEC_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            models=models or None,
            max_tokens=DIAGRAM_SPEC_MAX_TOKENS,
            temperature=0.2,
            frequency_penalty=0.2,
            max_retries=1,
            # "Regenerate" must produce a new diagram, not the cached one.
            extra_headers={"X-OpenRouter-Cache": "false"},
            **({"extra_body": {"reasoning": {"effort": DIAGRAM_REASONING_EFFORT}}}
               if DIAGRAM_REASONING_EFFORT else {}),
        )

    spec: DiagramSpec = await _attempt(user_prompt)

    # A schema-valid spec can still be worthless: empty, or nodes with no edges.
    # Both happened live. Correct it once, explicitly, before giving up — these
    # are semantic failures, not malformed JSON, so instructor never retries them.
    shortfall = spec_shortfall(spec)
    if shortfall:
        log.warning("diagram spec unusable (%s) — retrying with an explicit correction",
                    shortfall)
        # The correction must address the ACTUAL shortfall -- see _correction_for.
        spec = await _attempt(user_prompt + _correction_for(shortfall))
        shortfall = spec_shortfall(spec)
    if shortfall:
        if _is_fatal_shortfall(shortfall):
            raise ValueError(f"model returned an unusable diagram spec — {shortfall}")
        # COSMETIC shortfall after the retry: keep the diagram.
        #
        # Runs 9 and 12 both LOST the Integration / Joiner Flow entirely because
        # some node shapes were wrong. A rectangle where a diamond belongs is a
        # cosmetic flaw; no diagram at all is a missing page, and it is the one
        # diagram with a direct counterpart in IV's own proposal. Rejecting a
        # correct, complete flow over shape metadata is the wrong trade.
        log.warning("diagram spec accepted with a cosmetic shortfall (%s); "
                    "keeping the diagram rather than losing it", shortfall)

    # Preserve caller intent for title/type, then sanitize/cap everything.
    spec.title = title or spec.title
    if diagram_type in DIAGRAM_TYPES:
        spec.diagram_type = diagram_type
    return sanitize_spec(spec)


# ---------------------------------------------------------------------------
# Spec-template reuse (best-effort MVP — interface only, storage deferred)
# ---------------------------------------------------------------------------
# Reusable DiagramSpec templates are keyed by (vendor, diagram_type). Approved
# diagrams promote to templates. No dedicated table exists yet (the migration
# adds no diagram_templates table and Pass 4 must not add migrations unless
# absolutely required), so persistence is DEFERRED. The pure helpers below define
# the interface and let approved specs be reused within a process; wiring them to
# a durable store is a follow-up.

def template_key(iam_vendor: Optional[str], diagram_type: str) -> str:
    vendor = (iam_vendor or "generic").strip().lower() or "generic"
    dtype = diagram_type if diagram_type in DIAGRAM_TYPES else "architecture"
    return f"{vendor}:{dtype}"


# Process-local template cache (NOT durable — see note above).
_TEMPLATE_CACHE: dict[str, dict] = {}


def promote_to_template(spec: DiagramSpec, iam_vendor: Optional[str]) -> str:
    """Promote an approved spec to a reusable template. Returns the template key.

    DEFERRED: currently an in-process cache only. Swap the body for a durable
    store (e.g. a diagram_templates table) without changing this signature.
    """
    key = template_key(iam_vendor, spec.diagram_type)
    _TEMPLATE_CACHE[key] = sanitize_spec(spec).model_dump()
    return key


def get_template_spec(iam_vendor: Optional[str], diagram_type: str) -> Optional[DiagramSpec]:
    """Fetch a reusable template spec by (vendor, diagram_type), if any."""
    data = _TEMPLATE_CACHE.get(template_key(iam_vendor, diagram_type))
    return DiagramSpec.model_validate(data) if data else None


# ---------------------------------------------------------------------------
# Deterministic solution stack (no model call)
# ---------------------------------------------------------------------------
# IV's most useful picture is a one-page stack: users, each platform as a block
# of its capability areas, the client's named systems. Everything on it is a
# FACT from the answers, so it is built here rather than drafted: the model-
# drawn equivalent in ESNAD 09-24 named the wrong products and none of the nine
# client systems. Accurate and identical on every run.

_SAAS_PLATFORM = {"ping": "PingOne Advanced Identity Cloud",
                  "saviynt": "Saviynt Enterprise Identity Cloud",
                  "sailpoint": "SailPoint Identity Security Cloud"}

# (pattern on the vendor's scope, node label). Order is display order.
_DOMAINS = (
    ("wiam", r"access management|\bam\b|wiam|workforce|\bsso\b",
     "Workforce access: SSO, adaptive MFA, federation"),
    ("ciam", r"ciam|customer", "Customer identity (CIAM): registration, login, consent"),
    ("iga", r"\biga\b|governance|lifecycle",
     "Identity governance (IGA): joiner/mover/leaver, access reviews, SoD"),
    ("pam", r"\bpam\b|privileged",
     "Privileged access (PAM): vaulting, just-in-time access, session recording"),
)


def _vendor_scopes(iam_vendor: str, vendor_scope_map: Optional[dict]) -> list[tuple[str, str]]:
    """[(vendor, scope)] from the scope map, else from 'X for A, Y for B'."""
    if vendor_scope_map and len(vendor_scope_map) > 1:
        return [(str(v), str(s)) for v, s in vendor_scope_map.items()]
    out = []
    for part in re.split(r",|;|\band\b(?=\s+[A-Z][a-z]+\s+for\b)", iam_vendor or ""):
        m = re.match(r"\s*(.+?)\s+for\s+(.+?)\s*$", part)
        if m:
            out.append((m.group(1), m.group(2)))
    return out or ([(iam_vendor.strip(), iam_vendor)] if (iam_vendor or "").strip() else [])


def _count(text: str, pattern: str) -> Optional[str]:
    m = re.search(rf"(?:{pattern})[^:\n]*:\s*([\d,]+)", text or "", re.I)
    return f"{int(m.group(1).replace(',', '')):,}" if m else None


def _system_names(target_integrations: str) -> list[str]:
    """System names from the target_integrations answer. ESNAD's 09-25
    extraction separated them with commas, some inside parentheses ("GIS (Esri
    Geoportal, Admin, Maps)"), and the stack drew one truncated box."""
    text = target_integrations or ""
    described = bool(re.search(r";|\n", text))  # "Name (what it does); ..."
    sep = r";|\n" if described else r",(?![^()]*\))"
    names = []
    for item in re.split(sep, text):
        item = item.strip(" .-")
        if not item or item.lower() in ("skip", "none", "n/a"):
            continue
        # Only the described format carries a trailing "(description)"; in a
        # plain comma list a parenthetical is part of the name.
        m = re.match(r"^(.*?)\s*\([^()]*\)\s*$", item) if described else None
        names.append(" ".join((m.group(1) if m and m.group(1) else item).split()))
    return names


def build_stack_spec(*, title: str, client_name: str, iam_vendor: str,
                     vendor_scope_map: Optional[dict] = None, is_saas: bool = False,
                     population: str = "", target_integrations: str = "",
                     context: str = "") -> DiagramSpec:
    """The solution stack as a DiagramSpec, from answers alone."""
    nodes: list[DiagramNode] = []
    edges: list[DiagramEdge] = []
    domain_node: dict[str, str] = {}

    for vendor, scope in _vendor_scopes(iam_vendor, vendor_scope_map):
        key = next((k for k in _SAAS_PLATFORM if k in vendor.lower()), None)
        group = (f"{vendor}: {_SAAS_PLATFORM[key]} (SaaS)" if is_saas and key
                 else vendor)
        for dom, pat, label in _DOMAINS:
            if dom not in domain_node and re.search(pat, scope, re.I):
                nid = f"{dom}_{len(nodes)}"
                domain_node[dom] = nid
                nodes.append(DiagramNode(id=nid, label=label, group=group))

    users = [("wiam", r"wiam|workforce", "Workforce users"),
             ("ciam", r"ciam users|customer", "Customer users"),
             ("pam", r"pam|privileged", "Privileged administrators")]
    for dom, pat, label in users:
        if dom not in domain_node:
            continue
        n = _count(population, pat)
        suffix = f" ({n} accounts)" if n and dom == "pam" else f" ({n})" if n else ""
        uid = f"users_{dom}"
        nodes.append(DiagramNode(id=uid, label=label + suffix, group="Users", shape="person"))
        edges.append(DiagramEdge(source=uid, target=domain_node[dom],
                                 label="privileged sign-in" if dom == "pam" else "sign-in"))

    # "Saudi Mining Services Company (ESNAD)" -> "ESNAD": the long name
    # overflowed its column title.
    short = re.search(r"\(([A-Z][A-Za-z0-9&]{1,15})\)\s*$", client_name or "")
    apps_group = f"{short.group(1) if short else client_name} applications"
    sources = "Identity sources and operations"
    systems = _system_names(target_integrations)
    nafath = next((s for s in systems if "nafath" in s.lower()), None)
    apps = [s for s in systems if s != nafath]
    if len(apps) > 10:
        apps = apps[:9] + [f"Other systems ({len(apps) - 9})"]
    for i, name in enumerate(apps):
        nodes.append(DiagramNode(id=f"app_{i}", label=name, group=apps_group))

    blob = " ".join([target_integrations or "", context or ""])
    if re.search(r"(?i:active directory|\bldap\b)|\bAD\b", blob):
        nodes.append(DiagramNode(id="src_ad", label="Active Directory / LDAP",
                                 group=sources, shape="datastore"))
    if re.search(r"\bHR(?:MS)?\b|\bERP\b|(?i:human resources)", blob):
        nodes.append(DiagramNode(id="src_hr", label="HR / ERP (authoritative source)",
                                 group=sources, shape="datastore"))
    if nafath:
        nodes.append(DiagramNode(id="src_nafath", label=f"{nafath} (national login)",
                                 group=sources, shape="external"))
    if re.search(r"\bSIEM\b", blob):
        nodes.append(DiagramNode(id="src_siem", label="SIEM (audit events)",
                                 group=sources))
    ids = {n.id for n in nodes}
    apps_ref = f"{GROUP_REF}{apps_group}" if apps else None

    def edge(src, tgt, label):
        if src and tgt and (src in ids or src.startswith(GROUP_REF)) and \
                (tgt in ids or tgt.startswith(GROUP_REF)):
            edges.append(DiagramEdge(source=src, target=tgt, label=label))

    for dom in ("wiam", "ciam"):
        edge(domain_node.get(dom), apps_ref, "SSO (SAML / OIDC)")
    edge(domain_node.get("iga"), apps_ref, "provisioning")
    edge(domain_node.get("pam"), apps_ref, "vaulted privileged sessions")
    edge("src_hr", domain_node.get("iga"), "joiner/mover/leaver feed")
    edge(domain_node.get("iga"), "src_ad", "accounts and groups")
    edge(domain_node.get("iga"), domain_node.get("pam"), "governed privileged access")
    edge("src_nafath", domain_node.get("ciam") or domain_node.get("wiam"), "federation")
    for dom in ("iga", "wiam"):
        edge(domain_node.get(dom), "src_siem", "audit events")
    return sanitize_spec(DiagramSpec(diagram_type="stack", title=title,
                                     nodes=nodes, edges=edges))
