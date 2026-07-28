#!/usr/bin/env python3
"""
corpus_manifest.py — inventory and CLASSIFY the Sales-SoWs Drive bank before
anything is ingested into the RAG corpus.

WHY THIS EXISTS
---------------
`Sales-SoWs` is not a proposal bank. It is a sales working folder, and only part
of it is IV-authored proposal prose. Ingesting it wholesale would actively damage
retrieval quality:

  * `RFP/` holds CLIENT-authored documents. Ingested into the proposal bank they
    would be retrieved and cited as if they were IV's own past work — Sarvam
    would ground IV's voice in the client's words.
  * `Questionaires/`, `Product Comparison/` are vendor/marketing material, not
    IV positioning.
  * `Efforts/` holds internal estimates and likely commercial detail.
  * `Deck/`, `PPT/`, `Demo/` are a different genre; slide fragments retrieved
    into prose sections read as noise.

So this script does NOT ingest. It walks a local mirror of the Drive folder,
derives metadata from the path, assigns each file to a tier, and writes a
manifest CSV for a human to review. Ingestion then runs off the approved
manifest — never off a directory walk.

USAGE
-----
1. Mirror the Drive folder locally. Either Google Drive for Desktop, or:

       rclone copy "gdrive:Sales-SoWs" ./Sales-SoWs --drive-shared-with-me -P

2. Build the manifest:

       python3 scripts/corpus_manifest.py ./Sales-SoWs -o corpus_manifest.csv

3. Review `corpus_manifest.csv`. Correct the `tier`, `iam_vendor` and
   `client_name` columns by hand where the heuristics guessed wrong — the path
   is a good signal, not a perfect one.

4. Ingest only rows where `tier == ingest`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import sys
from collections import Counter
from pathlib import Path

# --- tiers ------------------------------------------------------------------
TIER_INGEST = "ingest"            # IV-authored proposal / SOW prose
TIER_REFERENCE = "reference"      # IV-authored, but a different genre
TIER_TESTSET = "testset"          # client-authored RFPs -> pilot scoring, NOT the bank
TIER_EXCLUDE = "exclude"          # everything that would pollute retrieval

# Top-level folders whose content is NOT proposal prose. Keyed by the folder
# name as it appears in Sales-SoWs.
FOLDER_TIERS: dict[str, str] = {
    "rfp": TIER_TESTSET,
    "questionaires": TIER_EXCLUDE,
    "questionnaires": TIER_EXCLUDE,
    "product comparison": TIER_EXCLUDE,
    "deck": TIER_EXCLUDE,
    "ppt": TIER_EXCLUDE,
    "demo": TIER_EXCLUDE,
    "ai": TIER_EXCLUDE,
    "partner_enablement_program_2025_26": TIER_EXCLUDE,
    "efforts": TIER_EXCLUDE,
    "case study": TIER_REFERENCE,
    "assessment": TIER_REFERENCE,
}

# Known IAM vendors, matched against the path.
VENDORS = {
    "sailpoint": "SailPoint",
    "ping": "Ping Identity",
    "pingidentity": "Ping Identity",
    "forgerock": "ForgeRock",
    "ibm": "IBM",
    "okta": "Okta",
    "oracle": "Oracle",
    "one identity": "One Identity",
    "beyondtrust": "BeyondTrust",
    "keycloak": "Red Hat Keycloak",
    "rhbk": "Red Hat Keycloak",
    "cyberark": "CyberArk",
    "entra": "Microsoft Entra",
    "azure ad": "Microsoft Entra",
}

# Folders that name a theme rather than a vendor or a client.
THEME_FOLDERS = {
    "mss", "wiam", "ciam", "ksa", "mix", "other_sows", "case study", "assessment",
    "rfp", "deck", "ppt", "demo", "ai", "efforts", "questionaires", "questionnaires",
    "product comparison", "partner_enablement_program_2025_26",
}

INGESTIBLE_EXT = {".docx", ".doc", ".pdf", ".rtf", ".odt"}
SKIP_EXT = {".pptx", ".ppt", ".xlsx", ".xls", ".csv", ".png", ".jpg", ".jpeg",
            ".gif", ".zip", ".msg", ".eml", ".txt", ".md"}

PROPOSAL_HINTS = ("proposal", "sow", "statement of work", "technical", "response",
                  "solution", "offer", "quotation")
RFP_HINTS = ("rfp", "rfi", "rfq", "tender", "itt", "eoi")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def derive_vendor(parts: list[str]) -> str:
    joined = norm(" / ".join(parts))
    for needle, label in VENDORS.items():
        if needle in joined:
            return label
    return ""


def derive_client(parts: list[str]) -> str:
    """The client is usually the deepest folder that isn't a vendor or theme."""
    for part in reversed(parts):
        low = norm(part)
        if not low or low in THEME_FOLDERS:
            continue
        if any(v in low for v in VENDORS):
            continue
        return part.strip()
    return ""


def classify(rel_parts: list[str], filename: str) -> tuple[str, str]:
    """Return (tier, reason)."""
    ext = Path(filename).suffix.lower()
    top = norm(rel_parts[0]) if rel_parts else ""
    low_name = norm(filename)

    if ext in SKIP_EXT:
        return TIER_EXCLUDE, f"non-prose file type ({ext})"
    if ext not in INGESTIBLE_EXT:
        return TIER_EXCLUDE, f"unsupported file type ({ext or 'none'})"

    # A client-authored RFP anywhere is a test-set candidate, never bank content.
    if any(h in low_name for h in RFP_HINTS):
        return TIER_TESTSET, "looks client-authored (RFP/RFI/tender)"

    if top in FOLDER_TIERS:
        return FOLDER_TIERS[top], f"top-level folder '{rel_parts[0]}'"

    if any(h in low_name for h in PROPOSAL_HINTS):
        return TIER_INGEST, "IV-authored proposal/SOW"

    # Inside a vendor or client folder, a document is probably proposal content —
    # but flag it so a human confirms rather than assuming.
    return TIER_INGEST, "in a vendor/client folder (VERIFY)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="local mirror of the Sales-SoWs folder")
    ap.add_argument("-o", "--out", default="corpus_manifest.csv")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    rows, seen_hashes = [], {}
    tier_counts, vendor_counts, dupes = Counter(), Counter(), 0

    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            if fn.startswith((".", "~$")):
                continue
            full = Path(dirpath) / fn
            try:
                size = full.stat().st_size
            except OSError:
                continue

            rel = full.relative_to(root)
            parts = list(rel.parts[:-1])
            tier, reason = classify(parts, fn)

            # Content hash for duplicate detection. The winner is chosen in a
            # SECOND pass — first-seen-wins would keep whichever copy sorts
            # first alphabetically and drop the better-filed original.
            digest = ""
            if tier in (TIER_INGEST, TIER_REFERENCE) and size:
                h = hashlib.sha256()
                try:
                    with open(full, "rb") as fh:
                        for block in iter(lambda: fh.read(1 << 20), b""):
                            h.update(block)
                    digest = h.hexdigest()[:16]
                except OSError:
                    pass

            vendor = derive_vendor(parts)
            rows.append({
                "path": str(rel),
                "filename": fn,
                "tier": tier,
                "reason": reason,
                "iam_vendor": vendor,
                "client_name": derive_client(parts),
                "top_folder": parts[0] if parts else "",
                "size_kb": round(size / 1024),
                "sha256_16": digest,
            })

    # --- second pass: resolve duplicates ------------------------------------
    # The same proposal often sits in several folders; ingesting every copy
    # double-weights it in retrieval. Keep the best-filed copy: one that sits in
    # a vendor folder, then the deepest path (vendor/client/... is more
    # informative than a loose drop), then the longest filename.
    by_digest: dict[str, list[dict]] = {}
    for r in rows:
        if r["sha256_16"]:
            by_digest.setdefault(r["sha256_16"], []).append(r)

    def keep_rank(r: dict) -> tuple:
        return (
            0 if r["iam_vendor"] else 1,          # vendor-identified wins
            -r["path"].count(os.sep),             # deeper path wins
            -len(r["filename"]),                  # more descriptive name wins
            r["path"],
        )

    for digest, group in by_digest.items():
        if len(group) < 2:
            continue
        group.sort(key=keep_rank)
        keeper = group[0]
        for r in group[1:]:
            r["tier"] = TIER_EXCLUDE
            r["reason"] = f"duplicate of {keeper['path']}"
            dupes += 1

    tier_counts = Counter(r["tier"] for r in rows)
    vendor_counts = Counter(r["iam_vendor"] or "(unknown)"
                            for r in rows if r["tier"] == TIER_INGEST)

    rows.sort(key=lambda r: (r["tier"] != TIER_INGEST, r["path"]))
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else
                           ["path", "filename", "tier", "reason", "iam_vendor",
                            "client_name", "top_folder", "size_kb", "sha256_16"])
        w.writeheader()
        w.writerows(rows)

    print(f"\nscanned {len(rows)} files under {root}")
    print(f"manifest -> {args.out}\n")
    print("by tier:")
    for tier in (TIER_INGEST, TIER_REFERENCE, TIER_TESTSET, TIER_EXCLUDE):
        print(f"  {tier:<10} {tier_counts[tier]:>4}")
    if dupes:
        print(f"\n  ({dupes} exact duplicates demoted to exclude)")
    print("\ningest candidates by vendor:")
    for vendor, n in vendor_counts.most_common():
        print(f"  {vendor:<20} {n:>4}")
    print("\nNEXT: review the CSV by hand. Rows marked '(VERIFY)' are guesses.")
    print("Ingest ONLY rows where tier == ingest.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
