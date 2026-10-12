"""The real renderer stack, not mocks (audit 2026-10-12, finding 12).

The rest of the suite mocks LibreOffice and skips D2 when the binary is
absent, so a green run on a laptop or a bare CI runner said nothing about
whether the production image can draw a diagram or produce a PDF. These run
the actual binaries. Outside the brain image they skip; inside it
(SHILPI_REQUIRE_STACK=1, set by CI's image job and by deploy/deploy.sh) a
missing binary is a FAILURE, because that image is what ships.
"""
import io
import os
import shutil

import pytest
from docx import Document
from PIL import Image

import diagram_engine
import export_engine
import rfp_intake

REQUIRED = os.environ.get("SHILPI_REQUIRE_STACK") == "1"


def _need(binary: str):
    if shutil.which(binary):
        return
    if REQUIRED:
        pytest.fail(f"{binary} is missing from the production image")
    pytest.skip(f"{binary} not installed here")


def _spec():
    return diagram_engine.DiagramSpec.model_validate({
        "diagram_type": "architecture", "title": "Smoke",
        "nodes": [{"id": "a", "label": "Identity Provider", "group": "IAM"},
                  {"id": "b", "label": "Business Application", "group": "Apps"}],
        "edges": [{"source": "a", "target": "b", "label": "SAML"}]})


def test_d2_draws_a_real_diagram():
    _need("d2")
    _need("rsvg-convert")
    png = diagram_engine.render_spec(_spec(), fmt="png", renderer="d2")
    assert png and Image.open(io.BytesIO(png)).width >= 600


def test_graphviz_fallback_draws_a_real_diagram():
    _need("dot")
    png = diagram_engine.render_spec(_spec(), fmt="png", renderer="graphviz")
    assert png and Image.open(io.BytesIO(png)).width > 50


def _docx() -> bytes:
    doc = Document()
    doc.add_heading("Smoke", level=1)
    doc.add_paragraph("A proposal paragraph.")
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def test_libreoffice_turns_a_docx_into_a_pdf_and_poppler_reads_it(tmp_path):
    _need("soffice")
    pdf, err = export_engine.export_pdf(_docx())
    assert err is None and pdf and pdf.startswith(b"%PDF")
    _need("pdftoppm")
    path = tmp_path / "smoke.pdf"
    path.write_bytes(pdf)
    pages = rfp_intake.rasterise(str(path), str(tmp_path / "pages"))
    assert len(pages) == 1
