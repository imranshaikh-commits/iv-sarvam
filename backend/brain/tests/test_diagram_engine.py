"""
Sprint 5 Pass 4 — architecture diagram framework tests (keyless, no network).

Covers:
  * DiagramSpec validation + sanitization (caps, escaping, dangling-edge drop,
    id rewriting, diagram_type coercion) and deterministic DOT building.
  * Render path: skips fail-soft when `dot` is unavailable; otherwise renders.
  * Approval state machine: valid/invalid transitions, rejection comment
    requirement, iteration bump on re-draft.
  * DOCX embedding: approved diagrams embed; draft/rejected/needs_review do NOT.
  * generate_proposal default path still works with ZERO diagrams (unchanged).
  * LLM spec generation uses the INJECTED structured helper (mocked) with the
    mandated caps — no live OpenRouter call.

No live Supabase / OpenRouter calls anywhere.
"""

import asyncio
import base64
import io
import os
import re
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BRAIN = os.path.dirname(_HERE)
if _BRAIN not in sys.path:
    sys.path.insert(0, _BRAIN)

import httpx
from docx import Document

import diagram_engine as de
import document_engine
from diagram_engine import DiagramEdge, DiagramNode, DiagramSpec, InvalidTransition
from document_engine import assemble_docx, generate_proposal

# A minimal valid 1x1 PNG so add_picture works without the `dot` binary or PIL.
_ONE_PX_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


# --- schema / sanitization --------------------------------------------------

def test_sanitize_caps_node_and_edge_counts():
    nodes = [DiagramNode(id=f"n{i}", label=f"Node {i}") for i in range(de.MAX_NODES + 15)]
    edges = [DiagramEdge(source=f"n{i}", target=f"n{i+1}") for i in range(de.MAX_EDGES + 30)]
    spec = DiagramSpec(title="Big", nodes=nodes, edges=edges)
    safe = de.sanitize_spec(spec)
    assert len(safe.nodes) <= de.MAX_NODES
    assert len(safe.edges) <= de.MAX_EDGES


def test_sanitize_clips_label_length():
    long_label = "x" * 500
    spec = DiagramSpec(title="t", nodes=[DiagramNode(id="a", label=long_label)], edges=[])
    safe = de.sanitize_spec(spec)
    assert len(safe.nodes[0].label) <= de.MAX_LABEL_LEN


def test_sanitize_rewrites_unsafe_ids_and_keeps_edges():
    spec = DiagramSpec(
        title="t",
        nodes=[DiagramNode(id="hr!! source", label="HR"), DiagramNode(id="idp", label="IdP")],
        edges=[DiagramEdge(source="hr!! source", target="idp", label="SCIM")],
    )
    safe = de.sanitize_spec(spec)
    ids = {n.id for n in safe.nodes}
    assert "hrsource" in ids  # non-alnum stripped
    assert len(safe.edges) == 1
    assert safe.edges[0].source in ids and safe.edges[0].target in ids


def test_sanitize_drops_dangling_edges():
    spec = DiagramSpec(
        title="t",
        nodes=[DiagramNode(id="a", label="A")],
        edges=[DiagramEdge(source="a", target="ghost")],
    )
    safe = de.sanitize_spec(spec)
    assert safe.edges == []


def test_diagram_type_coerced_to_allowlist():
    spec = DiagramSpec(diagram_type="totally-made-up", title="t", nodes=[], edges=[])
    assert spec.diagram_type == "architecture"
    ok = DiagramSpec(diagram_type="data_flow", title="t", nodes=[], edges=[])
    assert ok.diagram_type == "data_flow"


def test_build_dot_escapes_quotes_no_injection():
    # A label attempting to break out of the quoted string / inject attributes.
    evil = 'X" ]; node[color=red]; a[label="pwned'
    spec = DiagramSpec(
        title='T"itle',
        nodes=[DiagramNode(id="a", label=evil)],
        edges=[],
    )
    dot = de.build_dot(spec)
    # The raw payload (with its UNescaped quotes) must not survive verbatim — the
    # quotes are escaped, so the label can never break out to inject attributes.
    assert evil not in dot
    assert de._escape_label('a"b') == 'a\\"b'
    assert '\\"' in dot  # escaped quotes present
    # Only ONE real node attribute list exists (no injected extra node): count
    # node-definition lines, which start with an indented quoted id.
    node_defs = [ln for ln in dot.splitlines() if ln.strip().startswith('"a"')]
    assert len(node_defs) == 1
    assert dot.startswith("digraph shilpi_diagram {")
    assert dot.strip().endswith("}")


def test_build_dot_groups_into_clusters():
    spec = DiagramSpec(
        title="t",
        nodes=[
            DiagramNode(id="a", label="A", group="Zone1"),
            DiagramNode(id="b", label="B", group="Zone1"),
            DiagramNode(id="c", label="C"),
        ],
        edges=[DiagramEdge(source="a", target="b")],
    )
    dot = de.build_dot(spec)
    assert "subgraph cluster_0" in dot
    assert 'label="Zone1"' in dot


# --- render path ------------------------------------------------------------

def test_render_fail_soft_or_png(monkeypatch):
    spec = DiagramSpec(title="t", nodes=[DiagramNode(id="a", label="A")], edges=[])
    if de.dot_available() or (de.d2_available() and de.shutil.which("rsvg-convert")):
        out = de.render_spec(spec, fmt="png")
        assert out and out[:8] == b"\x89PNG\r\n\x1a\n"
    else:
        assert de.render_spec(spec, fmt="png") is None


def test_render_returns_none_when_dot_missing(monkeypatch):
    monkeypatch.setattr(de.shutil, "which", lambda _: None)
    spec = DiagramSpec(title="t", nodes=[DiagramNode(id="a", label="A")], edges=[])
    assert de.render_spec(spec, fmt="png") is None


# --- state machine ----------------------------------------------------------

def test_valid_transitions():
    assert de.apply_transition({"status": "draft"}, "needs_review")["status"] == "needs_review"
    approved = de.apply_transition({"status": "needs_review"}, "approved")
    assert approved["status"] == "approved" and approved["approved"] is True


def test_reject_requires_comment_and_appends():
    with pytest.raises(InvalidTransition):
        de.apply_transition({"status": "needs_review"}, "rejected")
    patch = de.apply_transition(
        {"status": "needs_review", "rejection_comments": ["old"]},
        "rejected",
        rejection_comment="needs a cloud zone",
    )
    assert patch["status"] == "rejected"
    assert patch["rejection_comments"] == ["old", "needs a cloud zone"]


def test_redraft_bumps_iteration():
    patch = de.apply_transition({"status": "rejected", "iteration": 2}, "draft")
    assert patch["status"] == "draft" and patch["iteration"] == 3


def test_invalid_transitions_rejected():
    for current, target in [
        ("draft", "approved"),
        ("draft", "rejected"),
        ("approved", "draft"),
        ("approved", "needs_review"),
        ("needs_review", "draft"),
        ("rejected", "approved"),
    ]:
        with pytest.raises(InvalidTransition):
            de.apply_transition({"status": current}, target, rejection_comment="c")


def test_unknown_target_rejected():
    with pytest.raises(InvalidTransition):
        de.apply_transition({"status": "draft"}, "banana")


# --- LLM spec generation (injected structured helper, mocked) --------------

def test_generate_diagram_spec_uses_injected_helper_with_caps():
    captured = {}

    async def fake_structured(response_model, messages, **kwargs):
        captured["model"] = response_model
        captured["kwargs"] = kwargs
        return DiagramSpec(
            diagram_type="architecture",
            title="ignored — caller title wins",
            nodes=[DiagramNode(id="idp", label="IdP"), DiagramNode(id="app", label="App")],
            edges=[DiagramEdge(source="idp", target="app", label="SAML")],
        )

    spec = asyncio.run(
        de.generate_diagram_spec(
            fake_structured,
            title="My Architecture",
            diagram_type="architecture",
            context_text="SailPoint IGA rollout",
            client_name="Meridian",
            iam_vendor="SailPoint",
        )
    )
    assert spec.title == "My Architecture"  # caller title preserved
    assert captured["model"] is DiagramSpec
    kw = captured["kwargs"]
    assert kw["max_tokens"] == de.DIAGRAM_SPEC_MAX_TOKENS
    assert kw.get("extra_body", {}).get("reasoning") == {"effort": de.DIAGRAM_REASONING_EFFORT}
    assert kw["frequency_penalty"] == 0.2
    assert kw["max_retries"] == 1
    # returned spec is sanitized
    assert len(spec.nodes) == 2 and len(spec.edges) == 1


# --- DOCX embedding (approval gate) ----------------------------------------

def _extract_images(docx_bytes: bytes) -> int:
    doc = Document(io.BytesIO(docx_bytes))
    return sum(1 for rel in doc.part.rels.values() if "image" in rel.reltype)


def _extract_text(docx_bytes: bytes) -> str:
    doc = Document(io.BytesIO(docx_bytes))
    return "\n".join(p.text for p in doc.paragraphs)


def _base_sections():
    return [{"id": "executive_summary", "title": "Executive Summary",
             "content": "Body [1].", "citations": [], "max_similarity": 0.6,
             "needs_sme_review": False}]


def test_only_approved_diagrams_embed():
    diagrams = [
        {"title": "Approved Arch", "status": "approved", "image_bytes": _ONE_PX_PNG},
        {"title": "Draft Arch", "status": "draft", "image_bytes": _ONE_PX_PNG},
        {"title": "Rejected Arch", "status": "rejected", "image_bytes": _ONE_PX_PNG},
        {"title": "Review Arch", "status": "needs_review", "image_bytes": _ONE_PX_PNG},
    ]
    docx_bytes = assemble_docx(
        {"client_name": "Meridian", "proposal_type": "implementation"},
        _base_sections(),
        diagrams=diagrams,
    )
    text = _extract_text(docx_bytes)
    assert "Solution Architecture Diagrams" in text
    assert "Approved Arch" in text
    assert "Draft Arch" not in text
    assert "Rejected Arch" not in text
    assert "Review Arch" not in text
    # Exactly one diagram image embedded (the approved one). Branding/logo images
    # may also exist, so assert the approved image increased the count vs none.
    assert _extract_images(docx_bytes) >= 1


def test_no_diagrams_section_when_none_approved():
    diagrams = [
        {"title": "Draft Arch", "status": "draft", "image_bytes": _ONE_PX_PNG},
        {"title": "Rejected Arch", "status": "rejected", "image_bytes": _ONE_PX_PNG},
    ]
    docx_bytes = assemble_docx(
        {"client_name": "Meridian", "proposal_type": "implementation"},
        _base_sections(),
        diagrams=diagrams,
    )
    assert "Solution Architecture Diagrams" not in _extract_text(docx_bytes)


def test_approved_without_image_is_skipped():
    diagrams = [{"title": "Approved No Image", "status": "approved"}]
    docx_bytes = assemble_docx(
        {"client_name": "Meridian", "proposal_type": "implementation"},
        _base_sections(),
        diagrams=diagrams,
    )
    assert "Solution Architecture Diagrams" not in _extract_text(docx_bytes)


# --- generate_proposal default path unchanged (zero diagrams) --------------

async def _stub_embed(client, text):
    return [0.01] * 1536


async def _stub_retrieve(client, embedding, query, k=8):
    return [{"chunk_text": "SailPoint IIQ provisioning across 42 apps.",
             "heading": "Provisioning", "similarity": 0.62,
             "client_name": "Northwind", "iam_vendor": "sailpoint"}]


def _stub_build_system(chunks):
    return "=== EVIDENCE ===\n" + "\n".join(f"[{i}] {c['chunk_text']}" for i, c in enumerate(chunks, 1))


async def _stub_draft(client, system_prompt, user_prompt, max_tokens=1500):
    return "Grounded prose [1]."


def test_generate_proposal_zero_diagrams_default_path():
    document_engine.draft_with_openrouter = _stub_draft

    async def _run():
        async with httpx.AsyncClient() as client:
            return await generate_proposal(
                client,
                rfp_text="IAM implementation.",
                client_name="Meridian Bank",
                proposal_type="implementation",
                iam_vendor="SailPoint",
                embed_fn=_stub_embed,
                retrieve_fn=_stub_retrieve,
                build_grounded_system_fn=_stub_build_system,
                sections=None,
                include_compliance_matrix=False,
            )

    result = asyncio.run(_run())
    assert result["docx_bytes"]
    text = _extract_text(result["docx_bytes"])
    assert "Executive Summary" in text
    # No diagrams passed -> no diagram section.
    assert "Solution Architecture Diagrams" not in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# --- prompt assembly regression ---------------------------------------------
def test_guidance_survives_prompt_truncation():
    """REGRESSION: guidance used to be appended to the END of context_text, which
    a blunt [:4000] slice cut off entirely — so a 'deployment' diagram was never
    actually told to show zones, load balancing or HA."""
    import asyncio
    captured = {}

    async def fake_structured(model, messages=None, **kw):
        captured["prompt"] = messages[-1]["content"]
        return DiagramSpec.model_validate({
            "diagram_type": "architecture", "title": "t",
            "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"},
                      {"id": "c", "label": "C"}],
            "edges": [{"source": "a", "target": "b"}]})

    asyncio.run(de.generate_diagram_spec(
        fake_structured,
        title="AWS - Deployment",
        diagram_type="architecture",
        context_text="network_zones: DMZ, application, data\n" + ("filler\n" * 2000),
        client_name="AWS",
        iam_vendor="PingIdentity",
        guidance="Every node must sit in a named zone. Show regions and HA.",
        evidence_text="evidence " * 2000,
    ))
    prompt = captured["prompt"]
    assert "named zone" in prompt, "guidance was truncated away"
    assert prompt.index("named zone") < prompt.index("network_zones"), \
        "guidance must precede the answers so it always survives"
    assert len(prompt) < 12000, f"prompt unexpectedly large: {len(prompt)}"


def test_evidence_is_trimmed_before_answers():
    """Evidence is the most replaceable input, so it gets the smallest budget."""
    import asyncio
    captured = {}

    async def fake_structured(model, messages=None, **kw):
        captured["prompt"] = messages[-1]["content"]
        return DiagramSpec.model_validate({
            "diagram_type": "architecture", "title": "t",
            "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"},
                      {"id": "c", "label": "C"}],
            "edges": [{"source": "a", "target": "b"}]})

    asyncio.run(de.generate_diagram_spec(
        fake_structured, title="x", context_text="ANSWER_MARKER\n" + ("a\n" * 100),
        guidance="g", evidence_text="EVIDENCE_MARKER " * 5000))
    prompt = captured["prompt"]
    assert "ANSWER_MARKER" in prompt
    assert prompt.count("EVIDENCE_MARKER") < 5000


# --- empty-spec guard -------------------------------------------------------
def test_empty_spec_triggers_a_corrective_retry():
    """REGRESSION: nodes defaults to [], so an EMPTY spec is schema-valid. The
    model returned exactly that live and we rendered a diagram with only a title."""
    import asyncio
    calls = {"n": 0}

    async def empty_then_good(model, messages=None, models=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return DiagramSpec.model_validate({
                "diagram_type": "architecture", "title": "t", "nodes": [], "edges": []})
        return DiagramSpec.model_validate({
            "diagram_type": "architecture", "title": "t",
            "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"},
                      {"id": "c", "label": "C"}],
            "edges": [{"source": "a", "target": "b"}]})

    spec = asyncio.run(de.generate_diagram_spec(empty_then_good, title="X"))
    assert calls["n"] == 2, "did not retry an empty spec"
    assert len(spec.nodes) >= de.MIN_SPEC_NODES


def test_persistently_empty_spec_raises_rather_than_rendering_nothing():
    import asyncio

    async def always_empty(model, messages=None, models=None, **kw):
        return DiagramSpec.model_validate({
            "diagram_type": "architecture", "title": "t", "nodes": [], "edges": []})

    try:
        asyncio.run(de.generate_diagram_spec(always_empty, title="X"))
    except ValueError as e:
        assert "unusable" in str(e)
    else:
        raise AssertionError("an empty spec must raise, not render an empty diagram")


def test_correction_prompt_states_the_requirement():
    import asyncio
    prompts = []

    async def capture(model, messages=None, models=None, **kw):
        prompts.append(messages[-1]["content"])
        return DiagramSpec.model_validate({
            "diagram_type": "architecture", "title": "t", "nodes": [], "edges": []})

    try:
        asyncio.run(de.generate_diagram_spec(capture, title="X"))
    except ValueError:
        pass
    assert len(prompts) == 2
    low = prompts[1].lower()
    assert "rejected" in low and "nodes" in low and "edges" in low


# --- edges are mandatory ----------------------------------------------------
def _spec(nodes, edges):
    return DiagramSpec.model_validate(
        {"diagram_type": "architecture", "title": "t", "nodes": nodes, "edges": edges})


def test_the_live_edgeless_failure_is_rejected():
    """REGRESSION: a live spec had 12 nodes and 1 edge and rendered as a grouped
    list. Node count alone was checked, so it passed validation."""
    nodes = [{"id": f"n{i}", "label": f"N{i}"} for i in range(12)]
    shortfall = de.spec_shortfall(_spec(nodes, [{"source": "n0", "target": "n1"}]))
    assert shortfall and "edge" in shortfall


def test_healthy_graph_passes():
    nodes = [{"id": f"n{i}", "label": f"N{i}"} for i in range(12)]
    edges = [{"source": f"n{i}", "target": f"n{i+1}"} for i in range(11)]
    assert de.spec_shortfall(_spec(nodes, edges)) is None


def test_minimal_two_node_diagram_still_allowed():
    assert de.spec_shortfall(_spec(
        [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        [{"source": "a", "target": "b"}])) is None


def test_too_many_orphans_rejected():
    nodes = [{"id": f"n{i}", "label": f"N{i}"} for i in range(9)]
    # Enough edges to pass the count, but most nodes still floating.
    edges = [{"source": "n0", "target": "n1"}, {"source": "n1", "target": "n0"},
             {"source": "n0", "target": "n1"}, {"source": "n1", "target": "n0"},
             {"source": "n0", "target": "n1"}]
    shortfall = de.spec_shortfall(_spec(nodes, edges))
    assert shortfall and "no connections" in shortfall


def test_edgeless_spec_triggers_correction_then_raises():
    import asyncio
    prompts = []

    async def edgeless(model, messages=None, models=None, **kw):
        prompts.append(messages[-1]["content"])
        return _spec([{"id": f"n{i}", "label": f"N{i}"} for i in range(8)],
                     [{"source": "n0", "target": "n1"}])

    try:
        asyncio.run(de.generate_diagram_spec(edgeless, title="X"))
    except ValueError as e:
        assert "unusable" in str(e)
    else:
        raise AssertionError("an edgeless spec must not be accepted")
    assert len(prompts) == 2, "no corrective retry"
    assert "GRAPH" in prompts[1] and "edges" in prompts[1].lower()


def test_group_labels_are_humanised():
    for raw, want in (("identity_source_layer", "Identity Source"),
                      ("iam_platform_layer", "IAM Platform"),
                      ("_monitoring_layer", "Monitoring"),
                      ("dmz_zone", "DMZ Zone"),
                      ("Identity Sources", "Identity Sources")):
        assert de._pretty_group(raw) == want, f"{raw} -> {de._pretty_group(raw)}"


import diagram_engine  # noqa: E402

# ---------------------------------------------------------------------------
# Swimlanes, decision nodes and page fit.
#
# IV's joiner-flow diagram is four horizontal lanes (HRMS / SailPoint IIQ /
# Manager / Active Directory) with a decision diamond branching Employee vs
# Contractor. Shilpi drew every node as a rectangle in a single column, and
# embedded diagrams at 1800x4217 (2.34:1) that scaled to 3.2in wide slivers.
# ---------------------------------------------------------------------------

def _flow_spec():
    return diagram_engine.DiagramSpec(
        diagram_type="flow", title="Joiner Flow",
        nodes=[
            diagram_engine.DiagramNode(id="hr", label="HRMS", group="HRMS", shape="external"),
            diagram_engine.DiagramNode(id="agg", label="Aggregation Task", group="SailPoint IIQ"),
            diagram_engine.DiagramNode(id="q", label="Employee?", group="SailPoint IIQ", shape="decision"),
            diagram_engine.DiagramNode(id="ap", label="Manager Approval", group="Manager"),
            diagram_engine.DiagramNode(id="ad", label="Active Directory", group="Active Directory", shape="datastore"),
        ],
        edges=[
            diagram_engine.DiagramEdge(source="hr", target="agg"),
            diagram_engine.DiagramEdge(source="agg", target="q"),
            diagram_engine.DiagramEdge(source="q", target="ad", label="YES"),
            diagram_engine.DiagramEdge(source="q", target="ap", label="CONTRACTOR"),
        ],
    )


def test_decision_node_renders_as_a_diamond():
    d2 = diagram_engine.build_d2(_flow_spec())
    assert "shape: diamond" in d2, "decision node did not become a diamond"
    assert "shape: cylinder" in d2, "datastore did not become a cylinder"
    assert "shape: package" in d2, "external system did not become a package"


def test_unknown_shape_falls_back_to_process():
    n = diagram_engine.DiagramNode(id="a", label="X", shape="banana")
    assert n.shape == diagram_engine.DEFAULT_NODE_SHAPE


def test_flow_groups_become_swimlanes():
    """Each lane runs across the page; lanes stack down."""
    d2 = diagram_engine.build_d2(_flow_spec())
    assert d2.count("direction: right") >= 4, "lanes did not get their own direction"
    assert d2.startswith("direction: down"), d2[:40]


def test_architecture_groups_are_zones_not_lanes():
    spec = diagram_engine.DiagramSpec(
        diagram_type="architecture", title="Deployment",
        nodes=[diagram_engine.DiagramNode(id="a", label="Node", group="DMZ")],
        edges=[],
    )
    # Zones carry no lane direction of their own; only the root sets one
    # (left to right for structural views since ESNAD 09-24).
    d2 = diagram_engine.build_d2(spec)
    assert d2.count("direction:") == 1 and d2.startswith("direction: right"), d2[:60]


def test_aspect_penalty_scores_a_band_not_a_ceiling():
    """A flip that overshoots must score WORSE, not better.

    An 8-zone chain went from 3.49 (sliver) to 0.09 (an 11:1 strip). Both are
    unusable, so 'smaller wins' would have picked the worse one.
    """
    p = diagram_engine._aspect_penalty
    assert p(1.0) == 0.0
    assert p(3.49) > 0
    assert p(0.09) > p(3.49), "over-wide scored better than over-tall"
    assert p(None) == float("inf")


def test_guidance_actually_asks_for_lanes_and_shapes():
    """CALL-SITE check: the spec fields are useless if nothing requests them."""
    import chat_state
    flow = chat_state.deployment_guidance_for("Integration / Joiner Flow", "flow")
    assert "SWIMLANE" in flow
    assert "decision" in flow
    arch = chat_state.deployment_guidance_for("Deployment", "architecture")
    assert "decision" in arch, "shape mandate missing from non-flow diagrams"


# ---------------------------------------------------------------------------
# Decision nodes, enforced by VALIDATION rather than asked for again.
#
# The model reliably writes branch logic into flow diagrams -- run 6 produced
# "Identity Already Exists?" and "Manager Approval Required?" with correctly
# labelled Yes/No edges -- and then marks every node `process`, so the diamonds
# that make a flow readable never render. Two rounds of prompt instruction did
# not move it, while the swimlane half of the SAME instruction landed at once.
# ---------------------------------------------------------------------------

def _flow(nodes, edges):
    return diagram_engine.DiagramSpec(
        diagram_type="flow", title="Joiner",
        nodes=[diagram_engine.DiagramNode(**n) for n in nodes],
        edges=[diagram_engine.DiagramEdge(**e) for e in edges])


_BRANCH_NODES = [
    {"id": "a", "label": "HR Joiner Event", "group": "HRMS"},
    {"id": "q", "label": "Identity Already Exists?", "group": "SailPoint IIQ"},
    {"id": "y", "label": "Update Attributes", "group": "SailPoint IIQ"},
    {"id": "n", "label": "Provision New Identity", "group": "SailPoint IIQ"},
]
_BRANCH_EDGES = [
    {"source": "a", "target": "q"},
    {"source": "q", "target": "y", "label": "Yes"},
    {"source": "q", "target": "n", "label": "No"},
]


def test_a_question_node_left_as_process_is_rejected():
    reason = diagram_engine.spec_shortfall(_flow(_BRANCH_NODES, _BRANCH_EDGES))
    assert reason and "decision" in reason
    assert "Identity Already Exists?" in reason


def test_a_properly_marked_decision_passes():
    nodes = [dict(n) for n in _BRANCH_NODES]
    nodes[1]["shape"] = "decision"
    assert diagram_engine.spec_shortfall(_flow(nodes, _BRANCH_EDGES)) is None


def test_outcome_labelled_edges_count_as_a_branch():
    """Not every branch point is phrased as a question -- but the edge labels
    must read as OUTCOMES of a choice, not as destinations."""
    nodes = [{"id": "a", "label": "Aggregate", "group": "HRMS"},
             {"id": "r", "label": "Route by identity type", "group": "IIQ"},
             {"id": "e", "label": "Employee path", "group": "IIQ"},
             {"id": "c", "label": "Contractor path", "group": "IIQ"}]
    edges = [{"source": "a", "target": "r"},
             {"source": "r", "target": "e", "label": "Employee"},
             {"source": "r", "target": "c", "label": "Contractor"}]
    assert diagram_engine.spec_shortfall(_flow(nodes, edges)) is not None


def test_a_provisioning_fan_out_is_not_a_branch():
    """THE run-12 failure. These are the actual node labels the old rule
    rejected, twice, costing the joiner-flow diagram entirely:

        "Provision Accounts & Entitlements"
        "Provision AD Account & Groups"
        "Set Initial Password & Propagate"

    Each provisions to several targets, so each has multiple labelled outgoing
    edges. None is a choice. The corrective retry then told the model to mark
    them as decisions, it complied, and a DIFFERENT fan-out node tripped the
    rule -- two rounds, four nodes, no convergence.
    """
    for label in ("Provision Accounts & Entitlements",
                  "Provision AD Account & Groups",
                  "Set Initial Password & Propagate"):
        nodes = [{"id": "p", "label": label, "group": "SailPoint IIQ"},
                 {"id": "ad", "label": "Active Directory", "group": "AD"},
                 {"id": "app", "label": "Target Applications", "group": "Apps"}]
        edges = [{"source": "p", "target": "ad", "label": "AD account"},
                 {"source": "p", "target": "app", "label": "application accounts"}]
        assert diagram_engine.spec_shortfall(_flow(nodes, edges)) is None, label


def test_a_shape_shortfall_never_loses_the_diagram():
    """Runs 9 and 12 both lost the Integration / Joiner Flow because some node
    shapes were wrong. A rectangle where a diamond belongs is cosmetic; no
    diagram at all is a missing page."""
    assert not diagram_engine._is_fatal_shortfall(
        'branch points were not marked as decisions, so they would render as '
        'plain rectangles instead of diamonds: "Provision Accounts"')


def test_everything_except_shapes_is_still_fatal():
    """Advisory must not mean "accept anything". The cosmetic case is
    WHITELISTED rather than the fatal ones blacklisted, so a check added later
    defaults to fatal -- the first version matched on "no nodes"/"no edges" and
    missed the real wordings ("it contained 2 node(s)"), which would have let an
    empty spec render."""
    for fatal in ("it contained 2 node(s)",
                  "it contained 8 nodes but only 1 edge(s), so the components are "
                  "not connected to each other",
                  "12 of 15 components had no connections at all",
                  "some future check nobody has written yet"):
        assert diagram_engine._is_fatal_shortfall(fatal), fatal


def test_an_architecture_fan_out_is_not_a_decision():
    """A load balancer with two labelled edges is not a branch point. This rule
    must not fire outside flow diagrams or it would reject every HA topology."""
    spec = diagram_engine.DiagramSpec(
        diagram_type="architecture", title="Deployment",
        nodes=[diagram_engine.DiagramNode(id="lb", label="Load Balancer", group="DMZ"),
               diagram_engine.DiagramNode(id="n1", label="IIQ Node 1", group="App"),
               diagram_engine.DiagramNode(id="n2", label="IIQ Node 2", group="App")],
        edges=[diagram_engine.DiagramEdge(source="lb", target="n1", label="HTTPS"),
               diagram_engine.DiagramEdge(source="lb", target="n2", label="HTTPS")])
    assert diagram_engine.spec_shortfall(spec) is None


def test_the_rejection_names_the_offending_nodes():
    """The retry prompt is built from this string, so it has to be specific
    enough for the model to act on."""
    reason = diagram_engine.spec_shortfall(_flow(_BRANCH_NODES, _BRANCH_EDGES))
    assert 'shape="decision"' in reason


# ---------------------------------------------------------------------------
# The corrective retry must address the shortfall it was given.
#
# Live in run 9: the Integration / Joiner Flow spec was rejected for unmarked
# decision nodes, and the retry appended a fixed paragraph about graphs and
# edges -- advice with no bearing on the fault. It failed the same check twice
# and the diagram was abandoned. A generic correction is worse than none: it
# consumes the single retry without addressing the problem.
# ---------------------------------------------------------------------------

def test_a_decision_rejection_gets_decision_advice():
    reason = "branch points were not marked as decisions, so they would render as rectangles"
    out = diagram_engine._correction_for(reason)
    assert 'shape="decision"' in out
    assert "roughly as many edges as nodes" not in out, \
        "generic graph advice sent for a decision-shape fault"


def test_a_connectivity_rejection_still_gets_graph_advice():
    out = diagram_engine._correction_for("12 of 15 components had no connections at all")
    assert "GRAPH" in out
    assert 'shape="decision"' not in out


def test_every_correction_quotes_the_actual_reason():
    """The model cannot fix what it was not told."""
    for reason in ("branch points were not marked as decisions",
                   "the spec had no nodes at all",
                   "12 of 15 components had no connections at all"):
        assert reason in diagram_engine._correction_for(reason)


def test_decision_correction_lists_the_valid_shapes():
    """A correction that names the field but not its legal values invites a
    second failure."""
    out = diagram_engine._correction_for("branch points were not marked as decisions")
    for shape in ("process", "decision", "datastore", "external"):
        assert shape in out


def test_a_question_label_alone_is_enough():
    """Isolates the question rule: this node's edges carry NO labels, so only
    the "?" check can catch it. Without this the outcome-label path masks the
    question path and a negative control on either passes."""
    nodes = [{"id": "a", "label": "Aggregate", "group": "HRMS"},
             {"id": "q", "label": "Manager Approval Required?", "group": "Manager"},
             {"id": "x", "label": "Approve", "group": "Manager"}]
    edges = [{"source": "a", "target": "q"}, {"source": "q", "target": "x"}]
    assert diagram_engine.spec_shortfall(_flow(nodes, edges)) is not None


# ---------------------------------------------------------------------------
# ESNAD 09-24 diagram review: wrong products, no named systems, tangles,
# shapes lost, no colour, everything in a trailing section.
# ---------------------------------------------------------------------------
_ESNAD_SYSTEMS = (
    "Nafath (Citizen and workforce authentication, SSO through federation); "
    "Taadeen Platform (Privileged access, federation, SSO); Bravo/ inspection platform "
    "(Federation, SSO); ESM / ITSM Platform (Access request workflows, SSO); Bidding "
    "platform (Federation, SSO); GIS (Esri Geoportal, Admin, Maps) (SSO); PowerBi/ "
    "reporting tools (SSO); Bytebase, Grafana & OutSystem (Privileged access, SSO); "
    "Complex Management (Custom connector-based provisioning and SSO)")


def _esnad_stack():
    return diagram_engine.build_stack_spec(
        title="ESNAD — Solution Stack", client_name="ESNAD", is_saas=True,
        iam_vendor="Ping Identity for Access Management and CIAM, Saviynt for IGA and PAM",
        population="WIAM users: 5000\nCIAM users: 10000\nPAM privileged accounts: 50",
        target_integrations=_ESNAD_SYSTEMS,
        context="Integrate with ERP, HR, AD/LDAP, Taadeen, CRM/ESM, APIs, SIEM")


def test_sanitize_keeps_node_shapes():
    """Every decision diamond rendered as a rectangle: shape was dropped."""
    spec = diagram_engine.DiagramSpec(title="t", nodes=[
        diagram_engine.DiagramNode(id="q", label="MFA Required?", shape="decision"),
        diagram_engine.DiagramNode(id="d", label="AD", shape="datastore")], edges=[])
    shapes = [n.shape for n in diagram_engine.sanitize_spec(spec).nodes]
    assert shapes == ["decision", "datastore"]
    d2 = diagram_engine.build_d2(diagram_engine.sanitize_spec(spec))
    assert "shape: diamond" in d2 and "shape: cylinder" in d2


def test_stack_is_built_from_facts_with_the_right_products_and_systems():
    spec = _esnad_stack()
    labels = " | ".join(n.label for n in spec.nodes)
    groups = {n.group for n in spec.nodes}
    assert "Ping Identity: PingOne Advanced Identity Cloud (SaaS)" in groups
    assert "Saviynt: Saviynt Enterprise Identity Cloud (SaaS)" in groups
    for name in ("Taadeen Platform", "Bidding platform", "Complex Management",
                 "Workforce users (5,000)", "Customer users (10,000)",
                 "Privileged administrators (50 accounts)", "Nafath (national login)"):
        assert name in labels, name
    assert not any(k in labels for k in ("Ping DS", "Ping IDM", "Ping AM", "PingFederate"))
    # SSO reaches the whole application group with one arrow, not nine.
    assert any(e.target == "group:ESNAD applications" for e in spec.edges)
    # Rendered as a fixed grid, arrows between neighbouring columns only. The
    # columns are ordered to draw the most links (deep audit F54, review
    # 2026-10-14): applications sit between the two platforms that feed them.
    d2 = diagram_engine.build_d2(spec)
    assert "grid-columns: 4" in d2 and "main.c0 -> main.c1" in d2
    assert d2.index("Users") < d2.index("Ping Identity") < d2.index("ESNAD applications") \
        < d2.index("Saviynt") < d2.index("Identity sources")


def test_vendor_colours_and_legend():
    d2 = diagram_engine.build_d2(_esnad_stack())
    assert "#1A56DB" in d2 and "#1E8E3E" in d2, "Ping blue / Saviynt green missing"
    assert f"font-size: {diagram_engine._STACK_FONT}" in d2
    legend = diagram_engine.legend_for(_esnad_stack())
    assert "blue = Ping Identity" in legend and "green = Saviynt" in legend


def test_no_border_radius_on_non_rectangles():
    spec = diagram_engine.DiagramSpec(title="t", nodes=[
        diagram_engine.DiagramNode(id="q", label="Approved?", shape="decision")], edges=[])
    block = diagram_engine.build_d2(spec).split('q: "Approved?"')[1].split("\n}\n")[0]
    assert "border-radius" not in block


def test_too_complex_spec_is_sent_back_to_simplify_then_kept():
    nodes = [diagram_engine.DiagramNode(id=f"n{i}", label=f"Node {i}") for i in range(20)]
    edges = [diagram_engine.DiagramEdge(source=f"n{i}", target=f"n{i+1}") for i in range(19)]
    spec = diagram_engine.DiagramSpec(diagram_type="architecture", title="t",
                                      nodes=nodes, edges=edges)
    why = diagram_engine.spec_shortfall(spec)
    assert why and "too complex" in why
    assert not diagram_engine._is_fatal_shortfall(why)
    assert "Simplify" in diagram_engine._correction_for(why)


def test_engagement_facts_reach_the_spec_prompt_untruncated():
    seen = {}

    async def fake(model, messages, **kw):
        seen["prompt"] = messages[1]["content"]
        return diagram_engine.DiagramSpec(title="x", nodes=[
            diagram_engine.DiagramNode(id="a", label="A"),
            diagram_engine.DiagramNode(id="b", label="B")],
            edges=[diagram_engine.DiagramEdge(source="a", target="b")])

    facts = "DEPLOYMENT: PingOne Advanced Identity Cloud; CLIENT SYSTEMS: Taadeen"
    asyncio.run(diagram_engine.generate_diagram_spec(
        fake, title="t", context_text="x" * 20000, facts=facts))
    assert facts in seen["prompt"]


def test_diagram_regenerate_bypasses_the_response_cache():
    seen = {}

    async def fake(model, messages, **kw):
        seen.update(kw)
        return diagram_engine.DiagramSpec(title="x", nodes=[
            diagram_engine.DiagramNode(id="a", label="A"),
            diagram_engine.DiagramNode(id="b", label="B")],
            edges=[diagram_engine.DiagramEdge(source="a", target="b")])

    asyncio.run(diagram_engine.generate_diagram_spec(fake, title="t"))
    assert seen["extra_headers"] == {"X-OpenRouter-Cache": "false"}


def test_stack_parses_comma_separated_systems_and_finds_active_directory():
    """ESNAD 09-25: commas (some inside parentheses) and 'Active Directory /
    LDAP' in a separate field gave one truncated box and no AD node."""
    ti = ("Nafath, Taadeen Platform, Bravo/ inspection platform, ESM / ITSM Platform, "
          "Bidding platform, GIS (Esri Geoportal, Admin, Maps), PowerBi/ reporting tools, "
          "Bytebase, Grafana & OutSystem, Complex Management")
    assert "GIS (Esri Geoportal, Admin, Maps)" in diagram_engine._system_names(ti)
    spec = diagram_engine.build_stack_spec(
        title="t", client_name="ESNAD", is_saas=True,
        iam_vendor="Ping Identity for Access Management and CIAM, Saviynt for IGA and PAM",
        target_integrations=ti,
        context="Active Directory / LDAP (Workforce directory & legacy authentication)")
    labels = [n.label for n in spec.nodes]
    groups = {n.group for n in spec.nodes}
    assert "ESNAD applications" in groups and "Taadeen Platform" in labels
    assert "Nafath (national login)" in labels
    assert "Active Directory / LDAP" in labels
    apps = [n.label for n in spec.nodes if n.group == "ESNAD applications"]
    assert len(apps) == 9 and "GIS (Esri Geoportal, Admin, Maps)" in apps, apps


def test_a_too_wide_architecture_diagram_is_retried_top_to_bottom():
    tried = []

    def fake_run(source, timeout, layout=None):
        tried.append(source.splitlines()[0])
        return b'<svg width="1000" height="200"></svg>'

    orig = diagram_engine._d2_run
    diagram_engine._d2_run = fake_run
    try:
        spec = diagram_engine.DiagramSpec(diagram_type="architecture", title="t", nodes=[
            diagram_engine.DiagramNode(id="a", label="A")], edges=[])
        diagram_engine._render_with_d2(spec, "svg", 5)
    finally:
        diagram_engine._d2_run = orig
    assert "direction: down" in tried, tried


@pytest.mark.skipif(not diagram_engine.d2_available(), reason="d2 not installed")
def test_every_diagram_form_actually_compiles_with_d2():
    """Real D2, not string checks: the stack grid, every node shape, vendor
    colours and group edges must compile, or the diagram silently falls back
    to Graphviz in production."""
    shapes = [diagram_engine.DiagramNode(id=f"s{i}", label=f"{k} node", shape=k,
                                         group="Saviynt IGA" if i % 2 else "Ping Identity")
              for i, k in enumerate(diagram_engine.NODE_SHAPES)]
    edges = [diagram_engine.DiagramEdge(source="s0", target="s1", label="x"),
             diagram_engine.DiagramEdge(source="s1", target="group:Ping Identity", label="y")]
    for dtype in ("flow", "architecture"):
        spec = diagram_engine.sanitize_spec(diagram_engine.DiagramSpec(
            diagram_type=dtype, title="t", nodes=shapes, edges=edges))
        assert diagram_engine._d2_run(diagram_engine.build_d2(spec), 25), dtype
    assert diagram_engine._d2_run(diagram_engine.build_d2(_esnad_stack()), 25), "stack"


def test_stack_links_each_platform_to_the_sources_band():
    """Review finding: the sources band floated with no connection."""
    d2 = diagram_engine.build_d2(_esnad_stack())
    # Two links, each labelled with the spec's own edge labels (deep audit F54:
    # the labels used to be a fixed "HR feed · accounts · audit").
    assert d2.count("<-> sources") == 2
    assert "joiner/mover/leaver" in d2 and "accounts and groups" in d2
    if diagram_engine.d2_available():
        assert diagram_engine._d2_run(d2, 25)


def test_stack_uses_the_short_client_name_and_finds_hr_in_requirements():
    spec = diagram_engine.build_stack_spec(
        title="t", client_name="Saudi Mining Services Company (ESNAD)", is_saas=True,
        iam_vendor="Ping Identity for Access Management and CIAM, Saviynt for IGA and PAM",
        target_integrations="Taadeen Platform, Bidding platform",
        context='[{"id": "ILM-07", "text": "HR-driven joiner/mover/leaver from authoritative HR source"}]')
    assert "ESNAD applications" in {n.group for n in spec.nodes}
    assert "HR / ERP (authoritative source)" in [n.label for n in spec.nodes]


@pytest.mark.skipif(not diagram_engine.d2_available(), reason="d2 not installed")
def test_d2_measures_with_the_rendering_font_when_one_is_configured(monkeypatch):
    """D2 sized boxes in Source Sans while the server drew DejaVu, so text
    overran box edges. With a font file configured, D2 must accept it."""
    font = "/System/Library/Fonts/Supplemental/Arial.ttf"
    if not os.path.exists(font):
        pytest.skip("no test font")
    monkeypatch.setattr(diagram_engine, "_D2_FONTS", (("--font-regular", font),))
    assert diagram_engine._d2_run(diagram_engine.build_d2(_esnad_stack()), 25)


def test_gantt_is_drawn_from_week_columns_and_skipped_without_them():
    h = ["Workstream", "Phase", "Start Week", "End Week", "Key Activities"]
    rows = [["IGA", "Design", "1", "8", "x"], ["IGA", "Build", "W9", "Week 20", "x"],
            ["PAM", "Build", "9", "bad", "x"]]
    items = diagram_engine.gantt_rows(h, rows)
    assert items == [("IGA", "Design", 1, 8), ("IGA", "Build", 9, 20)]
    png = diagram_engine.render_gantt(items)
    assert png and png[:4] == b"\x89PNG"
    assert diagram_engine.gantt_rows(["Phase", "Duration"], [["a", "4 weeks"]]) == []
    assert diagram_engine.render_gantt(items[:1]) is None


def test_a_plan_whose_weeks_are_all_zero_gets_no_chart_and_still_assembles():
    """Deep audit F71: two rows at week 0 divided by zero and ended the whole
    document at assembly. A chart is decoration; the table stays, the chart goes."""
    h = ["Workstream", "Phase", "Start Week", "End Week"]
    rows = [["IGA", "Kick-off", "0", "0"], ["PAM", "Kick-off", "Week 0", "0"]]
    assert diagram_engine.render_gantt(diagram_engine.gantt_rows(h, rows)) is None
    import document_engine
    from docx import Document
    doc = Document()
    table = "| " + " | ".join(h) + " |\n|---|---|---|---|\n" + "\n".join(
        "| " + " | ".join(r) + " |" for r in rows)
    document_engine._add_body_paragraphs(doc, table + "\n")
    assert len(doc.tables) == 1 and not doc.inline_shapes


def test_plan_table_in_a_draft_gets_a_chart_under_it():
    from docx import Document
    import document_engine
    doc = Document()
    document_engine._add_body_paragraphs(doc, (
        "| Workstream | Phase | Start Week | End Week |\n|---|---|---|---|\n"
        "| WIAM | Design | 1 | 8 |\n| WIAM | Build | 9 | 20 |\n"))
    assert len(doc.tables) == 1 and len(doc.inline_shapes) == 1


def test_compact_never_merges_leaves_of_different_kinds():
    """Deep audit F56: a database, a person and a queue hanging off one source
    were folded into one database-shaped box. Leaves merge only when they are
    the same kind of thing; like leaves still merge (the legibility fix stays)."""
    from diagram_engine import DiagramSpec, _compact
    spec = DiagramSpec.model_validate({
        "diagram_type": "architecture", "title": "t",
        "nodes": [{"id": "iga", "label": "IGA", "group": "IAM"},
                  {"id": "db", "label": "HR database", "group": "Targets", "shape": "datastore"},
                  {"id": "mgr", "label": "Line manager", "group": "Targets", "shape": "person"},
                  {"id": "q", "label": "Event queue", "group": "Targets", "shape": "queue"},
                  *[{"id": f"d{i}", "label": f"Directory {i}", "group": "Targets",
                     "shape": "datastore"} for i in range(3)]],
        "edges": [{"source": "iga", "target": "db", "label": "SQL"},
                  {"source": "iga", "target": "mgr", "label": "approval"},
                  {"source": "iga", "target": "q", "label": "events"},
                  *[{"source": "iga", "target": f"d{i}", "label": "LDAP"} for i in range(3)]]})
    out = _compact(spec)
    by_id = {n.id: n for n in out.nodes}
    # The person and the queue stand alone, with their own edges and labels.
    assert by_id["mgr"].shape == "person" and by_id["q"].shape == "queue"
    assert ("mgr", "approval") in [(e.target, e.label) for e in out.edges]
    assert ("q", "events") in [(e.target, e.label) for e in out.edges]
    # The four datastores are one kind, so they still fold into one box.
    stores = [n for n in out.nodes if n.shape == "datastore"]
    assert len(stores) == 1 and "HR database" in stores[0].label.replace("\\n", " ")


def test_compact_merges_fan_out_leaves_and_wraps_long_labels():
    from diagram_engine import DiagramSpec, _compact
    spec = DiagramSpec.model_validate({
        "diagram_type": "architecture", "title": "t",
        "nodes": [{"id": "idp", "label": "PingOne Advanced Identity Cloud (AM & CIAM)", "group": "IAM"},
                  *[{"id": f"a{i}", "label": f"App {i}", "group": "Apps"} for i in range(4)],
                  {"id": "siem", "label": "SIEM", "group": "Ops"}],
        "edges": [*[{"source": "idp", "target": f"a{i}", "label": "SAML" if i % 2 else "OIDC"}
                    for i in range(4)],
                  {"source": "idp", "target": "siem", "label": "Syslog"}]})
    out = _compact(spec)
    ids = [n.id for n in out.nodes]
    assert ids == ["idp", "a0", "siem"]
    assert out.nodes[1].label.replace("\\n", " ") == "App 0 · App 1 · App 2 · App 3"
    assert "\\n" in out.nodes[0].label                   # long label wrapped
    assert [(e.target, e.label) for e in out.edges] == [("a0", "OIDC / SAML"), ("siem", "Syslog")]
    assert len(spec.nodes) == 6                          # stored spec untouched
    stack = spec.model_copy(update={"diagram_type": "stack"})
    assert _compact(stack) is stack


# --- Deep audit F72: the stack shows only capabilities that are in scope -----

def _stack_labels(**answers):
    # Through app._stack_spec, the caller: a filter the caller never feeds is
    # the bug class this project keeps meeting. Dummy keys so app imports.
    for key, value in (("OPENROUTER_API_KEY", "test-key"), ("SUPABASE_KEY", "test-key"),
                       ("SUPABASE_URL", "https://example.invalid")):
        os.environ.setdefault(key, value)
    import app
    base = {"client_name": "Acme", "iam_vendor": "Ping Identity for Access Management and SSO"}
    spec = app._stack_spec({**base, **answers}, "Acme — Solution Stack")
    return [n.label for n in spec.nodes], {n.group for n in spec.nodes}


def test_the_stack_drops_a_capability_the_answers_place_out_of_scope():
    """An SSO-only deal with MFA ruled out still printed "adaptive MFA" on the
    one picture that goes into every proposal with no review step."""
    labels, _ = _stack_labels(out_of_scope="MFA is out of scope for this phase.")
    workforce = next(l for l in labels if l.startswith("Workforce access"))
    assert "MFA" not in workforce
    assert "SSO" in workforce and "federation" in workforce      # the rest stays
    # Saying a capability stays in scope does not remove it.
    labels, _ = _stack_labels(out_of_scope="No exclusions for MFA; MFA remains in scope.")
    assert "adaptive MFA" in next(l for l in labels if l.startswith("Workforce access"))
    # With nothing excluded the capability list is unchanged (IV's own stack
    # slide packs bullets into each box).
    labels, _ = _stack_labels()
    assert "Workforce access: SSO, adaptive MFA, federation" in labels
    # Everything excluded leaves the domain name alone, never an empty list.
    labels, _ = _stack_labels(out_of_scope="SSO, MFA and federation are excluded.")
    assert "Workforce access" in labels


def test_a_client_who_forbids_saas_gets_no_saas_product_name_on_the_stack():
    _, groups = _stack_labels(deployment_model="SaaS is not permitted; private data centre")
    assert not any("Identity Cloud" in g or "SaaS" in g for g in groups), groups
    _, groups = _stack_labels(deployment_model="SaaS")
    assert any("PingOne Advanced Identity Cloud (SaaS)" in g for g in groups), groups


def test_every_stack_capability_comes_from_the_shared_domain_definition():
    """One definition: a bullet the stack can show must be a capability
    proposal_templates.DOMAIN_CAPABILITIES lists for that domain."""
    import re
    import proposal_templates as pt
    assert set(pt.DOMAIN_STACK_BULLETS) == set(pt.DOMAIN_CAPABILITIES)
    for dom, (_head, bullets) in pt.DOMAIN_STACK_BULLETS.items():
        for label, pattern in bullets:
            assert re.search(pattern, pt.DOMAIN_CAPABILITIES[dom], re.I), (dom, label)


# --- Deep audit F54: stack arrows come from the spec's edges -----------------

def _stack_arrows(spec):
    """(left, arrow, right, label) for every connection in the stack's D2."""
    out = []
    for line in diagram_engine.build_d2(spec).splitlines():
        m = re.match(r'\s*(\S+) (<->|->|<-) (\S+): "(.*)" \{', line)
        if m:
            out.append((m.group(1), m.group(2), m.group(3), m.group(4).replace("\\n", " ")))
    return out


def _two_vendor_stack(edges):
    N = diagram_engine.DiagramNode
    return diagram_engine.sanitize_spec(diagram_engine.DiagramSpec(
        diagram_type="stack", title="t", edges=edges, nodes=[
            N(id="u", label="Workforce users", group="Users", shape="person"),
            N(id="am", label="Workforce access", group="Ping Identity"),
            N(id="iga", label="Identity governance (IGA)", group="Saviynt"),
            N(id="app", label="ERP", group="Acme applications"),
            N(id="hr", label="HR / ERP (authoritative source)",
              group="Identity sources and operations", shape="datastore")]))


def test_the_stack_draws_no_arrow_the_spec_does_not_contain():
    """The renderer never read spec.edges: it drew an arrow between every pair
    of neighbouring columns from a fixed label table, so two vendors side by
    side always exchanged "identity data" and an empty spec still had arrows."""
    E = diagram_engine.DiagramEdge
    assert _stack_arrows(_two_vendor_stack([])) == []
    # One real edge, one arrow, carrying that edge's own label.
    one = _two_vendor_stack([E(source="iga", target="app", label="provisioning")])
    assert _stack_arrows(one) == [("main.c2", "->", "main.c3", "provisioning")]
    # A different edge gives a different picture.
    other = _two_vendor_stack([E(source="am", target="iga", label="SCIM")])
    assert _stack_arrows(other) == [("main.c1", "->", "main.c2", "SCIM")]
    # Direction follows the edge; both ways is a double-headed arrow.
    back = _two_vendor_stack([E(source="iga", target="am", label="entitlements")])
    assert _stack_arrows(back) == [("main.c1", "<-", "main.c2", "entitlements")]


def test_the_stack_never_routes_an_arrow_across_a_column():
    """Non-adjacent columns stay unconnected: an arrow from column 1 to column
    3 would cross column 2, which is what the grid exists to prevent. The
    columns are ordered (Users first) to make as many links neighbours as
    possible; what no order can place side by side is not drawn."""
    E = diagram_engine.DiagramEdge
    # One link between Ping and the applications: they become neighbours.
    spec = _two_vendor_stack([E(source="am", target="app", label="SSO (SAML / OIDC)")])
    assert _stack_arrows(spec) == [("main.c1", "->", "main.c2", "SSO (SAML / OIDC)")]
    # Three columns all linked to each other: in a row, two pairs can be
    # neighbours and one cannot. Every order draws two; on that tie IV's own
    # order (platforms, then applications) is kept, and the third link is left out.
    triangle = _two_vendor_stack([E(source="am", target="iga", label="SCIM"),
                                  E(source="iga", target="app", label="provisioning"),
                                  E(source="am", target="app", label="SSO")])
    arrows = _stack_arrows(triangle)
    assert arrows == [("main.c1", "->", "main.c2", "SCIM"),
                      ("main.c2", "->", "main.c3", "provisioning")]
    assert all(int(a[2][-1]) - int(a[0][-1]) == 1 for a in arrows)   # neighbours only


def test_the_sources_band_is_linked_only_where_the_spec_links_it():
    E = diagram_engine.DiagramEdge
    spec = _two_vendor_stack([E(source="hr", target="iga", label="joiner/mover/leaver feed")])
    assert _stack_arrows(spec) == [("main.c2", "<-", "sources", "joiner/mover/leaver feed")]


def test_the_esnad_stack_keeps_its_true_links_and_loses_the_invented_one():
    arrows = _stack_arrows(_esnad_stack())
    labels = " | ".join(a[3] for a in arrows)
    assert "identity data" not in labels                       # nobody stated it
    assert ("main.c0", "->", "main.c1", "sign-in") in arrows   # users to Ping: stated
    # Users | Ping | applications | Saviynt: Ping's SSO reaches the applications,
    # and Saviynt's provisioning arrives from the other side.
    assert ("main.c1", "->", "main.c2", "SSO (SAML / OIDC)") in arrows
    saviynt = next(a for a in arrows if (a[0], a[2]) == ("main.c2", "main.c3"))
    assert saviynt[1] == "<-" and "provisioning" in saviynt[3]
    assert "joiner/mover/leaver feed" in labels
    if diagram_engine.d2_available():
        assert diagram_engine._d2_run(diagram_engine.build_d2(_esnad_stack()), 25)


def test_two_zones_whose_names_differ_only_by_punctuation_stay_two_zones():
    """Deep audit F55: D2 ids replace every non-alphanumeric character with an
    underscore, so "Zone-A" and "Zone A" both became Zone_A and the two zones
    were drawn as one. A reviewer cannot be expected to notice a merged zone."""
    N, E = diagram_engine.DiagramNode, diagram_engine.DiagramEdge
    spec = diagram_engine.sanitize_spec(diagram_engine.DiagramSpec(
        diagram_type="architecture", title="t",
        nodes=[N(id="web", label="Web tier", group="Zone-A"),
               N(id="db", label="Database", group="Zone A", shape="datastore"),
               N(id="a.b", label="Proxy"), N(id="a-b", label="Cache")],
        edges=[E(source="web", target="db", label="SQL")]))
    d2 = diagram_engine.build_d2(spec)
    containers = re.findall(r'(?m)^(\w+): "Zone', d2)
    assert len(containers) == 2 and len(set(containers)) == 2, containers
    # The edge joins the two different zones, not a zone to itself.
    edge = re.search(r"(?m)^(\w+)\.\w+ -> (\w+)\.\w+", d2)
    assert edge and edge.group(1) != edge.group(2)
    # Top-level nodes that normalise alike stay distinct too.
    top = re.findall(r'(?m)^(\w+): "(?:Proxy|Cache)"', d2)
    assert len(set(top)) == 2, top
    if diagram_engine.d2_available():
        assert diagram_engine._d2_run(d2, 25)


def test_edges_that_name_nodes_by_label_are_kept():
    """ESNAD 10-16: every edge named its ends by label, all were dropped as
    dangling, and the spec came back with 12 nodes and 0 edges."""
    import diagram_engine as de
    raw = de.DiagramSpec.model_validate({
        "diagram_type": "architecture", "title": "t",
        "nodes": [{"id": "ping_aic", "label": "PingOne Advanced Identity Cloud"},
                  {"id": "nafath", "label": "Nafath"}],
        "edges": [{"source": "Nafath", "target": "PingOne Advanced Identity Cloud", "label": "OIDC"},
                  {"source": "NAFATH", "target": "ping_aic"},
                  {"source": "nowhere", "target": "ping_aic"}]})
    out = de.sanitize_spec(raw)
    assert [(e.source, e.target) for e in out.edges] == [("nafath", "ping_aic"), ("nafath", "ping_aic")]


def test_edges_under_another_key_or_with_from_to_are_read():
    """A spec whose edge list is called "connections", with from/to ends,
    used to validate as 0 edges: unknown keys are ignored."""
    import diagram_engine as de
    spec = de.DiagramSpec.model_validate({
        "title": "t", "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "connections": [{"from": "a", "to": "b", "label": "OIDC"}]})
    assert [(e.source, e.target, e.label) for e in spec.edges] == [("a", "b", "OIDC")]


def test_schema_sent_to_the_model_requires_nodes_and_edges():
    """With defaults, only the title was required and Gemini left out the
    edges entirely (ESNAD 10-16, keys: diagram_type, nodes, title)."""
    import diagram_engine as de
    assert {"nodes", "edges"} <= set(de.DiagramSpec.model_json_schema()["required"])


def test_a_cut_discovery_context_is_logged_with_sizes_and_dropped_fields(caplog):
    """The 3,500-character spec budget stays; when it cuts, the log says how
    much and which fields were lost, so the next run shows whether the end of
    the architecture list (zones, regions, HA, topology) is reaching specs."""
    import asyncio
    import logging

    async def fake_structured(model, messages=None, **kw):
        return DiagramSpec.model_validate({
            "diagram_type": "architecture", "title": "t",
            "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"},
                      {"id": "c", "label": "C"}],
            "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}]})

    context = ("target_integrations: " + "LDAP, Nafath, Taadeen; " * 200 + "\n"
               "envs: Dev, Test, Prod\nnetwork_zones: DMZ, application, data\n"
               "regions: KSA central\ncluster_topology: active-active")
    assert len(context) > de._SPEC_CONTEXT_BUDGET
    with caplog.at_level(logging.WARNING, logger="shilpi-brain.diagram-engine"):
        asyncio.run(de.generate_diagram_spec(fake_structured, title="T", context_text=context))
    line = next(r.getMessage() for r in caplog.records if "cut" in r.getMessage())
    assert f"{len(context.strip())} -> {de._SPEC_CONTEXT_BUDGET}" in line
    assert "network_zones" in line and "regions" in line and "cluster_topology" in line
    assert "target_integrations" in line                 # the one cut part-way

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="shilpi-brain.diagram-engine"):
        asyncio.run(de.generate_diagram_spec(fake_structured, title="T",
                                             context_text="envs: Dev, Test, Prod"))
    assert not [r for r in caplog.records if "cut" in r.getMessage()]   # nothing cut, nothing said


def test_stack_from_the_esnad_10_23_reading():
    """The run drew the reader's lead-in sentence as an application, showed AD,
    HR and SIEM twice, hid the client's named systems in "Other systems (8)",
    had no PAM count, and lost the product names (SaaS was missed)."""
    import document_engine
    ti = ("The solution must connect and integrate with: Active Directory / LDAP (AD/LDAP); "
          "ERP; HR; CRM/ESM; ESM / ITSM Platform; Taadeen Platform; SIEM (via syslog/API); "
          "APIs; Bidding platform; Bravo / inspection platform; Bytebase, Grafana & OutSystem; "
          "Complex Management; GIS (Esri Geoportal, Admin, Maps); PowerBi / reporting tools; "
          "Nafath (for citizen identity verification); B2B federation with external government "
          "entities and partners (external IdPs); and custom/legacy systems requiring custom "
          "REST connectors.")
    dm = ("Software-as-a-Service (SaaS) offering hosted within the Kingdom of Saudi Arabia "
          "(SaaS platform operated from KSA-sovereign infrastructure; On-premises or "
          "KSA-sovereign cloud, e.g., Hyperscaler KSA regions; IAM platform SaaS subscription license).")
    assert document_engine._looks_like_saas(dm)
    spec = diagram_engine.build_stack_spec(
        title="t", client_name="Saudi Mining Services Company (ESNAD)", is_saas=True,
        iam_vendor="Ping Identity for Access Management and CIAM, Saviynt for IGA and PAM",
        population="Up to 20,000 identities; 10,000 users including 50 privileged accounts",
        target_integrations=ti)
    apps = [n.label for n in spec.nodes if n.group == "ESNAD applications"]
    assert not any("solution must" in a or a in ("HR", "SIEM") or "LDAP" in a for a in apps), apps
    for name in ("Bravo / inspection platform", "GIS", "PowerBi / reporting tools",
                 "Complex Management", "Taadeen Platform"):
        assert any(a.startswith(name) for a in apps), (name, apps)
    labels = [n.label for n in spec.nodes]
    assert "Privileged administrators (50 accounts)" in labels
    assert any("PingOne Advanced Identity Cloud" in (n.group or "") for n in spec.nodes)
    pairs = {(e.source.split("_")[0], e.target.split("_")[0]) for e in spec.edges}
    assert ("iga", "wiam") in pairs and ("pam", "pam") in pairs
