#!/usr/bin/env python3
"""Populate `partner_product_assets` from the Sprint 7 partner-product manifest.

CHEAP PASS ONLY -- no vision-model calls, no new cost beyond the storage
write. Classification is heuristics only (size, aspect ratio, optional
Tesseract OCR if the binary is present). vision_description stays NULL.
See sarvam_017's migration comment for why this is a separate table from
visual_assets, and why this pass is deliberately cheap rather than a full
extract_visual_assets.py-style recovery (there is nothing free to recover:
Sprint 7's text ingestion never ran vision/OCR on these documents).

Only PDF-sourced rows are covered. HTML pages' images need a different
extraction path (parsing <img> tags and fetching each one separately) and
are lower value for a first pass -- most of the manifest's genuine
architecture/screenshot content sits inside the PDF sources.

USAGE
    python3 scripts/extract_partner_product_images.py --manifest partner_product_manifest.csv --dry-run
    python3 scripts/extract_partner_product_images.py --manifest partner_product_manifest.csv
"""
from __future__ import annotations

import argparse
import csv as _csv
import hashlib
import io
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("extract_partner_images")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
ORG_ID = os.getenv("ORG_ID", "")
BUCKET = "partner-product-assets"

# Matches ingest_v2.py / extract_visual_assets.py's own floor, so a bullet,
# rule or spacer graphic never becomes a stored "asset".
MIN_IMAGE_BYTES = 3000
BUCKET_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp"}


@dataclass
class Row:
    vendor: str
    product_name: str
    source_url: str


def load_all_rows(csv_path: Path) -> list[Row]:
    """All manifest rows, not just ones whose URL happens to end in '.pdf'.

    A URL-suffix check misses real PDFs: IBM's manifest URL
    (ibm.com/downloads/cas/POBY5B69) has no extension at all, and Saviynt's
    carries a trailing '?hsLang=en' query string after '.pdf'. Both were
    silently skipped by the first version of this check -- caught only by
    noticing 4 PDF rows selected when the manifest has 6. Content-type,
    checked after fetching, is the only signal that can't be fooled this way
    -- same approach ingest_partner_products.py already uses to tell a PDF
    from an HTML page."""
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = _csv.DictReader(fh)
        raw = list(reader)
    rows = [Row(vendor=(r.get("vendor") or "").strip(),
               product_name=(r.get("product_name") or "").strip(),
               source_url=(r.get("source_url") or "").strip()) for r in raw]
    log.info("manifest: %d row(s) total (PDF vs HTML decided per-row by "
             "content-type after fetching, not by URL)", len(rows))
    return rows


def fetch(url: str, timeout: int = 30) -> tuple[bytes, str]:
    """Return (content, content_type)."""
    resp = requests.get(
        url, timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ShilpiCorpusBot/1.0)"},
    )
    resp.raise_for_status()
    return resp.content, resp.headers.get("content-type", "").lower()


def extract_images(pdf_bytes: bytes) -> list[bytes]:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf_bytes))
    images: list[bytes] = []
    for page in reader.pages:
        try:
            for img in page.images:
                images.append(img.data)
        except Exception:
            pass
    return images


def ocr_tesseract(img_bytes: bytes) -> str:
    """Best-effort OCR. Returns '' if tesseract is not installed or fails --
    this is the cheap pass, OCR is a nice-to-have signal for classification,
    never a requirement."""
    import subprocess
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        if max(img.size) > 2000:
            img.thumbnail((2000, 2000))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        result = subprocess.run(
            ["tesseract", "-", "-", "-l", "eng", "--psm", "6"],
            input=buf.getvalue(), capture_output=True, timeout=30,
        )
        text = result.stdout.decode("utf-8", errors="ignore").strip()
        if len(text) < 30 or len(text.split()) < 5:
            return ""
        return text
    except Exception:
        return ""


def classify_asset(size: int, width: int, height: int, ocr: str, vendor: str) -> str:
    """Heuristics only -- no vision description available in this pass.

    Same ordering logic as extract_visual_assets.classify_asset: small marks
    first (most confidently classifiable), then OCR-text-heavy wide images as
    product screenshots. Anything left over is 'unknown' rather than guessed
    into 'architecture' -- without a vision description there is no real
    signal that it is a diagram rather than, say, a table screenshot, and
    'architecture' carries the strongest reuse restriction, so misclassifying
    INTO it is the safer direction to guess.
    """
    if size < 60_000 and max(width, height) < 600:
        return "corporate"
    if vendor and vendor.split()[0].lower() in ocr.lower() and len(ocr.split()) > 20:
        return "product"
    if ocr and len(ocr.split()) >= 25 and width > height:
        return "product"
    return "unknown"


def sb_headers(extra: Optional[dict] = None) -> dict:
    h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    if extra:
        h.update(extra)
    return h


def lookup_product_id(source_url: str) -> Optional[str]:
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/partner_products", headers=sb_headers(),
        params={"select": "id", "source_url": f"eq.{source_url}",
               "org_id": f"eq.{ORG_ID}"}, timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json() or []
    return rows[0]["id"] if rows else None


def upload(storage_path: str, blob: bytes, mime: str) -> None:
    resp = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{storage_path}",
        headers=sb_headers({"Content-Type": mime, "x-upsert": "true"}),
        data=blob, timeout=120,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"upload failed HTTP {resp.status_code}: {resp.text[:200]}")


def insert_asset(row: dict) -> bool:
    """Returns False on a duplicate (409) -- the same vendor logo appearing
    in several of that vendor's documents is the normal case."""
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/partner_product_assets",
        headers=sb_headers({"Content-Type": "application/json",
                            "Prefer": "return=minimal"}),
        json=row, timeout=60,
    )
    if resp.status_code in (200, 201, 204):
        return True
    if resp.status_code == 409:
        return False
    raise RuntimeError(f"insert failed HTTP {resp.status_code}: {resp.text[:200]}")


def process_row(row: Row, dry_run: bool) -> dict:
    log.info("=" * 70)
    log.info("Fetching: %s (%s / %s)", row.source_url, row.vendor, row.product_name)
    try:
        content, content_type = fetch(row.source_url)
    except Exception as e:
        log.error("FETCH FAILED: %s", e)
        return {"source_url": row.source_url, "error": str(e)}

    is_pdf = "application/pdf" in content_type or row.source_url.lower().split("?")[0].endswith(".pdf")
    if not is_pdf:
        log.info("  content-type=%s -- not a PDF, skipping (HTML image "
                 "extraction is a separate, lower-value pass)", content_type)
        return {"source_url": row.source_url, "skipped": "not a pdf"}

    images = extract_images(content)
    log.info("  %d embedded image object(s) in the PDF", len(images))

    from PIL import Image
    kept = skipped_small = skipped_dup = failed_decode = 0
    kinds: dict[str, int] = {}
    product_id = None if dry_run else lookup_product_id(row.source_url)
    if not dry_run and not product_id:
        log.warning("  no partner_products row found for this URL -- was it "
                   "ingested by ingest_partner_products.py? Skipping.")
        return {"source_url": row.source_url,
               "error": "no matching partner_products row (ingest text first)"}

    # In-run dedup by content hash. Measured on PingOne Advanced Services:
    # 8,280 embedded image OBJECTS, 31 genuinely DISTINCT images -- one logo
    # repeated 2,760 times, several page-header/footer graphics repeated 184
    # times (once per page, 184 pages). Deduping here, before decode/OCR,
    # turns a wasteful 8,280-iteration loop into the 31 that matter, rather
    # than relying only on the database's unique-hash index to clean it up
    # after the fact.
    seen_hashes: set[str] = set()
    stored = 0
    for i, img_bytes in enumerate(images):
        if len(img_bytes) < MIN_IMAGE_BYTES:
            skipped_small += 1
            continue
        content_hash = hashlib.sha256(img_bytes).hexdigest()
        if content_hash in seen_hashes:
            skipped_dup += 1
            continue
        seen_hashes.add(content_hash)
        try:
            im = Image.open(io.BytesIO(img_bytes))
            width, height = im.size
            fmt = (im.format or "PNG").lower()
        except Exception:
            failed_decode += 1
            continue

        ocr = ocr_tesseract(img_bytes)
        kind = classify_asset(len(img_bytes), width, height, ocr, row.vendor)
        kinds[kind] = kinds.get(kind, 0) + 1
        kept += 1

        if dry_run:
            continue

        mime = f"image/{fmt}" if f"image/{fmt}" in BUCKET_MIME_TYPES else "image/png"
        if mime == "image/png" and fmt != "png":
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="PNG")
            img_bytes = buf.getvalue()
        storage_path = f"{ORG_ID}/{content_hash}.{mime.split('/')[1]}"
        try:
            upload(storage_path, img_bytes, mime)
            ok = insert_asset({
                "org_id": ORG_ID, "product_id": product_id,
                "storage_path": storage_path, "content_hash": content_hash,
                "mime_type": mime, "width": width, "height": height,
                "size_bytes": len(img_bytes), "source_url": row.source_url,
                "asset_kind": kind, "ocr_text": ocr or None,
            })
            if ok:
                stored += 1
        except Exception as e:
            log.warning("  image %d failed to store: %s", i, e)

    log.info("  kept=%d skipped_small=%d skipped_dup=%d failed_decode=%d kinds=%s%s",
             kept, skipped_small, skipped_dup, failed_decode, kinds,
             f" stored={stored}" if not dry_run else " [DRY RUN, nothing stored]")
    return {"source_url": row.source_url, "vendor": row.vendor,
           "images_found": len(images), "kept": kept, "kinds": kinds,
           "stored": stored if not dry_run else None, "dry_run": dry_run}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.dry_run:
        missing = [k for k in ("SUPABASE_URL", "SUPABASE_KEY", "ORG_ID") if not os.getenv(k)]
        if missing:
            log.error("Missing env vars: %s", ", ".join(missing))
            sys.exit(1)

    rows = load_all_rows(Path(args.manifest))
    results = [process_row(r, args.dry_run) for r in rows]

    total_kept = sum(r.get("kept", 0) for r in results)
    total_stored = sum(r.get("stored") or 0 for r in results)
    log.info("=" * 70)
    log.info("DONE. %d PDF(s) processed, %d image(s) kept, %d stored%s",
             len(rows), total_kept, total_stored, " [DRY RUN]" if args.dry_run else "")
    Path("out_partner_images").mkdir(exist_ok=True)
    with open("out_partner_images/run_summary.json", "w") as fh:
        json.dump(results, fh, indent=2)


if __name__ == "__main__":
    main()
