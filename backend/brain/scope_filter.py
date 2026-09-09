"""Decide which sections an engagement actually warrants.

WHY
---
Every proposal was drafted with every section in its template, whatever the
discovery answers said. Measured on the first migration proposal (Bank BTPN,
ForgeRock 6.5.x -> 7.3, a scoped upgrade of three lower environments) against
what IV actually wrote for the same deal:

                    IV wrote    Shilpi produced
    prose words        2,642              6,141    2.3x
    table words          962              2,703    2.8x
    top-level sections     8                 15    1.9x
    subsections           19                 49    2.6x

Shilpi's BTPN output (6,141 words) was almost exactly the size of IV's AMLAK
proposal (6,648) -- a 42-week greenfield SailPoint build. The system produced
one size of document regardless of engagement.

Worse than the length, three sections directly contradicted the answers given:

  * Decommissioning and Transition -- nothing is being decommissioned; it is a
    version upgrade in place.
  * Knowledge Transfer and Training -- the consultant answered "out of scope,
    BTPN's responsibility". We wrote a section and a 7x4 table.
  * Commercial -- licence, pricing, payment milestones and taxes were all
    skipped. We wrote three tables of "To be confirmed".

A section the client's own answers rule out is worse than a missing section: it
tells a reviewer the document was not read.

WHAT THIS DOES NOT DO
---------------------
It does not guess. A section is dropped only when the discovery answers give a
POSITIVE reason -- an explicit exclusion, or every field the section depends on
left empty. Silence about a section is not evidence against it, so anything
ambiguous is kept. The failure mode to avoid is dropping a section the client
wanted, which is far more damaging than one extra section.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

log = logging.getLogger("shilpi-brain.scope")

# Sections that must never be dropped, whatever discovery says. These carry the
# proposal's identity: who IV is, what is being proposed, what it costs to
# deliver. A proposal without them is not a proposal.
ALWAYS_KEEP = frozenset({
    "executive_summary", "company_profile", "scope_understanding",
    "current_state", "proposed_solution", "solution_overview",
    "target_state", "migration_strategy",
    "implementation_approach", "delivery_approach",
    "assumptions_responsibilities",
})

# Section id -> the discovery fields it is built from. When EVERY one is empty
# or skipped, the section has nothing to say and drafting it produces filler.
SECTION_EVIDENCE: dict[str, tuple[str, ...]] = {
    "commercial": ("license_included", "pricing_model", "payment_milestones",
                   "taxes", "travel", "support_terms", "currency"),
    "knowledge_transfer": ("training", "kt", "hypercare", "support_model",
                           "post_sla", "postgolive_reporting_cadence"),
    "similar_experience": ("case_studies_to_highlight", "case_studies_include",
                           "similar_projects", "partner_positioning"),
    "project_timeline": ("duration", "timeline_milestones", "go_live_date",
                         "delivery_phases", "delivery_milestones"),
    "decommissioning": ("data_retention", "out_of_scope", "existing_iam_platform"),
    "rollback_risk": ("ha_dr_requirements", "rto_rpo", "availability",
                      "dependencies", "assumptions"),
}

# Phrases in `out_of_scope` (or a section's own fields) that positively exclude
# a section. Matched against the answer text, not inferred.
SECTION_EXCLUSIONS: dict[str, tuple[str, ...]] = {
    "knowledge_transfer": (r"training", r"knowledge transfer", r"\bkt\b",
                           r"enablement", r"end[- ]user"),
    "decommissioning": (r"decommission", r"retire", r"legacy .*(retire|removal)"),
    "commercial": (r"commercial", r"pricing", r"licen[cs]e (cost|fee)"),
}

_EMPTY = {"", "skip", "n/a", "na", "none", "not applicable", "tbd", "unknown", "-"}


def _is_empty(value) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in _EMPTY


def _has_evidence(section_id: str, answers: dict) -> bool:
    """Did the consultant supply anything this section is built from?"""
    fields = SECTION_EVIDENCE.get(section_id)
    if not fields:
        return True  # no evidence map: assume the section is warranted
    return any(not _is_empty(answers.get(f)) for f in fields)


def _explicitly_excluded(section_id: str, answers: dict) -> Optional[str]:
    """Is this section ruled out by what the client said is out of scope?

    Returns the matched phrase, so the decision is explainable rather than
    silent -- a dropped section must be reportable to the user.
    """
    patterns = SECTION_EXCLUSIONS.get(section_id)
    if not patterns:
        return None
    haystack = " ".join(
        str(answers.get(k) or "") for k in ("out_of_scope", "training", "kt")
    ).lower()
    if not haystack.strip():
        return None
    for pat in patterns:
        m = re.search(pat, haystack)
        if m:
            return m.group(0)
    return None


def _upgrade_in_place(answers: dict) -> bool:
    """An in-place version upgrade, as opposed to a platform replacement.

    BTPN moved ForgeRock 6.5.x -> 7.3 on the same platform: nothing is
    decommissioned, nothing is replaced. A move OFF one vendor ONTO another is a
    different engagement and keeps its decommissioning section.
    """
    text = " ".join(str(answers.get(k) or "") for k in
                    ("is_migration", "business_objectives", "in_scope",
                     "current_state", "versions")).lower()
    if not text.strip():
        return False
    upgrading = bool(re.search(r"upgrade|version|release", text))
    replacing = bool(re.search(r"replace|migrate (from|off)|decommission|"
                               r"retire|new platform|greenfield", text))
    return upgrading and not replacing


def select_sections(template: list, answers: Optional[dict]) -> tuple[list, list]:
    """Sections to draft, plus (section_id, reason) for each one dropped.

    The reason is returned rather than only logged: a user must be able to see
    that a section was omitted deliberately and why, or a scoped-down proposal
    is indistinguishable from a broken one.
    """
    answers = answers or {}
    if not answers:
        return list(template), []

    keep, dropped = [], []
    for spec in template:
        sid = spec.id
        if sid in ALWAYS_KEEP:
            keep.append(spec)
            continue

        excluded = _explicitly_excluded(sid, answers)
        if excluded:
            dropped.append((sid, f"the client placed \"{excluded}\" out of scope"))
            continue

        if sid == "decommissioning" and _upgrade_in_place(answers):
            dropped.append((sid, "this is an in-place version upgrade, so nothing "
                                 "is being decommissioned"))
            continue

        if not _has_evidence(sid, answers):
            fields = ", ".join(SECTION_EVIDENCE.get(sid, ()))
            dropped.append((sid, f"no discovery answers were given for: {fields}"))
            continue

        keep.append(spec)

    # A proposal stripped below this is more likely a bug in the answers than a
    # genuinely tiny engagement, so fail safe and keep everything.
    if len(keep) < 5:
        log.warning("scope filter would leave only %d sections; keeping all %d",
                    len(keep), len(template))
        return list(template), []

    for sid, reason in dropped:
        log.info("section %s dropped: %s", sid, reason)
    return keep, dropped


# Subsection heading pattern -> the discovery fields it needs. A TABLE
# subsection with none of its inputs produces a header row and "To be
# confirmed" in every cell, which is worse than omitting it: it looks like the
# work was done and the numbers withheld.
#
# BTPN produced a 6x6 Licence Bill of Quantities and a 6x3 Payment Milestones
# with every figure "To be confirmed", against an IV proposal that had neither.
SUBSECTION_EVIDENCE: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"licence bill of quantit|license bill of quantit|total bill of quantit",
     ("license_included", "pricing_model", "currency")),
    (r"payment milestone", ("payment_milestones", "pricing_model")),
    (r"resident engineer", ("payment_milestones", "pricing_model")),
    (r"application integration bucket", ("payment_milestones", "app_count")),
    (r"commercial assumption", ("taxes", "travel", "support_terms",
                                "validity_period")),
    (r"knowledge transfer plan", ("kt", "training")),
    (r"training", ("training", "kt")),
    (r"hypercare|post[- ]production support", ("hypercare", "post_sla",
                                               "support_model")),
    (r"\bdr\b.*sizing|disaster recovery.*sizing", ("ha_dr_requirements",
                                                    "hardware_sizing_inputs")),
    (r"uat.*sizing|development.*sizing", ("envs", "hardware_sizing_inputs")),
)

_SUBSECTION_EVIDENCE_RE = tuple(
    (re.compile(pat, re.I), fields) for pat, fields in SUBSECTION_EVIDENCE)


def keep_subsection(heading: str, answers: Optional[dict]) -> bool:
    """Should this subsection be drafted at all?

    Only drops a subsection when it maps to discovery fields AND every one is
    empty. An unmapped heading is always kept -- silence is not evidence
    against a subsection.
    """
    if not answers or not heading:
        return True
    for pattern, fields in _SUBSECTION_EVIDENCE_RE:
        if pattern.search(heading):
            return any(not _is_empty(answers.get(f)) for f in fields)
    return True


def filter_subsections(spec, answers: Optional[dict]) -> list:
    """(heading, instruction) pairs worth drafting for this engagement."""
    pairs = list(getattr(spec, "subsections", ()) or ())
    if not answers or not pairs:
        return pairs
    kept = [(h, f) for h, f in pairs if keep_subsection(h, answers)]
    # Never strip a section to nothing: an empty section is a rendering bug,
    # and the section-level filter is the right place to remove it entirely.
    return kept or pairs


# Fields that materially change a section's quality when missing. A section is
# still drafted without them -- this is a prompt, not a gate -- but the
# consultant is told BEFORE generation rather than discovering 26 [SME REVIEW]
# markers afterwards.
#
# Only 1 of the 75 fields the templates draft from is marked required in the
# intake, which is why every run so far has carried 20-30 markers: nothing ever
# checked that a section had its inputs before drafting it.
HIGH_VALUE_FIELDS: dict[str, tuple[str, ...]] = {
    "proposed_solution": ("hardware_sizing_inputs", "deployment_model",
                          "cluster_topology", "envs", "target_integrations"),
    "target_state": ("hardware_sizing_inputs", "deployment_model",
                     "cluster_topology", "envs"),
    "scope_understanding": ("in_scope", "out_of_scope", "app_count", "user_count"),
    "current_state": ("current_state", "existing_iam_platform", "versions"),
    "implementation_approach": ("delivery_phases", "client_responsibilities", "raci"),
    "delivery_approach": ("delivery_phases", "client_responsibilities"),
    "project_timeline": ("duration", "timeline_milestones"),
    "migration_strategy": ("existing_iam_platform", "versions", "apps_to_onboard"),
    "assumptions_responsibilities": ("assumptions", "dependencies",
                                     "client_responsibilities"),
    "commercial": ("pricing_model", "payment_milestones"),
}


def missing_high_value(sections: list, answers: Optional[dict]) -> dict:
    """Per section, which quality-critical fields were left empty.

    Returned so the caller can ASK before drafting. Every run so far has been
    scored against a document whose gaps were only visible after generation,
    when the cost of filling them is a whole rerun.
    """
    answers = answers or {}
    out: dict[str, list[str]] = {}
    for spec in sections:
        fields = HIGH_VALUE_FIELDS.get(spec.id)
        if not fields:
            continue
        gaps = [f for f in fields if _is_empty(answers.get(f))]
        if gaps:
            out[spec.id] = gaps
    return out


# Subsection FAMILIES. A large engagement gets every variant; a small one gets
# one of each, which is what IV actually does.
#
# Measured: IV wrote 53 subsections for Amlak (42-week greenfield SailPoint
# build) and 19 for BTPN (scoped ForgeRock version upgrade of three lower
# environments). Shilpi wrote 43 for BTPN. The difference is not which topics
# are covered -- it is that IV writes ONE sizing table instead of four, ONE
# RACI instead of a legend plus two matrices, and no per-tranche tables at all.
#
# The first pattern in each family is the one kept.
SUBSECTION_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sizing", (r"production hardware sizing", r"dr .*sizing|disaster recovery.*sizing",
                r"uat.*sizing", r"development.*sizing")),
    ("raci", (r"raci - delivery activities", r"raci - project governance",
              r"raci legend")),
    ("tranche", (r"tranche 1", r"tranche 2", r"tranche 3")),
    ("payment", (r"payment milestone - implementation",
                 r"payment milestone - licence",
                 r"payment milestone - resident engineer",
                 r"payment milestone - application integration")),
    ("boq", (r"licence bill of quantit|license bill of quantit",
             r"total bill of quantit")),
)

_FAMILY_RE = tuple((name, tuple(re.compile(p, re.I) for p in pats))
                   for name, pats in SUBSECTION_FAMILIES)

# Below this the engagement is "compact" and gets one subsection per family.
# Chosen from the two measured points: BTPN is 3 environments and no stated
# duration; Amlak is 42 weeks, 25 applications and four environments.
COMPACT_APP_COUNT = 10
COMPACT_WEEKS = 20


# Set by the drafting engine from an LLM judgement made once per proposal. When
# present it OVERRIDES the keyword heuristic below.
#
# The heuristic reads words, not meaning, and got BTPN wrong in run 17. The
# environments answer said "Pre-Production and Production exist but are out of
# IV's scope"; the pattern saw "production" and concluded production was IN
# scope, so the engagement scored one signal instead of two and the document
# came out at 44 subsections against IV's 19.
#
# That is the same class of mistake as the colon-only parser -- pattern matching
# where reading was required -- so the fix is the same: ask the model, keep the
# heuristic as the offline fallback.
SCALE_ANSWER_KEY = "_engagement_scale"


def is_compact_engagement(answers: Optional[dict]) -> bool:
    """Is this a small engagement that warrants one of each thing?

    Prefers an explicit judgement stored under SCALE_ANSWER_KEY. Falls back to
    keyword signals when there is none, so this stays a pure function and every
    existing test keeps working without a model.

    Positive evidence only. An engagement with no size signals at all is NOT
    treated as compact -- the cost of under-writing a large proposal is far
    higher than the cost of an over-long small one.
    """
    answers = answers or {}

    explicit = str(answers.get(SCALE_ANSWER_KEY) or "").strip().lower()
    if explicit in ("compact", "small"):
        return True
    if explicit in ("full", "large", "standard"):
        return False

    signals = 0

    apps = str(answers.get("app_count") or "")
    m = re.search(r"\d+", apps)
    if m and int(m.group(0)) <= COMPACT_APP_COUNT:
        signals += 1

    dur = str(answers.get("duration") or "")
    m = re.search(r"(\d+)\s*week", dur, re.I)
    if m and int(m.group(1)) <= COMPACT_WEEKS:
        signals += 1
    m = re.search(r"(\d+)\s*month", dur, re.I)
    if m and int(m.group(1)) * 4 <= COMPACT_WEEKS:
        signals += 1

    # An in-place upgrade of a subset of environments is compact by nature:
    # nothing is built from scratch and production is often out of scope.
    scope = " ".join(str(answers.get(k) or "")
                     for k in ("in_scope", "out_of_scope", "is_migration",
                               "business_objectives")).lower()
    if re.search(r"upgrade", scope) and not re.search(r"greenfield|new (build|platform)", scope):
        signals += 1
    if re.search(r"production.{0,40}(not in scope|out of scope|excluded)", scope):
        signals += 1

    envs = str(answers.get("envs") or "").lower()
    if envs:
        # "Pre-Production and Production exist but are OUT OF IV's scope" names
        # production and excludes it. Presence of the word is not inclusion.
        excluded = re.search(r"production[^.]{0,60}(out of|not in|outside)\s+"
                             r"(iv'?s?\s+)?scope", envs)
        if excluded or not re.search(r"production", envs):
            signals += 1

    return signals >= 2


# A compact engagement gets at most this many subsections per section. IV wrote
# 19 subsections across 8 sections for BTPN -- between 2 and 3 each. Family
# collapsing alone only removes variants, and the migration template has few;
# the rest of the gap is simply breadth.
COMPACT_SUBSECTIONS_PER_SECTION = int(
    os.environ.get("SHILPI_COMPACT_SUBSECTIONS", "3"))


def cap_for_scale(pairs: list, answers: Optional[dict]) -> list:
    """Trim a section to its leading subsections for a compact engagement.

    Templates list subsections most-important-first, so the head is the right
    thing to keep. Never trims below one -- an empty section is a rendering
    bug, and removing a section entirely is select_sections' job.
    """
    if not is_compact_engagement(answers) or not pairs:
        return pairs
    return pairs[:max(1, COMPACT_SUBSECTIONS_PER_SECTION)]


def collapse_families(pairs: list, answers: Optional[dict]) -> list:
    """One subsection per family for a compact engagement.

    Returns the pairs unchanged when the engagement is not compact, or when a
    family has only one member present.
    """
    if not is_compact_engagement(answers) or not pairs:
        return pairs
    kept, seen = [], set()
    for heading, instruction in pairs:
        family = None
        for name, patterns in _FAMILY_RE:
            if any(pat.search(heading or "") for pat in patterns):
                family = name
                break
        if family is None:
            kept.append((heading, instruction))
            continue
        if family in seen:
            continue
        seen.add(family)
        kept.append((heading, instruction))
    return kept or pairs


def describe_gaps(gaps: dict) -> str:
    """A pre-flight prompt naming what will be weak and why.

    Deliberately not a blocker: a consultant often genuinely does not have a
    figure, and refusing to draft would be worse than drafting with a marker.
    """
    if not gaps:
        return ""
    every = sorted({f for fs in gaps.values() for f in fs})
    lines = [
        f"**{len(every)} field(s) that shape the draft are still empty.** "
        "Sections will be written, but these areas will carry [SME REVIEW] "
        "markers rather than your figures:",
        "",
    ]
    for sid, fields in sorted(gaps.items()):
        lines.append(f"  - {sid.replace('_', ' ')}: {', '.join(fields)}")
    lines += ["",
              "Send any of them as `field_name: value`, one per line, "
              "or say **generate anyway**."]
    return "\n".join(lines)


def describe_dropped(dropped: list) -> str:
    """One line per omitted section, for the chat reply."""
    if not dropped:
        return ""
    lines = ["Sections omitted because your answers ruled them out:"]
    for sid, reason in dropped:
        lines.append(f"  - {sid.replace('_', ' ')}: {reason}")
    lines.append("Tell me if you want any of these included anyway.")
    return "\n".join(lines)
