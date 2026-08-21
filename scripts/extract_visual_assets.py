#!/usr/bin/env python3
"""Populate `visual_assets` from the proposal bank. No API calls, no re-ingestion.

WHY
---
IV's human Amlak proposal carries 37 images; Shilpi has produced 6 in every run
since run 3. The ingestion pipeline already does the expensive half of the work:
it extracts every embedded image, OCRs the text-heavy ones and sends the
diagram-like ones to a vision model. It then folds the DESCRIPTION into a text
chunk and DISCARDS THE BYTES. The corpus holds 1,849 'diagram' chunks describing
pictures nobody can retrieve.

This recovers the bytes without paying for any of that again.

WHY THE DESCRIPTIONS CAN BE RECOVERED
-------------------------------------
`process_images` names its chunks "Diagram #5" and "Image OCR #3", where the
number IS the 1-based index into the extracted image list. Extraction order is
deterministic (docx: document.related_parts; pdf: page order), so re-extracting
locally with the same traversal reproduces the same indices and the existing
descriptions map back exactly. Not a guess -- a join key that was already there.

ASSET KINDS
-----------
Rules, not a model. The three kinds carry different reuse rules and the signals
that separate them are structural:

  corporate     small, appears in many proposals (logos, brand marks), or sits
                under a company/experience heading. THE MOST REUSABLE and the
                largest share of IV's image count.
  product       vendor UI screenshots. Wide, has OCR text, vendor named nearby.
                Reusable across proposals for the same vendor.
  architecture  has a vision description, large, diagram-like. Drawn for a
                SPECIFIC client estate -- rarely reusable, and embedding one in
                another client's document would leak their topology.

APPROVAL
--------
Everything is written approved = FALSE. Nothing reaches a client document until
a human says so, the same discipline as the per-diagram approval gate.

USAGE
    python3 scripts/extract_visual_assets.py --manifest ... --root ... --limit 1 --dry-run
    python3 scripts/extract_visual_assets.py --manifest ... --root ...
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import requests

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
ORG_ID = os.getenv("ORG_ID", "")
BUCKET = os.getenv("SHILPI_ASSET_BUCKET", "visual-assets")

# Below this an image is a bullet, rule or spacer, not an asset. Matches
# MIN_IMAGE_BYTES in ingest_v2.py so indices line up with the stored chunks.
MIN_IMAGE_BYTES = 3000
MAX_IMAGE_BYTES = 10 * 1024 * 1024   # bucket limit

# The bucket allowlist. An ANB PDF carried a JPEG 2000 image, which the storage
# API rejects with HTTP 415 -- and that killed the entire run 21 proposals in.
# Two separate mistakes: not checking the format before uploading, and letting
# one bad image end the batch. Exotic formats are converted to PNG where Pillow
# can read them, and skipped with a count where it cannot.
BUCKET_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp"}

_CHUNK_INDEX_RE = re.compile(r"#\s*(\d+)")


def sb_headers(extra: dict | None = None) -> dict:
    h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    if extra:
        h.update(extra)
    return h


def extract_images(path: Path) -> list[bytes]:
    """Images in the SAME ORDER ingest_v2.py extracted them.

    Any change to this traversal breaks the index join with the stored chunk
    descriptions, so it deliberately mirrors ingest_v2.extract_docx/extract_pdf.
    """
    images: list[bytes] = []
    if path.suffix.lower() == ".docx":
        from docx import Document
        doc = Document(str(path))
        for _rel_id, rel in doc.part.related_parts.items():
            if "image" in rel.content_type:
                try:
                    images.append(rel.blob)
                except Exception:
                    pass
    elif path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        for page in reader.pages:
            try:
                for img in page.images:
                    images.append(img.data)
            except Exception:
                pass
    return images


def chunk_descriptions(proposal_id: str) -> dict[int, dict]:
    """Existing image chunks for a proposal, keyed by the index in the heading.

    'Diagram #5' -> 5. This is how the vision descriptions we already paid for
    are reattached to their bytes.
    """
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/proposal_chunks",
        headers=sb_headers(),
        params={"select": "heading,text,section_type,section_topic",
                "proposal_id": f"eq.{proposal_id}",
                "section_type": "in.(diagram,ocr)"},
        timeout=60,
    )
    resp.raise_for_status()
    out: dict[int, dict] = {}
    for row in resp.json() or []:
        m = _CHUNK_INDEX_RE.search(row.get("heading") or "")
        if m:
            out[int(m.group(1))] = row
    return out


def nearest_heading(proposal_id: str) -> str | None:
    """A representative section heading, used when an image has no better one."""
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/proposal_chunks", headers=sb_headers(),
        params={"select": "heading", "proposal_id": f"eq.{proposal_id}",
                "section_type": "eq.other", "limit": "1"}, timeout=30)
    rows = resp.json() if resp.status_code == 200 else []
    return (rows[0]["heading"] if rows else None)


def classify_asset(size: int, width: int, height: int, desc: str | None,
                   ocr: str | None, vendor: str | None, heading: str | None) -> str:
    """Which of the three reuse categories this image falls into.

    Order matters: corporate is checked first because a logo is small and
    unmistakable, and misfiling one as architecture would make the most
    reusable asset in the bank unusable.
    """
    head = (heading or "").lower()
    text = f"{ocr or ''} {desc or ''}".lower()

    # Small marks: logos, icons, brand furniture. Highly reusable.
    if size < 60_000 and max(width, height) < 600:
        return "corporate"
    if any(k in head for k in ("profile", "about", "success stor", "case stud",
                               "experience", "our team", "overview")):
        return "corporate"

    # Vendor UI: wide screenshots with readable interface text.
    if vendor and vendor.split()[0].lower() in text and ocr and len(ocr.split()) > 20:
        return "product"
    # A UI screenshot is wide and carries a lot of readable interface text.
    # 25 words is roughly a toolbar plus a column header row -- lower than it
    # sounds, because OCR on a screenshot yields fragments, not sentences.
    if ocr and len(ocr.split()) >= 25 and width > height:
        return "product"

    # Diagram-like with a vision description: drawn for a specific estate.
    if desc:
        return "architecture"
    return "unknown"


def upload(storage_path: str, blob: bytes, mime: str) -> None:
    resp = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{storage_path}",
        headers=sb_headers({"Content-Type": mime, "x-upsert": "true"}),
        data=blob, timeout=120,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"upload failed HTTP {resp.status_code}: {resp.text[:200]}")


def insert_asset(row: dict) -> bool:
    """Insert one asset. Returns False when it is a duplicate (409), which is
    the normal case for logos appearing in every proposal."""
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/visual_assets",
        headers=sb_headers({"Content-Type": "application/json",
                            "Prefer": "return=minimal"}),
        json=row, timeout=60)
    if resp.status_code in (200, 201, 204):
        return True
    if resp.status_code == 409:
        return False
    raise RuntimeError(f"insert failed HTTP {resp.status_code}: {resp.text[:200]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    missing = [k for k in ("SUPABASE_URL", "SUPABASE_KEY", "ORG_ID") if not os.getenv(k)]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 1

    from PIL import Image

    # Map source_filename -> proposal_id, so assets attach to the right proposal.
    resp = requests.get(f"{SUPABASE_URL}/rest/v1/proposals", headers=sb_headers(),
                        params={"select": "id,source_filename,iam_vendor,client_name"},
                        timeout=60)
    resp.raise_for_status()
    by_file = {r["source_filename"]: r for r in resp.json() or []}
    print(f"{len(by_file)} proposals in the corpus", file=sys.stderr)

    import csv
    root = Path(os.path.expanduser(args.root))
    with open(os.path.expanduser(args.manifest), newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("tier") or "").strip() == "ingest"]
    if args.limit:
        rows = rows[:args.limit]

    kinds, stored, dupes, skipped, no_proposal = Counter(), 0, 0, 0, 0
    converted, failed = 0, 0
    log_skip: list[str] = []
    for r in rows:
        fname = r["filename"]
        prop = by_file.get(fname)
        if not prop:
            no_proposal += 1
            continue
        path = root / r["path"]
        if not path.exists():
            continue

        images = extract_images(path)
        # Fetch descriptions in DRY RUN TOO. Skipping them made `desc` always
        # None, so the architecture rule never fired and the preview reported
        # 91 of 102 images as 'unknown' -- a classification that cannot occur in
        # the real run. A dry run that does not exercise the real inputs is
        # worse than no dry run: it reports a failure that is not real, or hides
        # one that is. These are reads; they cost nothing and write nothing.
        descs = chunk_descriptions(prop["id"])
        heading_fallback = None

        for idx, blob in enumerate(images, start=1):
            if len(blob) < MIN_IMAGE_BYTES or len(blob) > MAX_IMAGE_BYTES:
                skipped += 1
                continue
            try:
                im = Image.open(io.BytesIO(blob))
                width, height = im.size
                mime = Image.MIME.get(im.format, "image/png")
                ext = (im.format or "PNG").lower().replace("jpeg", "jpg")
                if mime not in BUCKET_MIME_TYPES:
                    # Re-encode rather than discard: a JPEG 2000 diagram is
                    # still a usable diagram.
                    buf = io.BytesIO()
                    im.convert("RGB").save(buf, format="PNG")
                    blob = buf.getvalue()
                    mime, ext = "image/png", "png"
                    converted += 1
            except Exception as e:
                log_skip.append(f"{fname}#{idx}: {type(e).__name__}")
                skipped += 1
                continue

            chunk = descs.get(idx) or {}
            body = chunk.get("text") or ""
            desc = body if chunk.get("section_type") == "diagram" else None
            ocr = body if chunk.get("section_type") == "ocr" else None
            heading = chunk.get("heading")
            if heading and _CHUNK_INDEX_RE.search(heading):
                # "Diagram #5" is the extractor's numbering, not a real heading.
                if heading_fallback is None:
                    heading_fallback = nearest_heading(prop["id"])
                heading = heading_fallback

            kind = classify_asset(len(blob), width, height, desc, ocr,
                                  prop.get("iam_vendor"), heading)
            kinds[kind] += 1

            digest = hashlib.sha256(blob).hexdigest()
            storage_path = f"{digest[:2]}/{digest}.{ext}"
            if args.dry_run:
                continue

            try:
                upload(storage_path, blob, mime)
            except Exception as e:
                # One unreadable image must not end a batch of 112 proposals.
                log_skip.append(f"{fname}#{idx}: upload {e}")
                failed += 1
                continue
            ok = insert_asset({
                "org_id": ORG_ID, "proposal_id": prop["id"],
                "storage_path": storage_path, "content_hash": digest,
                "mime_type": mime, "width": width, "height": height,
                "size_bytes": len(blob),
                "section_heading": heading, "page_order": idx,
                "asset_kind": kind,
                "vision_description": (desc or "")[:8000] or None,
                "ocr_text": (ocr or "")[:8000] or None,
                "approved": False,
            })
            stored += 1 if ok else 0
            dupes += 0 if ok else 1

        print(f"  {fname[:58]:60s} {len(images):3d} images", file=sys.stderr)

    print("\n=== ASSET KINDS ===")
    for k, v in kinds.most_common():
        print(f"  {k:14s} {v:5d}")
    print(f"\n  stored {stored}, duplicates skipped {dupes}, "
          f"converted to PNG {converted}, too small/unreadable {skipped}, "
          f"upload failures {failed}, no matching proposal {no_proposal}")
    if log_skip:
        print(f"\n  first 10 skips/failures:")
        for line in log_skip[:10]:
            print(f"    {line}")
    if args.dry_run:
        print("\nDRY RUN: nothing uploaded or written.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
