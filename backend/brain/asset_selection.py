"""Select reusable images from the asset library for a drafted section.

WHY THIS MATCHES ON DESCRIPTIONS, NOT HEADINGS
----------------------------------------------
The design intent was to place images by their provenance -- the
`(section_heading, image, caption)` triple, so an image that sat under
"Identity Maturity Journey" in three proposals lands under that heading in the
next one.

That data is not trustworthy. `extract_visual_assets.py` fell back to "the first
non-image chunk in the proposal" whenever the image's own heading was the
extractor's numbering ("Diagram #5"), and that first chunk is almost always
"Introduction". The result: 49 corporate assets across 12 proposals all labelled
"Introduction". Provenance that says everything came from the same place says
nothing.

The vision descriptions ARE reliable -- they were produced by a model looking at
the image, and they are specific: "a bar chart titled Skill Matrix from a
technical proposal by Inspirit Vision", "a screenshot of a dashboard interface".
So selection matches the section being drafted against the description.

WHAT MAY BE PLACED
------------------
Only `corporate` and `product` assets, and only where `approved` is true.

`architecture` assets are excluded outright. They depict a SPECIFIC client's
estate -- their zones, their node counts, their integrations. Placing one in
another client's proposal would leak that client's topology into a document
addressed to someone else. There is no approval workflow that makes that safe,
so the exclusion is in code rather than left to a reviewer's judgement.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("shilpi-brain.assets")

# Kinds that may ever appear in a generated proposal. See the module docstring:
# `architecture` is excluded because it is client-specific by nature.
PLACEABLE_KINDS = ("corporate", "product")

# Section id -> (asset kinds, description patterns) that suit it.
#
# Patterns are matched against the VISION DESCRIPTION. Requiring a pattern match
# rather than kind alone stops the Company Profile section pulling in a random
# corporate asset just because it is corporate: it has to actually look like
# company material.
SECTION_ASSET_RULES: dict[str, tuple[tuple[str, ...], str]] = {
    "company_profile": (("corporate",),
                        r"inspirit vision|skill matrix|certified|resources|"
                        r"organi[sz]ation|office|branch|team structure|"
                        r"delivery (model|centre|center)|workforce"),
    "similar_experience": (("corporate",),
                           r"case stud|success stor|customer|client logo|logos|"
                           r"reference|sector|industr|banking|government|"
                           r"telecom|healthcare|retail|engagement|deployment "
                           r"across|outcome|achievement|delivered"),
    "solution_overview": (("product",),
                          r"platform|capabilit|module|architecture|"
                          r"identity (governance|security)|reference"),
    "proposed_solution": (("product",),
                          r"platform|connector|integration|console|"
                          r"architecture|reference"),
    "implementation_approach": (("corporate",),
                                r"methodolog|agile|delivery model|phase|"
                                r"maturity|approach|lifecycle"),
    "project_timeline": (("corporate",),
                         r"gantt|timeline|schedule|phase|milestone|week"),
    "knowledge_transfer": (("corporate", "product"),
                           r"training|knowledge|enablement|documentation|support model"),
    # Migration template
    "target_state": (("product",), r"platform|architecture|reference|capabilit"),
    "migration_strategy": (("corporate", "product"),
                           r"migration|cutover|wave|phase|upgrade|coexist"),
}

# A vendor-specific asset must not be shown for a different vendor. A SailPoint
# console screenshot in a Ping proposal is simply wrong.
_VENDORS = ("sailpoint", "ping identity", "ping", "forgerock", "okta", "ibm",
            "oracle", "cyberark", "beyondtrust", "saviynt", "keycloak")


def _mentions_other_vendor(text: str, wanted: Optional[str]) -> bool:
    """True when the text names a vendor that is NOT the one being proposed."""
    low = (text or "").lower()
    want = (wanted or "").lower()
    for v in _VENDORS:
        if v in low and (not want or v not in want):
            # "ping" inside "ping identity" is the same vendor, not another.
            if want and (v in want or want.split()[0] in v):
                continue
            return True
    return False


# How many images each section carries. IV's distribution is nothing like
# uniform: Case Studies has TEN, Project Plan five, and most solution
# subsections one each. Run 9 used a flat two per section and put zero in Case
# Studies -- IV's credibility section, and ours was text only.
SECTION_ASSET_LIMITS: dict[str, int] = {
    "similar_experience": 6,
    "solution_overview": 4,
    "company_profile": 3,
    "proposed_solution": 3,
    "project_timeline": 2,
}
DEFAULT_ASSET_LIMIT = 2


def select_assets(assets: list[dict], section_id: str,
                  iam_vendor: Optional[str] = None,
                  limit: Optional[int] = None) -> list[dict]:
    """Assets suitable for this section, best first.

    `assets` is the approved, placeable library (already filtered by the caller
    so this function stays pure and testable).

    Returns at most `limit`. Two per section is deliberate: IV's proposals carry
    37 images across 11 sections, so roughly three per section including
    per-deal architecture drawings we cannot reuse.
    """
    rule = SECTION_ASSET_RULES.get(section_id)
    if not rule:
        return []
    if limit is None:
        limit = SECTION_ASSET_LIMITS.get(section_id, DEFAULT_ASSET_LIMIT)
    kinds, pattern = rule
    rx = re.compile(pattern, re.I)

    scored: list[tuple[int, dict]] = []
    for a in assets:
        if a.get("asset_kind") not in kinds:
            continue
        if not a.get("approved"):
            # Belt and braces: the caller filters on this, and so does this.
            continue
        desc = f"{a.get('vision_description') or ''} {a.get('ocr_text') or ''}"
        if not rx.search(desc):
            continue
        if _mentions_other_vendor(desc, iam_vendor):
            continue
        # Prefer assets seen in more proposals: recurrence is the strongest
        # available evidence that IV reuses this image deliberately.
        score = int(a.get("occurrences") or 1)
        scored.append((score, a))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    chosen = [a for _s, a in scored[:limit]]
    if chosen:
        log.info("assets: %d selected for section %s", len(chosen), section_id)
    return chosen


def asset_summary(placed: dict[str, list[dict]]) -> str:
    """One line per placed image, for the reviewer to accept or reject.

    Approval is on the BATCH rather than per image: asking a reviewer to approve
    a dozen images one at a time is the kind of gate people click through
    without reading, which is worse than no gate.
    """
    if not placed:
        return ""
    lines = ["Images placed in this draft (reply to remove any):"]
    n = 0
    for section_id, assets in placed.items():
        for a in assets:
            n += 1
            desc = (a.get("vision_description") or "").replace("\n", " ")
            desc = re.sub(r"^\[[^\]]*\]\s*", "", desc)[:90]
            lines.append(f"  {n}. [{a.get('asset_kind')}] {section_id}: {desc}")
    return "\n".join(lines)
