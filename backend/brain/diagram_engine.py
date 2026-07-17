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
import subprocess
from typing import Awaitable, Callable, Literal, Optional

from pydantic import BaseModel, Field, field_validator

log = logging.getLogger("sarvam-brain.diagram-engine")

# --- hard caps (anti-runaway / anti-injection) ------------------------------
MAX_NODES = 40
MAX_EDGES = 80
MAX_LABEL_LEN = 80
MAX_TITLE_LEN = 120
MAX_ID_LEN = 64

# Per-call LLM budget for diagram-spec generation (task-mandated cap).
DIAGRAM_SPEC_MAX_TOKENS = 1500

# Allowlisted diagram types. Anything else is coerced to "architecture".
DIAGRAM_TYPES = ("architecture", "flow", "sequence", "network", "data_flow", "component")

# Graphviz rankdir per diagram type.
_RANKDIR = {
    "architecture": "TB",
    "component": "TB",
    "flow": "TB",
    "data_flow": "LR",
    "sequence": "LR",
    "network": "LR",
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

class DiagramNode(BaseModel):
    id: str = Field(..., description="Stable node identifier, e.g. 'idp' or 'hr_source'. Short, alphanumeric.")
    label: str = Field(..., description="Human-readable node label shown in the diagram.")
    group: Optional[str] = Field(
        None, description="Optional logical grouping/zone, e.g. 'On-prem', 'Cloud', 'Client'."
    )


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
            )
        )

    valid_ids = {n.id for n in safe_nodes}
    safe_edges: list[DiagramEdge] = []
    for edge in spec.edges[:MAX_EDGES]:
        src = id_map.get(edge.source, _safe_node_id(edge.source, -1))
        tgt = id_map.get(edge.target, _safe_node_id(edge.target, -1))
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
        "digraph sarvam_diagram {",
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

    for e in spec.edges:
        attr = f' [label="{_escape_label(e.label)}"]' if e.label else ""
        lines.append(f'  "{e.source}" -> "{e.target}"{attr};')

    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic Graphviz render (fail-soft)
# ---------------------------------------------------------------------------

def dot_available() -> bool:
    """True iff the local Graphviz `dot` binary is on PATH."""
    return shutil.which("dot") is not None


def render_spec(
    spec: DiagramSpec,
    fmt: Literal["png", "svg"] = "png",
    timeout: float = 15.0,
) -> Optional[bytes]:
    """Render a DiagramSpec to image bytes via the local `dot` binary.

    Fail-soft: returns None (and logs) when `dot` is unavailable or the render
    fails, so proposal generation is NEVER broken by a missing/broken renderer.
    """
    if fmt not in ("png", "svg"):
        fmt = "png"
    if not dot_available():
        log.warning("Graphviz `dot` not found on PATH; skipping diagram render (fail-soft).")
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
2. Keep it readable: aim for 5-15 nodes. Never exceed the schema caps.
3. Use short, stable node ids (lowercase, alphanumeric/underscore) and concise human labels.
4. Every edge's source and target MUST reference a node id you defined.
5. Ground the diagram in the provided context; do NOT invent specific product versions,
   vendors, or integrations that are not implied by the context.
6. Choose an appropriate diagram_type from the allowed list.
"""


async def generate_diagram_spec(
    structured_fn: StructuredFn,
    *,
    title: str,
    diagram_type: str = "architecture",
    context_text: str = "",
    client_name: str = "the client",
    iam_vendor: Optional[str] = None,
) -> DiagramSpec:
    """Ask the LLM for a DiagramSpec via the shared structured helper, then sanitize.

    Uses the task-mandated caps: max_tokens<=1500, frequency_penalty=0.2,
    max_retries=1. The model/fallback selection lives inside ``structured_fn``
    (app._structured_with_fallback) — this module does not pick models.
    """
    vendor_clause = f" using {iam_vendor}" if iam_vendor else ""
    user_prompt = (
        f"Design a '{diagram_type}' architecture diagram titled \"{title}\" for {client_name}"
        f"{vendor_clause}.\n\nCONTEXT:\n{(context_text or '').strip()[:4000]}"
    )
    spec: DiagramSpec = await structured_fn(
        DiagramSpec,
        messages=[
            {"role": "system", "content": _SPEC_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=DIAGRAM_SPEC_MAX_TOKENS,
        frequency_penalty=0.2,
        max_retries=1,
    )
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
