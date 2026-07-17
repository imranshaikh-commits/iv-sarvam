"""
Pass 3 depth tests — run with NO API keys / NO network.

Covers:
  * depth-tier resolution (valid values, invalid/missing -> safe default)
  * per-call token caps never exceed the hard ceiling
  * full mode adds multi-subsection drafting + the appendix pack (RACI, timeline,
    sizing, integration inventory, risks)
  * standard/no-depth generate-proposal still succeeds with a mocked LLM
"""

import asyncio
import io
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BRAIN = os.path.dirname(_HERE)
if _BRAIN not in sys.path:
    sys.path.insert(0, _BRAIN)

import httpx
from docx import Document

import document_engine
from document_engine import assemble_docx, generate_proposal
from proposal_templates import (
    DEFAULT_DEPTH,
    DEPTH_TIERS,
    VALID_DEPTHS,
    get_depth_tier,
)

CLIENT_NAME = "Meridian Bank"
APPENDIX_HEADINGS = [
    "Appendix A — RACI Matrix",
    "Appendix B — Indicative Timeline",
    "Appendix C — Sizing & Volumetrics",
    "Appendix D — Integration Inventory",
    "Appendix E — Risk Register",
]


# --- stubs ------------------------------------------------------------------
async def stub_embed(client, text):
    return [0.01] * 1536


def _fake_chunks():
    return [
        {
            "chunk_text": "SailPoint IdentityIQ deployed with automated joiner-mover-leaver "
            "provisioning across 42 target applications.",
            "heading": "Provisioning Architecture",
            "similarity": 0.62,
            "client_name": "Northwind Insurance",
            "iam_vendor": "sailpoint",
        },
        {
            "chunk_text": "Access certification campaigns ran quarterly with delegated reviewers.",
            "heading": "Access Certification",
            "similarity": 0.58,
            "client_name": "Acme Retail",
            "iam_vendor": "sailpoint",
        },
    ]


async def stub_retrieve(client, embedding, query, k=8):
    return _fake_chunks()


def stub_build_system(chunks):
    return "=== EVIDENCE ===\n" + "\n".join(
        f"[{i}] {c['chunk_text']}" for i, c in enumerate(chunks, 1)
    )


async def stub_draft(client, system_prompt, user_prompt, max_tokens=1500):
    # Assert the per-call cap is honoured and never exceeds the hard ceiling.
    assert max_tokens <= document_engine.MAX_DRAFT_TOKENS, max_tokens
    return (
        "Inspirit Vision proposes a SailPoint IdentityIQ deployment [1]. "
        "Quarterly access certification with delegated reviewers [1]."
    )


def _extract_text(docx_bytes: bytes) -> str:
    doc = Document(io.BytesIO(docx_bytes))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


async def _generate(depth):
    document_engine.draft_with_openrouter = stub_draft
    async with httpx.AsyncClient() as client:
        return await generate_proposal(
            client,
            rfp_text="Deliver an enterprise IAM implementation with automated provisioning.",
            client_name=CLIENT_NAME,
            proposal_type="implementation",
            iam_vendor="SailPoint",
            embed_fn=stub_embed,
            retrieve_fn=stub_retrieve,
            build_grounded_system_fn=stub_build_system,
            sections=None,
            include_compliance_matrix=False,
            proposal_depth=depth,
        )


# --- depth-tier resolution --------------------------------------------------
def test_valid_depth_values_resolve():
    assert VALID_DEPTHS == {"brief", "standard", "full"}
    for name in ("brief", "standard", "full"):
        assert get_depth_tier(name).name == name
        assert get_depth_tier(name.upper()).name == name  # case-insensitive


def test_invalid_and_missing_depth_fall_back_to_default():
    for bad in (None, "", "   ", "deep", "ultra", "123", "STANDARDX"):
        assert get_depth_tier(bad).name == DEFAULT_DEPTH == "standard"


def test_per_call_token_caps_never_exceed_hard_ceiling():
    for tier in DEPTH_TIERS.values():
        assert tier.per_call_max_tokens <= document_engine.MAX_DRAFT_TOKENS
        assert tier.per_call_max_tokens > 0


def test_full_tier_plan_is_richer_than_standard():
    full = get_depth_tier("full")
    standard = get_depth_tier("standard")
    assert full.subsections_per_section > standard.subsections_per_section
    assert full.retrieval_fanout > standard.retrieval_fanout
    assert full.include_appendices and not standard.include_appendices


# --- generation: no-depth / standard still works ---------------------------
def test_no_depth_generate_proposal_succeeds():
    result = asyncio.run(_generate(None))
    assert result["docx_bytes"]
    assert result["proposal_depth"] == "standard"
    assert result["included_appendices"] is False
    text = _extract_text(result["docx_bytes"])
    assert CLIENT_NAME in text
    assert "Executive Summary" in text
    # No appendix pack at standard depth.
    for h in APPENDIX_HEADINGS:
        assert h not in text


def test_invalid_depth_generate_falls_back():
    result = asyncio.run(_generate("nonsense"))
    assert result["proposal_depth"] == "standard"
    assert result["included_appendices"] is False


# --- full mode: subsections + appendices -----------------------------------
def test_full_mode_adds_subsections_and_appendices():
    result = asyncio.run(_generate("full"))
    assert result["proposal_depth"] == "full"
    assert result["included_appendices"] is True
    text = _extract_text(result["docx_bytes"])

    # Multi-subsection drafting: facet subheadings appear in the document.
    assert "Overview" in text
    assert "Detailed Design" in text
    assert "Considerations & Dependencies" in text

    # Appendix pack: all five appendices present with content.
    for h in APPENDIX_HEADINGS:
        assert h in text, f"missing appendix heading: {h}"
    assert "Responsible" in text  # RACI legend
    assert "InspiritVision" in text  # RACI column
    assert "Risk" in text
    assert "[ASSUMPTION]" in text  # conservative placeholders, not fabricated specifics


def test_full_mode_draft_markdown_has_subsections():
    result = asyncio.run(_generate("full"))
    assert "### Overview" in result["draft_markdown"]


def test_appendices_render_directly():
    docx_bytes = assemble_docx(
        {"client_name": CLIENT_NAME, "proposal_type": "implementation", "iam_vendor": "SailPoint"},
        [{"id": "executive_summary", "title": "Executive Summary",
          "content": "Summary [1].", "citations": _fake_chunks(),
          "max_similarity": 0.62, "needs_sme_review": False}],
        include_appendices=True,
    )
    text = _extract_text(docx_bytes)
    for h in APPENDIX_HEADINGS:
        assert h in text


if __name__ == "__main__":
    test_valid_depth_values_resolve()
    test_invalid_and_missing_depth_fall_back_to_default()
    test_per_call_token_caps_never_exceed_hard_ceiling()
    test_full_tier_plan_is_richer_than_standard()
    test_no_depth_generate_proposal_succeeds()
    test_invalid_depth_generate_falls_back()
    test_full_mode_adds_subsections_and_appendices()
    test_full_mode_draft_markdown_has_subsections()
    test_appendices_render_directly()
    print("ALL PASS 3 CHECKS PASSED")
