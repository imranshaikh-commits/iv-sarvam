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


def _mentions_other_vendor(text: str, wanted: Optional[str],
                           wanted_vendors: Optional[list] = None) -> bool:
    """True when the text names a vendor that is NOT one of the ones being
    proposed.

    MULTI-VENDOR: pass `wanted_vendors` (a list) for a multi-vendor
    engagement rather than relying on `wanted` being a combined string. A
    combined string like "Ping Identity and Saviynt" happened to work by
    coincidence (each vendor's own name is a substring of the combined
    string), but that is not something to depend on — a capability-scoped
    name, a third vendor, or a vendor name that is itself a substring of
    another would silently break it. `wanted_vendors` checks membership
    against each vendor explicitly instead.
    """
    low = (text or "").lower()
    candidates = [w.lower() for w in (wanted_vendors or [])] or (
        [(wanted or "").lower()] if wanted else [])
    for v in _VENDORS:
        if v not in low:
            continue
        # "ping" inside "ping identity" is the same vendor, not another --
        # checked against EVERY wanted vendor, not just the first/combined one.
        is_one_of_ours = any(
            v in want or want.split()[0] in v for want in candidates if want)
        if not is_one_of_ours:
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


def _is_proposed_vendor(vendor: str, wanted: Optional[list]) -> bool:
    """A partner image's vendor ("Ping Identity") against the proposed vendors.
    No proposed vendor means no partner image: nothing confirms it fits."""
    v = (vendor or "").lower()
    return bool(v) and any(
        v.split()[0] in w.lower() or w.lower().split()[0] in v
        for w in (wanted or []) if w and w.strip())


def select_assets(assets: list[dict], section_id: str,
                  iam_vendor: Optional[str] = None,
                  iam_vendors: Optional[list] = None,
                  limit: Optional[int] = None) -> list[dict]:
    """Assets suitable for this section, best first.

    `assets` is the approved, placeable library (already filtered by the caller
    so this function stays pure and testable).

    `iam_vendors` (multi-vendor engagements): pass the split vendor list so an
    asset mentioning a competing vendor by name is excluded correctly for
    EVERY proposed vendor, not just the first one / a combined string. Falls
    back to treating `iam_vendor` as a single-item list when not given, so a
    single-vendor call site is unaffected.

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
    vendors_for_check = iam_vendors or ([iam_vendor] if iam_vendor else None)

    scored: list[tuple[int, dict]] = []
    for a in assets:
        if a.get("asset_kind") not in kinds:
            continue
        if not a.get("approved"):
            # Belt and braces: the caller filters on this, and so does this.
            continue
        if a.get("vendor"):
            # Partner product image (partner_product_assets): public vendor
            # material, tagged with its vendor and approved by a human. Its OCR
            # text is diagram-label noise, so it is matched on vendor, never on
            # description, and only where the section takes product imagery.
            if a.get("asset_kind") == "product" and "product" in kinds \
                    and _is_proposed_vendor(a["vendor"], vendors_for_check):
                scored.append((1, a))
            continue
        if a.get("asset_kind") == "product":
            # IV-library product images carry no reliable vendor: ESNAD 09-25
            # got an IBM screenshot and a ForgeRock Open Banking demo in a
            # Saviynt/Ping proposal. Product imagery comes from the vendor-tagged
            # partner library and the kit only.
            continue
        desc = f"{a.get('vision_description') or ''} {a.get('ocr_text') or ''}"
        if not rx.search(desc):
            continue
        if _mentions_other_vendor(desc, iam_vendor, vendors_for_check):
            continue
        # Prefer assets seen in more proposals: recurrence is the strongest
        # available evidence that IV reuses this image deliberately.
        score = int(a.get("occurrences") or 1)
        scored.append((score, a))

    # Interleave partner images by vendor so a Ping + Saviynt deal shows both
    # products, not the first vendor's diagrams in every slot. IV's own assets
    # have no vendor and keep plain score order (stable sort).
    seen: dict[str, int] = {}
    keyed = []
    for score, a in scored:
        v = (a.get("vendor") or "").lower()
        rank = seen.get(v, 0) if v else 0
        if v:
            seen[v] = rank + 1
        keyed.append((-score, rank, a))
    keyed.sort(key=lambda t: (t[0], t[1]))
    chosen = [a for _s, _r, a in keyed[:limit]]
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


# --- The kit: fixed images IV places in every proposal -------------------------
# Keyword-matching vision descriptions (above) placed an IBM screenshot, an
# Open Banking "ForgeBank" diagram and an on-prem data-centre drawing in a SaaS
# Saviynt/Ping proposal (ESNAD 09-25), and still left IV's own house slides out.
# IV does not choose these images per deal: its company slides, methodology,
# KT and support slides go in every proposal, and each vendor's analyst views,
# product slides and reference architectures go in every proposal for that
# vendor. So the kit is a fixed manifest, not a search.
#
# Images live in the asset bucket under kit/ (scripts/upload_asset_kit.py).
# A kit image not uploaded yet is skipped at download time, so this manifest can
# ship before the images do.
#
# heading: regex for the subsection heading the image sits under ({vendor} is
#   the proposed vendor's first word); None = directly under the section title.
# vendor: placed only when a proposed vendor's name contains this.
# domains: placed only when ALL are in scope (and scope is known).
# unless: NOT placed when all of these are in scope (a fuller picture covers it).
# deployment: "saas" / "self" -- placed only for that deployment model.
# near: regex on the model's own capability headings inside that subsection;
#   the image sits under the first one that matches, not piled at the end
#   (ESNAD 09-27 stacked 10 Ping and 9 Saviynt images after the last heading).
KIT: tuple[dict, ...] = (
    # IV house slides: withdrawn 2026-09-27 at Imran's instruction -- the
    # corporate deck they came from is out of date. They return when the
    # updated deck is supplied.
    # Also withdrawn 2026-10-14 at Imran's instruction: the three Ping
    # case-study slides (from IV's case-study deck) and the third-party
    # marketing slides (Gartner/Forrester market leadership, Ping's customer
    # logo wall, KuppingerCole for Saviynt). IV's proposals do not carry its
    # slide decks; the kit keeps only the vendor diagrams that explain the
    # proposed solution.
    # Ping Identity
    {"file": "ping_workforce_challenges.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "ping", "domains": ("wiam",), "near": r"Workforce Identity|WIAM"},
    {"file": "ping_aic_multitenant.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "ping", "deployment": "saas", "near": r"Workforce Identity|WIAM|Platform|Architecture"},
    {"file": "ping_aic_tenant_isolation.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "ping", "deployment": "saas", "near": r"Workforce Identity|WIAM|Platform|Architecture"},
    {"file": "ping_advanced_identity_software.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "ping", "deployment": "self", "near": r"Workforce Identity|WIAM|Platform|Architecture"},
    {"file": "ping_orchestration.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "ping", "near": r"Orchestration|Journey"},
    {"file": "ping_gateway.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "ping", "near": r"Gateway|Legacy"},
    {"file": "ping_app_integration.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "ping", "near": r"Single Sign-On|Federation|Integration"},
    {"file": "ping_adaptive_risk.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "ping", "near": r"Adaptive|Risk"},
    {"file": "ping_pingid_app.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "ping", "near": r"Passwordless|FIDO|MFA"},
    # Saviynt (Frost Radar 392 px and Gartner PAM 600 px withdrawn: too small
    # to print sharp)
    # One picture per topic (ESNAD 09-28 printed two JML flows and two PAM
    # flows side by side): the IGA-only JML flow yields to the IGA+PAM one, and
    # the second PAM flow slide is withdrawn.
    {"file": "saviynt_eic_logical.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "saviynt", "domains": ("iga",), "near": r"Identity Governance|IGA"},
    {"file": "saviynt_jml_flow.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "saviynt", "domains": ("iga",), "unless": ("pam",), "near": r"Joiner|Lifecycle"},
    {"file": "saviynt_jml_iga_pam.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "saviynt", "domains": ("iga", "pam"), "near": r"Joiner|Lifecycle"},
    {"file": "saviynt_pam_hla.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "saviynt", "domains": ("pam",), "near": r"Privileged Access Management|\bPAM\b"},
    {"file": "saviynt_pam_architecture.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "saviynt", "domains": ("pam",), "near": r"Privileged Access Management|\bPAM\b"},
    # IV shows the SaaS platform picture with the PAM technical architecture.
    {"file": "saviynt_cloud_platform.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "saviynt", "deployment": "saas", "near": r"Privileged Access Management|\bPAM\b|Platform|Architecture"},
    {"file": "saviynt_pam_flow.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "saviynt", "domains": ("pam",), "near": r"Session|Just-in-Time"},
    {"file": "saviynt_identity_to_privileged_flow.png", "section": "solution_overview", "heading": r"^{vendor}.* Solution Overview", "vendor": "saviynt", "domains": ("iga", "pam"), "near": r"Approval|Audit|Analytics"},
)
KIT_PREFIX = "kit/"


def kit_for(section_id: str, context: dict) -> list[dict]:
    """Kit images for this section in this engagement, in manifest order:
    [{"id", "storage_path", "heading": compiled regex or None}]."""
    vendors = [v for v in (context.get("iam_vendors") or [context.get("iam_vendor")]) if v]
    domains = set(context.get("domains") or ())
    saas = bool(context.get("is_saas"))
    out = []
    for item in KIT:
        if item["section"] != section_id:
            continue
        name = None
        if item.get("vendor"):
            name = next((v for v in vendors if item["vendor"] in v.lower()), None)
            if not name:
                continue
        if item.get("domains") and not set(item["domains"]) <= domains:
            continue
        if item.get("unless") and set(item["unless"]) <= domains:
            continue
        dep = item.get("deployment")
        if dep and (dep == "saas") != saas:
            continue
        heading = item.get("heading")
        if heading and name:
            heading = heading.replace("{vendor}", re.escape(name.split()[0]))
        out.append({"id": f"kit:{item['file']}",
                    "storage_path": KIT_PREFIX + item["file"],
                    "heading": re.compile(heading, re.I) if heading else None,
                    "near": re.compile(item["near"], re.I) if item.get("near") else None})
    return out
