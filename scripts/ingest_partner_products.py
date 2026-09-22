#!/usr/bin/env python3
"""
Shilpi Partner Product Corpus Ingestion — Sprint 7

Mirrors scripts/ingest_v2.py's discipline for a different source shape:
ingest_v2.py reads local files selected by a human-reviewed manifest;
this reads PUBLIC URLS selected by a human-reviewed manifest. Same
principle either way -- selection comes from the manifest, never a
crawl, and the manifest's own values are never re-guessed.

For each manifest row (a public datasheet/architecture guide/solution
brief/analyst report):
  1. Fetch the URL
  2. Extract text -- pypdf for a PDF, a stdlib-only tag-strip for HTML
     (no new dependency for what a few lines of html.parser can do)
  3. Chunk (same ~350-word target, 40-word overlap as the proposal corpus)
  4. Embed each chunk with openai/text-embedding-3-small (MUST match
     app.py's EMBED_MODEL, or retrieval compares vectors from two
     different embedding spaces)
  5. Write to Supabase (partner_products + partner_product_chunks)

Config via env vars:
  OPENROUTER_API_KEY  — required (not needed for --dry-run)
  SUPABASE_URL        — required (not needed for --dry-run)
  SUPABASE_KEY        — required (not needed for --dry-run)
  ORG_ID              — required (not needed for --dry-run)

Usage:
  python ingest_partner_products.py --manifest partner_corpus_manifest.csv --dry-run
  python ingest_partner_products.py --manifest partner_corpus_manifest.csv
  python ingest_partner_products.py --manifest partner_corpus_manifest.csv --vendor "Ping Identity"
"""

from __future__ import annotations

import argparse
import csv as _csv
import hashlib
import html.parser
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ingest_partner")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
ORG_ID = os.getenv("ORG_ID", "")

# Must match app.py's EMBED_MODEL exactly, or query embeddings and corpus
# embeddings live in different vector spaces and cosine similarity is
# meaningless -- confirmed against app.py:71 before writing this file.
EMBED_MODEL = "openai/text-embedding-3-small"

CHUNK_TARGET_WORDS = 350
CHUNK_OVERLAP_WORDS = 40

MANIFEST_REQUIRED_COLUMNS = {
    "vendor", "product_name", "capability", "doc_type", "source_url",
}
VALID_DOC_TYPES = {
    "datasheet", "architecture_guide", "admin_guide", "analyst_report",
    "solution_brief", "other",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    heading: str
    text: str
    word_count: int


@dataclass
class ManifestRow:
    vendor: str
    product_name: str
    capability: str
    doc_type: str
    source_url: str
    published_year: Optional[int] = None
    notes: str = ""


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def load_manifest(csv_path: Path, vendor: Optional[str] = None) -> list[ManifestRow]:
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = _csv.DictReader(fh)
        missing = MANIFEST_REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(
                f"manifest {csv_path} is missing required columns: "
                f"{', '.join(sorted(missing))}")
        raw = list(reader)

    rows: list[ManifestRow] = []
    for r in raw:
        doc_type = (r.get("doc_type") or "").strip()
        if doc_type not in VALID_DOC_TYPES:
            raise SystemExit(
                f"manifest row has doc_type={doc_type!r}, not one of "
                f"{sorted(VALID_DOC_TYPES)} -- this must match the "
                f"partner_products CHECK constraint or every insert for "
                f"this row will fail: {r.get('source_url')}")
        v = (r.get("vendor") or "").strip()
        if vendor and vendor.lower() != v.lower():
            continue
        year_raw = (r.get("published_year") or "").strip()
        rows.append(ManifestRow(
            vendor=v,
            product_name=(r.get("product_name") or "").strip(),
            capability=(r.get("capability") or "").strip(),
            doc_type=doc_type,
            source_url=(r.get("source_url") or "").strip(),
            published_year=int(year_raw) if year_raw.isdigit() else None,
            notes=(r.get("notes") or "").strip(),
        ))
    log.info("manifest: %d row(s)%s", len(rows),
             f" (vendor filter: {vendor})" if vendor else "")
    return rows


# ---------------------------------------------------------------------------
# Fetch + extract
# ---------------------------------------------------------------------------

class _TextExtractor(html.parser.HTMLParser):
    """Stdlib-only HTML-to-text. Not a real reader-mode extractor -- it keeps
    every visible text node in document order and drops <script>/<style>.
    Good enough for a marketing page; the manifest already flags which rows
    are landing pages worth a closer human look rather than a clean PDF."""

    _SKIP_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self.parts.append(stripped)


def html_to_text(raw_html: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw_html)
    text = " ".join(parser.parts)
    return re.sub(r"\s+", " ", text).strip()


def fetch_and_extract(url: str, timeout: int = 30) -> tuple[str, str]:
    """Return (extracted_text, content_type). Raises on a genuine fetch
    failure -- a 404 or timeout must stop this row, not silently produce
    an empty chunk that looks like real content."""
    resp = requests.get(
        url, timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ShilpiCorpusBot/1.0)"},
    )
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "").lower()

    if "application/pdf" in content_type or url.lower().endswith(".pdf"):
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(resp.content))
        pages = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t.strip():
                pages.append(t.strip())
        return "\n\n".join(pages), "pdf"

    # Treat everything else as HTML. A PDF URL that actually serves an HTML
    # interstitial (seen during manifest-building, e.g. bot-check pages)
    # falls through here too -- extracted text will be short, and the
    # word-count check below refuses to chunk near-nothing rather than
    # writing a near-empty row that LOOKS like a real document.
    return html_to_text(resp.text), "html"


# ---------------------------------------------------------------------------
# Chunking (identical target/overlap to ingest_v2.py, for consistent
# retrieval behaviour across both corpora)
# ---------------------------------------------------------------------------

def chunk_text(text: str, heading: str, target_words: int = CHUNK_TARGET_WORDS,
               overlap: int = CHUNK_OVERLAP_WORDS) -> list[Chunk]:
    words = text.split()
    if not words:
        return []
    if len(words) <= target_words:
        return [Chunk(heading=heading, text=text, word_count=len(words))]

    chunks: list[Chunk] = []
    start = 0
    part = 0
    while start < len(words):
        end = min(start + target_words, len(words))
        chunk_words = words[start:end]
        chunks.append(Chunk(
            heading=f"{heading} (part {part + 1})",
            text=" ".join(chunk_words),
            word_count=len(chunk_words),
        ))
        if end == len(words):
            break
        start = end - overlap
        part += 1
    return chunks


# ---------------------------------------------------------------------------
# Embedding — identical to ingest_v2.py's embed_texts
# ---------------------------------------------------------------------------

def embed_texts(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{OPENROUTER_BASE}/embeddings",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={"model": EMBED_MODEL, "input": batch},
                    timeout=60,
                )
                if resp.status_code == 200:
                    data = resp.json()["data"]
                    all_embeddings.extend([item["embedding"] for item in data])
                    break
                log.warning("Embed batch %d HTTP %d (attempt %d): %s",
                            i // batch_size, resp.status_code, attempt + 1,
                            resp.text[:200])
                time.sleep(2 * (attempt + 1))
            except Exception as e:
                log.warning("Embed batch %d error (attempt %d): %s",
                            i // batch_size, attempt + 1, e)
                time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(f"Failed to embed batch {i // batch_size} after 3 attempts")
    return all_embeddings


# ---------------------------------------------------------------------------
# Supabase writes
# ---------------------------------------------------------------------------

def sb_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def already_ingested(hashes: list[str]) -> set[str]:
    """source_sha256 values already in partner_products for this org.
    Without this, re-running the same manifest inserts a second copy of
    every product and doubles its weight in retrieval -- same failure
    class the proposal corpus hit before sarvam_006 added this check."""
    if not hashes:
        return set()
    seen: set[str] = set()
    for i in range(0, len(hashes), 50):
        batch = hashes[i:i + 50]
        quoted = ",".join(f'"{h}"' for h in batch)
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/partner_products",
            headers=sb_headers(),
            params={"select": "source_sha256", "org_id": f"eq.{ORG_ID}",
                    "source_sha256": f"in.({quoted})"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"dedup check failed HTTP {resp.status_code}: {resp.text}. "
                "Refusing to continue -- ingesting without it risks duplicates.")
        seen.update(r["source_sha256"] for r in resp.json() if r.get("source_sha256"))
    return seen


def sb_insert_product(row: ManifestRow, source_sha256: str,
                      reviewed_by: str) -> str:
    payload = {
        "org_id": ORG_ID,
        "vendor": row.vendor,
        "product_name": row.product_name,
        "capability": row.capability or None,
        "doc_type": row.doc_type,
        "source_url": row.source_url,
        "source_filename": None,
        "source_sha256": source_sha256,
        "published_year": row.published_year,
        "reviewed": True,
        "reviewed_by": reviewed_by,
        "reviewed_at": "now()",
    }
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/partner_products",
        headers=sb_headers(), json=payload, timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Insert partner_products failed HTTP {resp.status_code}: {resp.text}")
    return resp.json()[0]["id"]


def sb_insert_chunks(product_id: str, chunks_with_embeddings: list[tuple[Chunk, list[float]]]) -> int:
    rows = []
    for chunk, emb in chunks_with_embeddings:
        rows.append({
            "product_id": product_id,
            "org_id": ORG_ID,
            "heading": chunk.heading[:500],
            "text": chunk.text,
            "word_count": chunk.word_count,
            "embedding": emb,
        })
    inserted = 0
    for i in range(0, len(rows), 50):
        batch = rows[i:i + 50]
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/partner_product_chunks",
            headers=sb_headers(), json=batch, timeout=60,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Insert partner_product_chunks failed HTTP {resp.status_code}: {resp.text[:500]}")
        inserted += len(batch)
    return inserted


# ---------------------------------------------------------------------------
# Per-row pipeline
# ---------------------------------------------------------------------------

MIN_WORDS_TO_INGEST = 40  # below this, treat as a fetch that got nothing real


def process_row(row: ManifestRow, output_dir: Path, reviewed_by: str,
                dry_run: bool = False) -> Optional[dict]:
    log.info("=" * 70)
    log.info("Fetching: %s (%s / %s)", row.source_url, row.vendor, row.product_name)
    t0 = time.time()

    try:
        text, kind = fetch_and_extract(row.source_url)
    except Exception as e:
        log.error("FETCH FAILED %s: %s", row.source_url, e)
        return {"source_url": row.source_url, "error": str(e)}

    word_count = len(text.split())
    log.info("  Extracted %d words as %s in %.1fs", word_count, kind, time.time() - t0)
    if word_count < MIN_WORDS_TO_INGEST:
        log.warning("  SKIPPED: only %d words -- likely a bot-check page, a "
                    "gated form, or an empty PDF text layer. Not ingesting "
                    "%d words as if it were real content.", word_count, word_count)
        return {"source_url": row.source_url,
               "error": f"only {word_count} words extracted, below the {MIN_WORDS_TO_INGEST}-word floor"}

    heading = f"{row.vendor} — {row.product_name}"
    chunks = chunk_text(text, heading)
    log.info("  Chunked into %d piece(s)", len(chunks))

    source_sha256 = hashlib.sha256(row.source_url.encode("utf-8")).hexdigest()

    stage_dir = output_dir / hashlib.sha256(row.source_url.encode()).hexdigest()[:16]
    stage_dir.mkdir(parents=True, exist_ok=True)
    with open(stage_dir / "staged.json", "w", encoding="utf-8") as f:
        json.dump({
            "row": asdict(row), "source_sha256": source_sha256,
            "word_count": word_count, "chunks": [asdict(c) for c in chunks],
        }, f, indent=2, ensure_ascii=False)

    if dry_run:
        log.info("  DRY RUN: %d chunks prepared, nothing written to Supabase",
                 len(chunks))
        return {"source_url": row.source_url, "vendor": row.vendor,
               "product_name": row.product_name, "chunks": len(chunks),
               "word_count": word_count, "dry_run": True}

    log.info("  Embedding %d chunk(s) via OpenRouter...", len(chunks))
    embeddings = embed_texts([c.text for c in chunks])

    log.info("  Writing to Supabase...")
    product_id = sb_insert_product(row, source_sha256, reviewed_by)
    inserted = sb_insert_chunks(product_id, list(zip(chunks, embeddings)))
    log.info("  product_id = %s, inserted %d chunk(s)", product_id, inserted)
    return {"source_url": row.source_url, "vendor": row.vendor,
           "product_name": row.product_name, "product_id": product_id,
           "chunks": inserted, "word_count": word_count}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Ingest the Sprint 7 partner-product manifest into "
                    "partner_products / partner_product_chunks. Selection "
                    "comes from the manifest CSV, never a crawl.")
    ap.add_argument("--manifest", required=True, help="Reviewed manifest CSV")
    ap.add_argument("--vendor", help="Only this vendor, for batched ingestion")
    ap.add_argument("--reviewed-by", default="imran",
                    help="Recorded on every row as who approved it (default: imran)")
    ap.add_argument("--output", default="./out_partner", help="Staging output directory")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch, extract, chunk and report. No embedding calls, no writes.")
    args = ap.parse_args()

    required = [] if args.dry_run else [
        "OPENROUTER_API_KEY", "SUPABASE_URL", "SUPABASE_KEY", "ORG_ID"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        log.error("Missing env vars: %s", ", ".join(missing))
        sys.exit(1)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = load_manifest(Path(args.manifest), vendor=args.vendor)

    if not args.dry_run:
        hashes = [hashlib.sha256(r.source_url.encode("utf-8")).hexdigest() for r in rows]
        seen = already_ingested(hashes)
        if seen:
            before = len(rows)
            rows = [r for r in rows
                   if hashlib.sha256(r.source_url.encode("utf-8")).hexdigest() not in seen]
            log.info("dedup: %d of %d already ingested, skipping them",
                     before - len(rows), before)

    log.info("Processing %d row(s)%s", len(rows), " [DRY RUN]" if args.dry_run else "")

    results, failures = [], []
    for row in rows:
        try:
            r = process_row(row, output_dir, args.reviewed_by, dry_run=args.dry_run)
            if r and "error" not in r:
                results.append(r)
            elif r:
                failures.append(r)
        except Exception as e:  # noqa: BLE001 - one bad row must not end the batch
            log.exception("FAILED on %s: %s", row.source_url, e)
            failures.append({"source_url": row.source_url, "error": str(e)})

    summary = {"processed": len(results), "failed": len(failures),
              "dry_run": args.dry_run, "vendor_filter": args.vendor,
              "results": results, "failures": failures}
    with open(output_dir / "run_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2)
    log.info("=" * 70)
    log.info("DONE. %d processed, %d failed. Summary: %s",
             len(results), len(failures), output_dir / "run_summary.json")
    if failures:
        log.warning("Failures:")
        for f in failures:
            log.warning("  %s -- %s", f.get("source_url"), f.get("error"))


if __name__ == "__main__":
    main()
