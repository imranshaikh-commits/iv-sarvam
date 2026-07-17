"""
Sprint 5 Pass 6 — export engine (lite DOCX compression + PDF export).

Opt-in, backward-compatible export post-processing for generated proposals. The
DEFAULT /v1/generate-proposal response is unaffected: this module is only invoked
when an export flag (lite / include_pdf) is set on the request.

Design rules (mirrors document_engine / diagram_engine):
  * MUST NOT import app.py — keeps the module importable in a keyless
    environment (CI / smoke tests) with no secrets present.
  * Pure stdlib + Pillow. Supabase storage lives in supabase_client; this module
    never touches the network.
  * Everything is FAIL-SOFT: compression that cannot reach the target returns a
    best-effort result plus a warning (never raises); PDF export with no soffice
    binary returns (None, error) rather than crashing the request.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from typing import Optional

from PIL import Image

log = logging.getLogger("sarvam-brain.export")

# --- lite DOCX compression --------------------------------------------------

LITE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB target ceiling for "lite" DOCX

# Progressive (downscale factor, JPEG quality) levels. Applied in order until the
# whole DOCX drops under the target or the list is exhausted (safe minimums).
_COMPRESSION_LEVELS: list[tuple[float, int]] = [
    (1.0, 85),
    (0.85, 80),
    (0.75, 70),
    (0.6, 60),
    (0.5, 50),
    (0.4, 40),
    (0.35, 35),
]

_MEDIA_PREFIX = "word/media/"


def _recompress_image(data: bytes, scale: float, quality: int) -> bytes:
    """Downscale + re-encode a single embedded image, preserving its format.

    Format is preserved so the DOCX relationship references (which include the
    file extension) stay valid. JPEGs use the quality knob; PNGs are quantized /
    optimized (lossless format, so downscale is the main lever). Any failure
    re-raises to the caller, which keeps the original bytes for that image."""
    img = Image.open(io.BytesIO(data))
    fmt = (img.format or "").upper()
    w, h = img.size
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

    out = io.BytesIO()
    if fmt in ("JPEG", "JPG"):
        img.convert("RGB").save(out, format="JPEG", quality=quality, optimize=True)
    elif fmt == "PNG":
        if img.mode in ("RGBA", "LA", "P"):
            # Keep alpha / palette; downscale + optimize without dropping channels.
            img.save(out, format="PNG", optimize=True)
        else:
            img.convert("P", palette=Image.ADAPTIVE, colors=256).save(
                out, format="PNG", optimize=True
            )
    else:
        # Unknown/other format: re-save as-is (best effort).
        img.save(out, format=fmt or "PNG")
    return out.getvalue()


def _rebuild_zip(
    infos: list[zipfile.ZipInfo], originals: dict[str, bytes], new_media: dict[str, bytes]
) -> bytes:
    """Rebuild the DOCX zip, substituting recompressed media and preserving every
    other part and its ZipInfo metadata (so the document stays a valid Word doc)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in infos:
            data = new_media.get(info.filename, originals[info.filename])
            zout.writestr(info, data)
    return buffer.getvalue()


def compress_docx_lite(
    docx_bytes: bytes, max_bytes: int = LITE_MAX_BYTES
) -> tuple[bytes, dict]:
    """Post-process a generated DOCX to shrink it under ``max_bytes``.

    Downsamples/recompresses ``word/media/*`` images with Pillow, looping through
    progressively more aggressive quality/downscale levels until the whole
    document is under the target or the safe minimums are reached.

    FAIL-SOFT in every branch — always returns ``(bytes, meta)`` and never
    raises:
      * no embedded images / already under target -> original bytes, no-op meta
      * cannot reach target -> best-effort smallest result + a ``warning``
      * a broken/unreadable image -> that image is left untouched

    meta keys: original_size, final_size, images_processed, compressed (bool),
    under_target (bool), and optionally warning / note.
    """
    original_size = len(docx_bytes)
    meta: dict = {
        "original_size": original_size,
        "final_size": original_size,
        "images_processed": 0,
        "compressed": False,
        "under_target": original_size <= max_bytes,
    }

    try:
        zin = zipfile.ZipFile(io.BytesIO(docx_bytes))
    except zipfile.BadZipFile as e:
        log.error("compress_docx_lite: not a valid zip (fail-soft): %s", e)
        meta["warning"] = f"input is not a valid DOCX zip: {e}"
        return docx_bytes, meta

    with zin:
        infos = zin.infolist()
        media = [n for n in zin.namelist() if n.startswith(_MEDIA_PREFIX)]
        if not media:
            meta["note"] = "no embedded images to compress"
            return docx_bytes, meta
        if original_size <= max_bytes:
            meta["note"] = "already under target size"
            return docx_bytes, meta

        originals = {info.filename: zin.read(info.filename) for info in infos}

    best_bytes, best_size = docx_bytes, original_size
    for scale, quality in _COMPRESSION_LEVELS:
        new_media: dict[str, bytes] = {}
        for name in media:
            data = originals[name]
            try:
                rc = _recompress_image(data, scale, quality)
                new_media[name] = rc if len(rc) < len(data) else data
            except Exception as e:  # noqa: BLE001 — a bad image never breaks export
                log.warning("compress_docx_lite: skipping image %s (%s)", name, e)
                new_media[name] = data
        candidate = _rebuild_zip(infos, originals, new_media)
        if len(candidate) < best_size:
            best_bytes, best_size = candidate, len(candidate)
        if best_size <= max_bytes:
            break

    meta.update(
        final_size=best_size,
        images_processed=len(media),
        compressed=best_size < original_size,
        under_target=best_size <= max_bytes,
    )
    if best_size > max_bytes:
        meta["warning"] = (
            f"could not compress below {max_bytes} bytes; "
            f"best effort {best_size} bytes (images already at safe minimums)"
        )
    return best_bytes, meta


# --- PDF export (LibreOffice headless) --------------------------------------

def _soffice_binary() -> Optional[str]:
    """Locate the LibreOffice headless binary, or None if unavailable."""
    return shutil.which("soffice") or shutil.which("libreoffice")


def soffice_available() -> bool:
    """True iff a LibreOffice binary is on PATH."""
    return _soffice_binary() is not None


def export_pdf(docx_bytes: bytes, timeout: float = 120.0) -> tuple[Optional[bytes], Optional[str]]:
    """Convert DOCX bytes to PDF via ``soffice --headless --convert-to pdf``.

    LibreOffice is the only realistic way to preserve the DOCX layout/branding.
    FAIL-SOFT: returns ``(None, error)`` when the binary is missing or the
    conversion fails — the caller surfaces the error in JSON, never a 500.
    Returns ``(pdf_bytes, None)`` on success.
    """
    binary = _soffice_binary()
    if not binary:
        return None, "LibreOffice (soffice) binary not found on PATH; PDF export unavailable"

    with tempfile.TemporaryDirectory() as tmp:
        docx_path = os.path.join(tmp, "proposal.docx")
        pdf_path = os.path.join(tmp, "proposal.pdf")
        with open(docx_path, "wb") as f:
            f.write(docx_bytes)
        try:
            # HOME is pinned into the temp dir so soffice can create its per-user
            # profile in a writable location (headless containers often lack one).
            subprocess.run(
                [binary, "--headless", "--convert-to", "pdf", "--outdir", tmp, docx_path],
                capture_output=True,
                timeout=timeout,
                check=True,
                env={**os.environ, "HOME": tmp},
            )
        except (subprocess.SubprocessError, OSError) as e:  # noqa: BLE001 — fail soft
            log.error("export_pdf: soffice conversion failed (fail-soft): %s", e)
            return None, f"PDF conversion failed: {e}"

        if not os.path.exists(pdf_path):
            return None, "PDF conversion produced no output file"
        with open(pdf_path, "rb") as f:
            return f.read(), None
