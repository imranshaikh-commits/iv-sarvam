"""
Sprint 5 smoke test — runs with NO API keys / NO network.

Strategy:
  * document_engine takes the brain helpers (embed / retrieve / build-system) as
    injected callables, so we pass stubs — no import of app.py, no secrets.
  * The single OpenRouter call lives in document_engine.draft_with_openrouter,
    which we monkeypatch to return canned drafted prose.

Run directly (`python tests/test_document_engine.py`) or via pytest. It writes a
sample DOCX to /home/user/workspace/sarvam_sample_proposal.docx and asserts the
document contains the expected markers.
"""

import asyncio
import io
import os
import sys

# Make the brain package importable when run as a bare script from any cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BRAIN = os.path.dirname(_HERE)
if _BRAIN not in sys.path:
    sys.path.insert(0, _BRAIN)

import httpx
from docx import Document

import document_engine
from document_engine import assemble_docx, generate_proposal

OUTPUT_PATH = "/home/user/workspace/sarvam_sample_proposal.docx"
CLIENT_NAME = "Meridian Bank"
SME_MARKER = "[SME REVIEW]"


# --- stubs ------------------------------------------------------------------
async def stub_embed(client, text):
    return [0.01] * 1536


def _fake_chunks():
    return [
        {
            "chunk_text": "SailPoint IdentityIQ was deployed with automated joiner-mover-leaver "
            "provisioning across 42 target applications, including SAP and Active Directory.",
            "heading": "Provisioning Architecture",
            "similarity": 0.62,
            "client_name": "Northwind Insurance",
            "iam_vendor": "sailpoint",
            "industry": "Insurance",
        },
        {
            "chunk_text": "Access certification campaigns ran quarterly with delegated reviewers and "
            "automated revocation of orphaned accounts.",
            "heading": "Access Certification",
            "similarity": 0.58,
            "client_name": "Acme Retail",
            "iam_vendor": "sailpoint",
            "industry": "Retail",
        },
    ]


async def stub_retrieve(client, embedding, query, k=8):
    return _fake_chunks()


def stub_build_system(chunks):
    return "=== EVIDENCE ===\n" + "\n".join(
        f"[{i}] {c['chunk_text']}" for i, c in enumerate(chunks, 1)
    )


async def stub_draft(client, system_prompt, user_prompt):
    # Canned drafted paragraph with a [1] citation and an SME-review marker.
    return (
        "Inspirit Vision proposes a SailPoint IdentityIQ deployment covering automated "
        "joiner-mover-leaver provisioning across the client's core applications [1]. "
        "Quarterly access certification campaigns will be established with delegated "
        "reviewers [1].\n\n"
        f"{SME_MARKER}: pricing and licensing counts must be confirmed by an SME before "
        "any client-facing use.\n\n"
        "- Assumption: target application inventory will be provided during discovery."
    )


async def _run() -> bytes:
    # Monkeypatch the isolated network call.
    document_engine.draft_with_openrouter = stub_draft

    async with httpx.AsyncClient() as client:
        result = await generate_proposal(
            client,
            rfp_text="Deliver an enterprise IAM implementation with automated provisioning "
            "and access certification.",
            client_name=CLIENT_NAME,
            proposal_type="implementation",
            iam_vendor="SailPoint",
            embed_fn=stub_embed,
            retrieve_fn=stub_retrieve,
            build_grounded_system_fn=stub_build_system,
            sections=None,
            include_compliance_matrix=False,
        )
    assert result["filename"].startswith("Sarvam_Proposal_Meridian_Bank_"), result["filename"]
    assert result["filename"].endswith(".docx")
    assert result["sections_meta"], "expected section metadata"
    return result["docx_bytes"]


def _extract_text(docx_bytes: bytes) -> str:
    doc = Document(io.BytesIO(docx_bytes))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


def test_generate_proposal_docx():
    docx_bytes = asyncio.run(_run())
    assert docx_bytes, "DOCX bytes must be non-empty"

    with open(OUTPUT_PATH, "wb") as f:
        f.write(docx_bytes)
    assert os.path.getsize(OUTPUT_PATH) > 0, "written DOCX must be non-empty"

    text = _extract_text(docx_bytes)
    assert CLIENT_NAME in text, "client name missing"
    assert "DRAFT" in text, "DRAFT safety banner missing"
    assert "Executive Summary" in text, "expected a section heading"
    assert "Citation Appendix" in text, "citation appendix heading missing"
    assert SME_MARKER in text, "SME REVIEW marker missing"
    # A citation source from the fake corpus should appear in the appendix.
    assert "Northwind Insurance" in text, "citation source missing from appendix"

    print("SMOKE TEST PASSED")
    print(f"  wrote {OUTPUT_PATH} ({os.path.getsize(OUTPUT_PATH)} bytes)")
    print(f"  client name present : {CLIENT_NAME in text}")
    print(f"  DRAFT banner        : {'DRAFT' in text}")
    print(f"  section heading     : {'Executive Summary' in text}")
    print(f"  citation appendix   : {'Citation Appendix' in text}")
    print(f"  SME REVIEW marker   : {SME_MARKER in text}")


def test_assemble_docx_directly():
    """Exercise assemble_docx on its own with a compliance-matrix markdown block."""
    sections = [
        {
            "id": "executive_summary",
            "title": "Executive Summary",
            "content": "A grounded summary with a citation [1].",
            "citations": _fake_chunks(),
            "max_similarity": 0.62,
            "needs_sme_review": False,
        }
    ]
    md = "# DRAFT Compliance Matrix\n\n| Req | Status |\n|---|---|\n| REQ-001 | Covered |\n"
    docx_bytes = assemble_docx(
        {"client_name": CLIENT_NAME, "proposal_type": "implementation", "iam_vendor": "SailPoint"},
        sections,
        compliance_markdown=md,
    )
    text = _extract_text(docx_bytes)
    assert "Compliance Matrix" in text
    assert "REQ-001" in text


if __name__ == "__main__":
    test_generate_proposal_docx()
    test_assemble_docx_directly()
    print("ALL CHECKS PASSED")
