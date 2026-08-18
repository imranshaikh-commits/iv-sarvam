#!/usr/bin/env python3
"""Backfill `proposal_chunks.section_topic` — the semantic section label.

WHY
---
`section_type` is 46% 'other' (5,144 of 11,286 chunks). Across 114 proposals it
finds 34 why_vendor chunks and 16 company_profile chunks, yet every IV proposal
has both. Metadata-filtered retrieval is impossible against labels that poor:
filtering "Why SailPoint" to why_vendor would search 34 chunks and miss the
hundreds that exist unlabelled.

RULES, NOT A MODEL
------------------
The heading distribution inside 'other' is highly concentrated:

      956 distinct headings
       55 headings cover 50% of the chunks
      218 headings cover 80%

and the headings are clean: "Inspirit Vision Overview", "RACI Matrix",
"Out of Scope", "Inspirit Vision Success Stories", "Documentation and Knowledge
Transfer". The old classifier had twelve patterns; it was under-specified, not
defeated by ambiguity. Rules are deterministic, free, instant and re-runnable,
which matters when reclassifying 11,286 rows.

A model pass over whatever residue remains is a later decision, taken against
the measured residue rather than assumed up front.

NON-DESTRUCTIVE
---------------
Writes a NEW column. `section_type` keeps the structural labels (table,
diagram, page, ocr) that the retrieval scorecard uses to track fragment share.
A table inside a Commercial section is both a table and commercial; two columns
express that, one cannot. Re-running is safe.

USAGE
    python3 scripts/classify_sections.py --dry-run     # report, write nothing
    python3 scripts/classify_sections.py               # backfill
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

import requests

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
PAGE = 1000

# ---------------------------------------------------------------------------
# Taxonomy.
#
# Deliberately aligned to the TEMPLATE SUBSECTIONS in proposal_templates.py, so
# retrieval can filter to the same topic the drafter is writing. A label with no
# corresponding template section earns nothing.
#
# ORDER MATTERS: first match wins, so specific patterns precede general ones.
# "Migration to Test" is a deployment stage, not a platform migration, and must
# not be caught by the migration rule -- hence the explicit negative below.
# ---------------------------------------------------------------------------
HEADING_RULES: list[tuple[str, str]] = [
    # --- IV's own material: the static, bank-sourced sections ---
    ("company_profile", r"inspirit\s*vision\s*(overview|services|profile)|"
                        r"company\s*(profile|overview)|about\s+(us|inspirit)|"
                        r"employee\s*skill|our\s*team|workforce|"
                        r"branch\s*location|service\s*office|"
                        r"project\s*delivery\s*expertise|delivery\s*capability|"
                        r"certifications?\s*(and|&)?\s*partnerships?"),
    ("similar_experience", r"success\s*stor|case\s*stud|customer\s*reference|"
                           r"similar\s*(experience|project|engagement)|"
                           r"relevant\s*(experience|engagement)|our\s*clients"),
    ("why_vendor", r"^why\s|why\s+(sailpoint|ping|forgerock|okta|ibm|oracle|"
                   r"cyberark|beyondtrust|saviynt|keycloak)|"
                   r"best[- ]in[- ]class|differentiat|value\s*proposition|"
                   r"vendor\s*(positioning|selection)|leader\s*quadrant"),

    # --- scope and understanding ---
    ("scope", r"out\s*of\s*scope|in\s*scope|scope\s*of\s*(work|services|supply)|"
              r"understanding\s*(of|the)|business\s*(objective|driver|requirement)|"
              r"functional\s*requirement|current\s*state|as[- ]is\s"),

    # --- solution and design ---
    ("architecture", r"architect|deployment\s*(model|diagram|topology)|"
                     r"solution\s*(overview|design|component)|topolog|"
                     r"high\s*level\s*design|low\s*level\s*design|\bhld\b|\blld\b|"
                     r"target\s*state|future\s*state|reference\s*model"),
    ("sizing", r"sizing|hardware\s*(requirement|specification)|volumetric|"
               r"capacity|infrastructure\s*requirement|server\s*specification|"
               r"environment\s*(sizing|specification)"),
    ("integration", r"integrat|connector|onboard|provisioning|de[- ]?provisioning|"
                    r"joiner|mover|leaver|\bjml\b|lifecycle\s*(management|event)|"
                    r"authoritative\s*source|hrms|active\s*directory|\bldap\b|"
                    r"\bsso\b|federation|single\s*sign"),
    ("governance", r"certification\s*campaign|access\s*(review|certification|request)|"
                   r"attestation|segregation\s*of\s*duties|\bsod\b|"
                   r"role\s*(model|mining|based)|\brbac\b|entitlement|"
                   r"policy\s*(model|violation)|audit\s*(trail|report)|compliance"),
    ("security", r"security\s*(architecture|requirement|control|consideration)|"
                 r"encryption|\btls\b|hardening|vulnerab|penetration|"
                 r"privileged\s*access|\bpam\b|secrets?\s*management"),

    # --- migration-specific. AFTER integration so "Migration to Test" (a
    #     deployment stage, 56 chunks) is not mistaken for a platform migration.
    ("migration", r"(?<!to\s)migration\s*(strategy|approach|plan|wave|pattern)|"
                  r"cutover|rollback|fallback|coexistence|parallel\s*run|"
                  r"decommission|legacy\s*(platform|system)\s*retire|"
                  r"upgrade\s*(path|approach|strategy)"),

    # --- delivery ---
    ("testing", r"^testing|unit\s*test|user\s*acceptance|\buat\b|"
                r"test\s*(plan|strategy|case|scenario)|quality\s*assurance|"
                r"validation\s*(approach|strategy)"),
    ("timeline", r"timeline|schedule|project\s*(plan|phase|initiation)|"
                 r"tranche|milestone|implementation\s*(plan|approach|phase)|"
                 r"rollout|go[- ]live|phase\s*\d|week\s*\d"),
    ("raci", r"\braci\b|responsibilit|accountab|roles?\s*(and|&)\s*responsibilit|"
             r"client\s*responsibilit|ownership\s*matrix"),
    ("project_management", r"project\s*(management|governance|communication|"
                           r"reporting|control|weekly|status)|"
                           r"weekly\s*report|reporting\s*deck|"
                           r"escalation|change\s*(management|"
                           r"request|control)|risk\s*(management|register|log)|"
                           r"issue\s*(management|log)|status\s*report|steering|"
                           r"risks?\s*(and|&)\s*mitigation|mitigation\s*plan"),
    ("team_roles", r"^(project\s*manager|technical\s*lead|developer|architect|"
                   r"consultant|engineer|analyst|resident\s*engineer)$|"
                   r"team\s*(structure|composition)|resource\s*(plan|profile)|"
                   r"\bcv\b|resume|profile\s*of"),
    ("knowledge_transfer", r"knowledge\s*transfer|\bkt\b|training|documentation|"
                           r"handover|hand[- ]over|enablement|"
                           r"post[- ]?(production|go[- ]live)\s*support|hypercare"),
    ("support", r"support\s*(model|service|contract|tier|level)|"
                r"\bsla\b|service\s*level|managed\s*service|\bmss\b|"
                r"\bl1\b|\bl2\b|\bl3\b|helpdesk|service\s*desk|incident"),

    # --- commercial and boundaries ---
    ("pricing", r"pricing|commercial|cost|price|\bboq\b|bill\s*of\s*quantit|"
                r"payment\s*(milestone|term|schedule)|licen[cs]e\s*(fee|cost|boq)|"
                r"investment|effort\s*estimat|rate\s*card"),
    ("deliverables", r"deliverable|artefact|artifact|document\s*list|"
                     r"high\s*level\s*task|task\s*(list|breakdown)|\bwbs\b|"
                     r"benefits?$|value\s*delivered|outcomes?$"),
    ("delivery_process", r"build\s*(and|&)?\s*infrastructure|logistics|"
                         r"environment\s*(build|setup|provisioning)|"
                         r"installation|configuration\s*process|"
                         r"deployment\s*process|migration\s*to\s*(test|uat|prod)"),
    ("assumptions", r"assumption|dependenc|prerequisite|constraint|exclusion|"
                    r"terms?\s*(and|&)\s*conditions?|caveat"),

    # --- front matter ---
    ("executive_summary", r"executive\s*summary|introduction|overview$|"
                          r"purpose\s*of\s*(this|the)\s*document|"
                          r"cover\s*letter|introductory\s*letter"),
    ("toc", r"table\s*of\s*contents|contents$|document\s*(control|history|"
            r"revision)|version\s*history|distribution\s*list"),
]

_COMPILED = [(topic, re.compile(pat, re.I)) for topic, pat in HEADING_RULES]

# ---------------------------------------------------------------------------
# Body fallback, applied only when the heading yields nothing.
#
# Requires MULTIPLE distinctive signals rather than one keyword: a single
# mention of "SLA" in a scope section should not relabel it as support. Each
# entry is (topic, [required signals], minimum matches).
# ---------------------------------------------------------------------------
BODY_RULES: list[tuple[str, list[str], int]] = [
    ("sizing", [r"\bcpu\b", r"\bram\b|\bmemory\b", r"\bgb\b|\btb\b",
                r"server|node|instance"], 3),
    ("raci", [r"\bresponsible\b", r"\baccountable\b", r"\bconsulted\b",
              r"\binformed\b"], 3),
    ("pricing", [r"\bboq\b|bill of quantit", r"quantity|qty", r"unit\s*(price|cost)",
                 r"total|subtotal|amount"], 3),
    # Payment-milestone tables read "Milestone | Trigger | Percentage", where
    # the heading regex fails because it needs the phrase "payment milestone"
    # adjacent. Column signatures are the reliable signal for tables.
    ("pricing", [r"milestone", r"percent|%|\btrigger\b",
                 r"payment|invoice|licen[cs]e|amount"], 3),
    ("timeline", [r"week\s*\d|month\s*\d", r"phase|tranche|milestone",
                  r"duration|start|end|complete"], 3),
    ("governance", [r"certification|attestation", r"reviewer|manager|owner",
                    r"campaign|entitlement"], 3),
    ("migration", [r"migrat|cutover", r"legacy|incumbent|existing platform",
                   r"rollback|fallback|parallel"], 2),
]

_BODY_COMPILED = [(t, [re.compile(p, re.I) for p in pats], n)
                  for t, pats, n in BODY_RULES]

UNCLASSIFIED = "unclassified"


# A heading carrying no semantic signal at all. 4,530 chunks are headed
# "Table 1", "Diagram #5", "Page 26" -- the extractor's own numbering, not
# IV's words. For these the body has to do all the work.
_GENERIC_HEADING_RE = re.compile(
    r"^\s*(table|diagram|figure|image|page|exhibit|annex)\s*#?\s*\d*\s*$", re.I)

# Minimum distinct pattern matches in the body before a topic is accepted.
# One keyword is noise; two independent signals is evidence.
_BODY_SCORE_MIN = 2


def _score_body(text: str) -> str:
    """Best topic by running the HEADING patterns over the body text.

    Reuses the same vocabulary rather than inventing a second one, so a topic
    is described in exactly one place. Requires at least _BODY_SCORE_MIN
    distinct matches, and requires a clear winner: when the top two topics tie,
    the content genuinely is ambiguous and stays unclassified rather than being
    assigned by list order.
    """
    body = (text or "")[:2000]
    if not body.strip():
        return UNCLASSIFIED
    scores: dict[str, int] = {}
    for topic, pattern in _COMPILED:
        n = len(pattern.findall(body))
        if n:
            scores[topic] = scores.get(topic, 0) + n
    if not scores:
        return UNCLASSIFIED
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top, top_n = ranked[0]
    if top_n < _BODY_SCORE_MIN:
        return UNCLASSIFIED
    if len(ranked) > 1 and ranked[1][1] == top_n:
        return UNCLASSIFIED
    return top


def classify(heading: str, text: str, section_type: str | None = None) -> str:
    """Semantic topic for a chunk.

    Order: high-precision body rules -> heading -> body scoring. The body rules
    come FIRST because a sizing or RACI table is unmistakable from its columns
    and may sit under a heading like "Proposed Solution" that would otherwise
    win and lose the detail.
    """
    body = (text or "")[:2000]

    # 1. High-precision structural signatures (column headers, R/A/C/I cells).
    for topic, patterns, need in _BODY_COMPILED:
        if sum(1 for p in patterns if p.search(body)) >= need:
            return topic

    # 2. The heading, unless it is the extractor's own numbering.
    head = re.sub(r"\s*\((?:part|cont\.?)\s*\d+\)\s*$", "",
                  (heading or "").strip(), flags=re.I)
    if head and not _GENERIC_HEADING_RE.match(head):
        for topic, pattern in _COMPILED:
            if pattern.search(head):
                return topic

    # 3. Body scoring. This is what reaches the 4,530 chunks headed "Table 1"
    #    and "Diagram #5", where the heading is the extractor's numbering and
    #    carries no meaning. Without it the residue was 60% -- WORSE than the
    #    46% 'other' this replaces.
    return _score_body(body)


def fetch_all() -> list[dict]:
    rows, offset = [], 0
    while True:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/proposal_chunks",
            headers={"apikey": SUPABASE_KEY,
                     "Authorization": f"Bearer {SUPABASE_KEY}",
                     "Range": f"{offset}-{offset + PAGE - 1}"},
            params={"select": "id,heading,text,section_type"},
            timeout=120,
        )
        resp.raise_for_status()
        batch = resp.json() or []
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


def write_topics(updates: list[tuple[str, str]], batch: int = 200) -> None:
    """PATCH in batches. Only section_topic is touched; section_type is left
    alone so the structural labels and the fragment metric survive."""
    for i in range(0, len(updates), batch):
        for chunk_id, topic in updates[i:i + batch]:
            resp = requests.patch(
                f"{SUPABASE_URL}/rest/v1/proposal_chunks",
                headers={"apikey": SUPABASE_KEY,
                         "Authorization": f"Bearer {SUPABASE_KEY}",
                         "Content-Type": "application/json",
                         "Prefer": "return=minimal"},
                params={"id": f"eq.{chunk_id}"},
                json={"section_topic": topic},
                timeout=60,
            )
            if resp.status_code not in (200, 204):
                raise RuntimeError(
                    f"update failed for {chunk_id}: HTTP {resp.status_code} {resp.text}")
        print(f"  wrote {min(i + batch, len(updates))}/{len(updates)}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="Report the classification without writing anything")
    ap.add_argument("--show-residue", type=int, default=25,
                    help="How many unclassified headings to print")
    args = ap.parse_args()

    if not (SUPABASE_URL and SUPABASE_KEY):
        print("Missing SUPABASE_URL / SUPABASE_KEY", file=sys.stderr)
        return 1

    rows = fetch_all()
    print(f"fetched {len(rows)} chunks\n", file=sys.stderr)

    before = Counter(r.get("section_type") or "null" for r in rows)
    topics, updates, residue = Counter(), [], Counter()
    for r in rows:
        topic = classify(r.get("heading"), r.get("text"), r.get("section_type"))
        topics[topic] += 1
        if topic == UNCLASSIFIED:
            residue[(r.get("heading") or "(none)")[:60]] += 1
        updates.append((r["id"], topic))

    total = len(rows)
    print("=== BEFORE (section_type, structural) ===")
    for k, v in before.most_common():
        print(f"  {k:22s} {v:6d}  {100*v/total:5.1f}%")

    print("\n=== AFTER (section_topic, semantic) ===")
    for k, v in topics.most_common():
        print(f"  {k:22s} {v:6d}  {100*v/total:5.1f}%")

    unclassified = topics[UNCLASSIFIED]
    old_other = before.get("other", 0)
    print(f"\n  section_type 'other'    {old_other:6d}  {100*old_other/total:5.1f}%")
    print(f"  section_topic residue   {unclassified:6d}  {100*unclassified/total:5.1f}%")

    if residue:
        print(f"\n=== TOP UNCLASSIFIED HEADINGS (residue) ===")
        for head, n in residue.most_common(args.show_residue):
            print(f"  {n:5d}  {head}")

    if args.dry_run:
        print("\nDRY RUN: nothing written.", file=sys.stderr)
        return 0

    print(f"\nwriting {len(updates)} labels...", file=sys.stderr)
    write_topics(updates)
    print("done.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
