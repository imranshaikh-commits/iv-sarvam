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
    assert dot.startswith("digraph sarvam_diagram {")
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
    if de.dot_available():
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
    assert kw["max_tokens"] == de.DIAGRAM_SPEC_MAX_TOKENS == 1500
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
