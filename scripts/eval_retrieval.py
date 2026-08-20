#!/usr/bin/env python3
"""Retrieval scorecard for the Shilpi corpus.

WHY THIS EXISTS
---------------
The corpus grew from 11 proposals (1,413 chunks) to 114 (11,286). Every planned
improvement to retrieval -- metadata pre-filtering, recency weighting, hybrid
BM25, reranking -- is a guess until it is measured.

That caution is not theoretical. A 2026 controlled comparison of five retrieval
strategies found that increased complexity did NOT reliably improve results at
this corpus scale: hybrid BM25+dense and multi-query expansion both finished
BELOW plain dense vector search, with multi-query posting the lowest precision
of any strategy. Only cross-encoder reranking clearly won. Our corpus is a
narrow domain at a similar scale, which is exactly the regime where added
sophistication backfired.

So: measure first, change second, measure again. A change that does not move
this scorecard gets reverted.

WHAT IT MEASURES
----------------
Per probe query, over the top-k results:

  proposals       distinct proposals represented (higher = broader evidence)
  max_from_one    hits from the single most-represented proposal (lower = better;
                  before the dedup fix one proposal could supply 7 of 10)
  type_match      share whose proposal_type matches the query's intent
  vendor_match    share whose iam_vendor matches, when the probe names one
  fragment_pct    share that are table/figure fragments ("Table 6 (part 44)"),
                  which are weak evidence for drafting prose
  recent_pct      share from 2024 or later (the bank reaches back to 2022 and
                  old product versions get cited as current)
  mean_sim        mean similarity

No single number is the score. A change that raises `proposals` while collapsing
`type_match` has not helped.

USAGE
-----
    python3 scripts/eval_retrieval.py --baseline           # write baseline
    python3 scripts/eval_retrieval.py --compare baseline.json

Needs OPENROUTER_API_KEY, SUPABASE_URL, SUPABASE_KEY.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone

import requests

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
EMBED_MODEL = "openai/text-embedding-3-small"  # must match ingest_v2.py
DEFAULT_TOP_K = 8
RECENT_FROM_YEAR = 2024

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")


# ---------------------------------------------------------------------------
# The probes.
#
# Drawn from REAL template subsections rather than invented questions, so the
# scorecard measures what drafting actually asks for. `expect_type` and
# `expect_vendor` encode what a good answer looks like; `section` records which
# template subsection the probe stands in for.
# ---------------------------------------------------------------------------
PROBES = [
    # --- implementation: the sections most often drafted ---
    {"id": "sizing_prod", "expect_topic": "sizing", "section": "Proposed Production Hardware Sizing",
     "query": "production hardware sizing CPU cores memory RAM storage application servers database server",
     "expect_type": "implementation"},
    {"id": "sizing_dr", "expect_topic": "sizing", "section": "Proposed DR Hardware Sizing",
     "query": "disaster recovery environment sizing replication between primary and standby data centre",
     "expect_type": "implementation"},
    {"id": "raci", "expect_topic": "raci", "section": "RACI Matrix",
     "query": "RACI matrix responsible accountable consulted informed activity workstream client vendor",
     "expect_type": None},
    {"id": "why_vendor_sp", "expect_topic": "why_vendor", "section": "Why SailPoint",
     "query": "why SailPoint IdentityIQ leading identity governance platform analyst position advantages",
     "expect_type": None, "expect_vendor": "SailPoint"},
    {"id": "why_vendor_ping", "expect_topic": "why_vendor", "section": "Why Ping",
     "query": "why Ping Identity advantages customer identity access management platform",
     "expect_type": None, "expect_vendor": "Ping"},
    {"id": "cert_campaigns", "expect_topic": "governance", "section": "Access Certification",
     "query": "access certification campaigns manager application owner entitlement owner attestation review",
     "expect_type": None},
    {"id": "provisioning", "expect_topic": "integration", "section": "Provisioning and Lifecycle Management",
     "query": "joiner mover leaver automated provisioning deprovisioning lifecycle events birthright roles",
     "expect_type": None},
    {"id": "sod", "expect_topic": "governance", "section": "Segregation of Duties",
     "query": "segregation of duties SoD policy violations detective preventive controls",
     "expect_type": None},
    {"id": "connectors", "expect_topic": "integration", "section": "Connectors and Integrations",
     "query": "connector coverage target systems Active Directory LDAP database SaaS application onboarding",
     "expect_type": None},
    {"id": "company_profile", "expect_topic": "company_profile", "section": "Company Profile",
     "query": "Inspirit Vision company profile offices workforce certified consultants delivery capability",
     "expect_type": None},
    {"id": "similar_exp", "expect_topic": "similar_experience", "section": "Similar Experience",
     "query": "similar experience customer references case studies comparable engagements sector",
     "expect_type": None},
    {"id": "timeline", "expect_topic": "timeline", "section": "High-Level Implementation Plan",
     "query": "implementation timeline phases duration weeks tranche milestones project plan",
     "expect_type": None},
    {"id": "kt", "expect_topic": "knowledge_transfer", "section": "Knowledge Transfer Plan",
     "query": "knowledge transfer plan administrator training handover support team hypercare",
     "expect_type": None},
    {"id": "boq", "expect_topic": "pricing", "section": "Licence Bill of Quantities",
     "query": "licence bill of quantities line items quantity unit basis commercial structure",
     "expect_type": None},
    {"id": "milestones", "expect_topic": "pricing", "section": "Payment Milestones",
     "query": "payment milestones trigger percentage licence delivery implementation completion",
     "expect_type": None},
    # --- migration: the newest proposal type, 39 proposals, never tested ---
    {"id": "mig_strategy", "expect_topic": "migration", "section": "Migration Pattern",
     "query": "migration approach phased cutover parallel run coexistence between old and new identity platform",
     "expect_type": "migration"},
    {"id": "mig_credentials", "expect_topic": "migration", "section": "Identity and Credential Migration",
     "query": "migrating user credentials password hashes re-enrolment during platform migration",
     "expect_type": "migration"},
    {"id": "mig_rollback", "expect_topic": "migration", "section": "Rollback Position",
     "query": "rollback plan fallback to incumbent platform if cutover fails business continuity",
     "expect_type": "migration"},
    {"id": "mig_decommission", "expect_topic": "migration", "section": "Legacy Platform Decommissioning",
     "query": "decommissioning legacy identity platform data retention archival licence retirement",
     "expect_type": "migration"},
    # --- mss ---
    {"id": "mss_sla", "expect_topic": "support", "section": "Support Model and SLA",
     "query": "managed services support model L1 L2 L3 severity levels response resolution SLA",
     "expect_type": "mss"},
]


def embed(text: str) -> list[float]:
    resp = requests.post(
        f"{OPENROUTER_BASE}/embeddings",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                 "Content-Type": "application/json"},
        json={"model": EMBED_MODEL, "input": text},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def retrieve(embedding: list[float], k: int,
             proposal_type: str | None = None,
             section_topic: str | None = None) -> list[dict]:
    """Call the same RPC the brain calls, WITH the same arguments.

    `proposal_type` matters: since sarvam_008 the brain passes the type of the
    proposal being drafted, and retrieval reserves a share of the slots for
    matching-type chunks. A harness that omitted it would score a version of
    retrieval nobody uses, and would have reported no gain from a change
    measured at 1-of-8 to 5-of-8 on migration content.
    """
    payload = {"query_embedding": json.dumps(embedding, separators=(",", ":")),
               "match_count": k}
    if proposal_type:
        payload["filter_proposal_type"] = proposal_type
    # Since sarvam_010 retrieval also reserves slots for the SECTION TOPIC being
    # drafted. Omitting it here would score a version of retrieval nobody uses.
    if section_topic:
        payload["filter_section_topic"] = section_topic
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/rpc/match_proposal_chunks",
        headers={"apikey": SUPABASE_KEY,
                 "Authorization": f"Bearer {SUPABASE_KEY}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json() or []


def proposal_metadata() -> dict[str, dict]:
    """proposal_id -> {proposal_type, year}. One call, reused by every probe."""
    out: dict[str, dict] = {}
    offset = 0
    while True:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/proposals",
            headers={"apikey": SUPABASE_KEY,
                     "Authorization": f"Bearer {SUPABASE_KEY}",
                     "Range": f"{offset}-{offset + 999}"},
            params={"select": "id,proposal_type,year,client_name"},
            timeout=60,
        )
        resp.raise_for_status()
        rows = resp.json() or []
        for r in rows:
            out[r["id"]] = r
        if len(rows) < 1000:
            break
        offset += 1000
    return out


def _is_fragment(heading: str) -> bool:
    """A slice of a table or figure, e.g. 'Table 6 (part 44)'.

    These retrieve as headerless fragments and are weak evidence for prose.
    Tracked because 24% of the corpus looks like this.
    """
    h = (heading or "").strip().lower()
    return h.startswith(("table", "figure", "image", "page")) and "part" in h


def score_probe(probe: dict, rows: list[dict], meta: dict[str, dict]) -> dict:
    if not rows:
        return {"id": probe["id"], "section": probe["section"], "hits": 0,
                "proposals": 0, "max_from_one": 0, "type_match": 0.0,
                "vendor_match": None, "fragment_pct": 0.0, "recent_pct": 0.0,
                "mean_sim": 0.0, "top_clients": []}

    by_proposal: dict[str, int] = {}
    for r in rows:
        by_proposal[r["proposal_id"]] = by_proposal.get(r["proposal_id"], 0) + 1

    n = len(rows)
    want_type = probe.get("expect_type")
    type_match = (
        sum(1 for r in rows
            if meta.get(r["proposal_id"], {}).get("proposal_type") == want_type) / n
        if want_type else None
    )

    want_vendor = (probe.get("expect_vendor") or "").lower()
    vendor_match = (
        sum(1 for r in rows if want_vendor in (r.get("iam_vendor") or "").lower()) / n
        if want_vendor else None
    )

    want_topic = probe.get("expect_topic")
    topic_match = (
        sum(1 for r in rows if r.get("section_topic") == want_topic) / n
        if want_topic else None
    )

    years = [meta.get(r["proposal_id"], {}).get("year") for r in rows]
    recent = sum(1 for y in years if y and int(y) >= RECENT_FROM_YEAR) / n

    return {
        "id": probe["id"],
        "section": probe["section"],
        "hits": n,
        "proposals": len(by_proposal),
        "max_from_one": max(by_proposal.values()),
        "type_match": round(type_match, 3) if type_match is not None else None,
        "topic_match": round(topic_match, 3) if topic_match is not None else None,
        "vendor_match": round(vendor_match, 3) if vendor_match is not None else None,
        "fragment_pct": round(sum(1 for r in rows if _is_fragment(r.get("heading"))) / n, 3),
        "recent_pct": round(recent, 3),
        "mean_sim": round(statistics.mean(float(r.get("similarity") or 0) for r in rows), 3),
        "top_clients": sorted({(r.get("client_name") or "?")[:28] for r in rows})[:5],
    }


def run(k: int, no_type_filter: bool = False) -> dict:
    meta = proposal_metadata()
    print(f"corpus: {len(meta)} proposals\n", file=sys.stderr)
    results = []
    for probe in PROBES:
        # Mirror the brain: pass the proposal type when the probe stands for a
        # section of a typed proposal. `--no-type-filter` reproduces the old
        # behaviour so the two can be compared directly.
        ptype = None if no_type_filter else probe.get("expect_type")
        rows = retrieve(embed(probe["query"]), k, proposal_type=ptype,
                        section_topic=probe.get("expect_topic"))
        s = score_probe(probe, rows, meta)
        results.append(s)
        print(f"  {s['id']:18s} props={s['proposals']:2d} maxone={s['max_from_one']:2d} "
              f"type={s['type_match']} topic={s['topic_match']} "
              f"frag={s['fragment_pct']:.2f} "
              f"recent={s['recent_pct']:.2f} sim={s['mean_sim']:.3f}", file=sys.stderr)

    def avg(field):
        vals = [r[field] for r in results if r.get(field) is not None]
        return round(statistics.mean(vals), 3) if vals else None

    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "top_k": k,
        "type_filter": not no_type_filter,
        "corpus_proposals": len(meta),
        "totals": {
            "mean_proposals_per_probe": avg("proposals"),
            "mean_max_from_one": avg("max_from_one"),
            "mean_type_match": avg("type_match"),
            "mean_topic_match": avg("topic_match"),
            "mean_vendor_match": avg("vendor_match"),
            "mean_fragment_pct": avg("fragment_pct"),
            "mean_recent_pct": avg("recent_pct"),
            "mean_similarity": avg("mean_sim"),
        },
        "probes": results,
    }


def compare(baseline: dict, current: dict) -> int:
    """Print a before/after table. Returns 1 if anything regressed materially.

    Direction matters per metric: more distinct proposals is better, fewer hits
    from one proposal is better, fewer fragments is better.
    """
    print(f"\n{'metric':28s} {'baseline':>10s} {'current':>10s} {'delta':>10s}")
    print("-" * 62)
    higher_better = {"mean_proposals_per_probe", "mean_type_match", "mean_topic_match",
                     "mean_vendor_match", "mean_recent_pct", "mean_similarity"}
    lower_better = {"mean_max_from_one", "mean_fragment_pct"}
    regressed = []
    for key, base in (baseline.get("totals") or {}).items():
        cur = (current.get("totals") or {}).get(key)
        if base is None or cur is None:
            continue
        delta = round(cur - base, 3)
        flag = ""
        if key in higher_better and delta < -0.05:
            flag, _ = " WORSE", regressed.append(key)
        elif key in lower_better and delta > 0.05:
            flag, _ = " WORSE", regressed.append(key)
        elif (key in higher_better and delta > 0.05) or (key in lower_better and delta < -0.05):
            flag = " better"
        print(f"{key:28s} {base:>10} {cur:>10} {delta:>+10}{flag}")

    print("\nper-probe distinct proposals:")
    base_probes = {p["id"]: p for p in baseline.get("probes", [])}
    for p in current.get("probes", []):
        b = base_probes.get(p["id"])
        if not b:
            continue
        d = p["proposals"] - b["proposals"]
        mark = "  <-- dropped" if d < 0 else ""
        print(f"  {p['id']:18s} {b['proposals']:2d} -> {p['proposals']:2d}{mark}")

    if regressed:
        print(f"\nREGRESSION in: {', '.join(regressed)}")
        print("The change should be reverted unless there is a measured reason to keep it.")
        return 1
    print("\nNo regression detected.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--k", type=int, default=DEFAULT_TOP_K)
    ap.add_argument("--out", default="retrieval_scorecard.json")
    ap.add_argument("--baseline", action="store_true",
                    help="Write the result as the baseline to compare against later")
    ap.add_argument("--compare", metavar="BASELINE_JSON",
                    help="Compare this run against a saved baseline")
    ap.add_argument("--no-type-filter", action="store_true",
                    help="Do not pass proposal_type (pre-sarvam_008 behaviour). "
                         "Use to isolate what the type reservation contributes.")
    args = ap.parse_args()

    missing = [k for k in ("OPENROUTER_API_KEY", "SUPABASE_URL", "SUPABASE_KEY")
               if not os.getenv(k)]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 1

    result = run(args.k, no_type_filter=args.no_type_filter)
    out = "retrieval_baseline.json" if args.baseline else args.out
    with open(out, "w") as fh:
        json.dump(result, fh, indent=2)
    print(f"\nwritten: {out}", file=sys.stderr)

    t = result["totals"]
    print("\n=== SCORECARD ===")
    for key, val in t.items():
        print(f"  {key:28s} {val}")

    if args.compare:
        with open(args.compare) as fh:
            return compare(json.load(fh), result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
