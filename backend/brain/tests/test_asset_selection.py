#!/usr/bin/env python3
"""Tests for image selection from the reusable asset library.

The library holds 946 assets from 74 proposals. Only `corporate` (239) and
`product` (106) may ever be placed; `architecture` (381) is excluded in code
because those images depict a SPECIFIC client's estate and placing one in
another client's proposal would leak their topology.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asset_selection as A  # noqa: E402

LIB = [
    {"asset_kind": "corporate", "approved": True, "occurrences": 9,
     "vision_description": "a bar chart titled Skill Matrix from Inspirit Vision "
                           "showing certified resources"},
    {"asset_kind": "corporate", "approved": True, "occurrences": 3,
     "vision_description": "a Gantt chart illustrating a 20-week project timeline"},
    {"asset_kind": "product", "approved": True, "occurrences": 5,
     "vision_description": "an architecture diagram illustrating SailPoint's Cloud "
                           "Identity Platform capabilities"},
    {"asset_kind": "product", "approved": True, "occurrences": 4,
     "vision_description": "a screenshot of the ForgeRock admin console platform"},
    {"asset_kind": "architecture", "approved": True, "occurrences": 1,
     "vision_description": "multi-tiered high-availability IAM architecture for PPA "
                           "showing network zones and node counts"},
    {"asset_kind": "corporate", "approved": False, "occurrences": 9,
     "vision_description": "Inspirit Vision office locations map"},
]


def test_architecture_assets_are_never_placed():
    """THE safety rule. An architecture drawing depicts one client's estate."""
    arch = [a for a in LIB if a["asset_kind"] == "architecture"]
    for section in A.SECTION_ASSET_RULES:
        assert A.select_assets(arch, section, "SailPoint") == [], section


def test_unapproved_assets_are_never_placed():
    for section in A.SECTION_ASSET_RULES:
        for a in A.select_assets(LIB, section, "SailPoint"):
            assert a["approved"] is True, section


def test_another_vendors_screenshot_is_not_shown():
    """A SailPoint console in a Ping proposal is simply wrong."""
    chosen = A.select_assets(LIB, "solution_overview", "Ping Identity")
    for a in chosen:
        assert "sailpoint" not in a["vision_description"].lower()
        assert "forgerock" not in a["vision_description"].lower()


def test_untagged_library_product_images_are_never_placed():
    """ESNAD 09-25: an IBM screenshot and a ForgeRock Open Banking demo reached
    a Saviynt/Ping proposal through description matching. Product imagery now
    comes only from vendor-tagged partner assets and the kit."""
    assert not A.select_assets(LIB, "solution_overview", "SailPoint")


def _kit(section, **ctx):
    base = {"iam_vendors": [], "domains": [], "is_saas": False}
    return [k["storage_path"] for k in A.kit_for(section, {**base, **ctx})]


def test_no_iv_corporate_slides_until_the_updated_deck_arrives():
    """Imran, 2026-09-27: the corporate deck is out of date; use none of it."""
    assert not any(k["file"].startswith("iv_") for k in A.KIT)
    assert not A.kit_for("company_profile", {"iam_vendors": ["Okta"]})


def test_kit_images_are_large_enough_to_print_sharp():
    """Every kit file must reach 150 dpi at 2.5 in (375 px) wide at least; the
    Frost Radar (392 px) and Gartner PAM (600 px) were withdrawn as blurry."""
    names = {k["file"] for k in A.KIT}
    assert "saviynt_frost_radar.png" not in names and "saviynt_gartner_pam.png" not in names


def test_vendor_kit_image_sits_near_its_capability_heading():
    k = next(x for x in A.kit_for("solution_overview", {"iam_vendors": ["Ping Identity"]})
             if x["storage_path"] == "kit/ping_gateway.png")
    assert k["near"].search("Protection of Legacy Applications via PingGateway")


def test_vendor_kit_follows_vendor_domains_and_deployment():
    saas = _kit("solution_overview", iam_vendors=["Ping Identity", "Saviynt"],
                domains=["wiam", "ciam", "iga", "pam"], is_saas=True)
    assert "kit/ping_aic_multitenant.png" in saas
    assert "kit/ping_advanced_identity_software.png" not in saas   # self-managed product
    assert "kit/saviynt_pam_hla.png" in saas
    iga_only = _kit("solution_overview", iam_vendors=["Saviynt"], domains=["iga"])
    assert "kit/saviynt_jml_flow.png" in iga_only
    assert "kit/saviynt_pam_hla.png" not in iga_only               # PAM not in scope
    assert not any("ping_" in p for p in iga_only)                  # vendor not proposed
    assert not _kit("solution_overview", iam_vendors=["Okta"], domains=["wiam"])


def test_vendor_heading_is_filled_with_the_proposed_vendor():
    k = next(x for x in A.kit_for("solution_overview", {"iam_vendors": ["Ping Identity"]})
             if x["storage_path"] == "kit/ping_market_leadership.png")
    assert k["heading"].search("Why Ping Identity") and not k["heading"].search("Why Saviynt")


def test_section_gets_material_that_suits_it():
    profile = A.select_assets(LIB, "company_profile", "SailPoint")
    assert profile and "skill matrix" in profile[0]["vision_description"].lower()
    timeline = A.select_assets(LIB, "project_timeline", "SailPoint")
    assert timeline and "gantt" in timeline[0]["vision_description"].lower()


def test_kind_alone_is_not_enough():
    """A corporate asset must also LOOK like company material for that section.

    Otherwise Company Profile pulls in any corporate image it can find.
    """
    odd = [{"asset_kind": "corporate", "approved": True, "occurrences": 5,
            "vision_description": "a photograph of a server rack"}]
    assert A.select_assets(odd, "company_profile", "SailPoint") == []


def test_unmapped_section_places_nothing():
    assert A.select_assets(LIB, "assumptions_responsibilities", "SailPoint") == []
    assert A.select_assets(LIB, "no_such_section", "SailPoint") == []


def test_more_widely_reused_assets_rank_first():
    """Recurrence across proposals is the best available evidence that IV
    reuses an image deliberately rather than it being a one-off."""
    lib = [
        {"asset_kind": "corporate", "approved": True, "occurrences": 1,
         "vision_description": "Inspirit Vision office branch locations"},
        {"asset_kind": "corporate", "approved": True, "occurrences": 12,
         "vision_description": "Inspirit Vision certified resources workforce"},
    ]
    assert A.select_assets(lib, "company_profile", "SailPoint", limit=1)[0]["occurrences"] == 12


def test_limit_is_respected():
    lib = [{"asset_kind": "corporate", "approved": True, "occurrences": i,
            "vision_description": "Inspirit Vision workforce certified resources"}
           for i in range(6)]
    assert len(A.select_assets(lib, "company_profile", "SailPoint", limit=2)) == 2


def test_summary_lists_every_placed_image():
    placed = {"company_profile": A.select_assets(LIB, "company_profile", "SailPoint")}
    out = A.asset_summary(placed)
    assert "company_profile" in out and "corporate" in out
    assert A.asset_summary({}) == ""


# Shared runner: collects tests at EXIT, so appending a test below this
# line cannot silently skip it. Four files previously lost appended
# tests to an inline loop that read globals() at call time.
import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _runner import run_tests  # noqa: E402

run_tests(globals(), "ASSET SELECTION TESTS")


def test_case_studies_get_a_bigger_image_budget_than_other_sections():
    """IV's Similar Experience section carries TEN images -- it is the
    credibility section. Run 9 used a flat two per section and put zero there."""
    assert A.SECTION_ASSET_LIMITS["similar_experience"] > A.DEFAULT_ASSET_LIMIT
    lib = [{"asset_kind": "corporate", "approved": True, "occurrences": i,
            "vision_description": f"banking sector engagement outcome panel {i}"}
           for i in range(9)]
    picked = A.select_assets(lib, "similar_experience", "SailPoint")
    assert len(picked) == A.SECTION_ASSET_LIMITS["similar_experience"]


def test_an_explicit_limit_still_wins():
    lib = [{"asset_kind": "corporate", "approved": True, "occurrences": i,
            "vision_description": f"banking sector engagement outcome {i}"}
           for i in range(9)]
    assert len(A.select_assets(lib, "similar_experience", "SailPoint", limit=1)) == 1


def test_sector_words_reach_the_case_studies_section():
    """IV's case-study visuals are client logos, sector graphics and outcome
    panels -- the pattern must not require the literal words 'case study'."""
    for desc in ("a logo wall of banking clients",
                 "government sector deployment achievements",
                 "outcomes delivered for a telecom customer"):
        lib = [{"asset_kind": "corporate", "approved": True, "occurrences": 3,
                "vision_description": desc}]
        assert A.select_assets(lib, "similar_experience", "SailPoint"), desc


# --- Partner product images (partner_product_assets) -----------------------

def _partner(vendor, n=1, kind="product", approved=True):
    return [{"asset_kind": kind, "approved": approved, "vendor": vendor,
             "storage_path": f"{vendor}/{i}.png", "bucket": "partner-product-assets",
             "ocr_text": "PingFederate PingOne DaVinci"} for i in range(n)]


def test_partner_image_placed_only_for_a_proposed_vendor():
    lib = _partner("Ping Identity")
    assert A.select_assets(lib, "solution_overview", iam_vendors=["Ping Identity", "Saviynt"])
    assert not A.select_assets(lib, "solution_overview", "SailPoint")
    # No proposed vendor: nothing can confirm the image fits.
    assert not A.select_assets(lib, "solution_overview")


def test_partner_image_never_lands_in_iv_corporate_sections():
    """A vendor's slide is not IV's company profile or case study."""
    lib = _partner("Ping Identity") + _partner("Okta", kind="corporate")
    for sec in ("company_profile", "similar_experience", "implementation_approach"):
        assert not A.select_assets(lib, sec, "Ping Identity"), sec


def test_partner_image_needs_approval_and_product_kind():
    lib = _partner("Saviynt", approved=False) + _partner("Saviynt", kind="unknown")
    assert not A.select_assets(lib, "proposed_solution", "Saviynt")


def test_partner_images_interleave_vendors_in_a_multi_vendor_deal():
    lib = _partner("Ping Identity", 3) + _partner("Saviynt", 1)
    got = A.select_assets(lib, "solution_overview",
                          iam_vendors=["Ping Identity", "Saviynt"], limit=2)
    assert sorted(a["vendor"] for a in got) == ["Ping Identity", "Saviynt"]


def test_one_kit_picture_per_topic_when_iga_and_pam_are_both_in_scope():
    ctx = {"iam_vendor": "Saviynt", "domains": ["iga", "pam"], "is_saas": True}
    files = [a["id"] for a in A.kit_for("solution_overview", ctx)]
    assert "kit:saviynt_jml_iga_pam.png" in files
    assert "kit:saviynt_jml_flow.png" not in files
    assert "kit:saviynt_pam_e2e_flow.png" not in files
    iga_only = [a["id"] for a in A.kit_for(
        "solution_overview", {**ctx, "domains": ["iga"]})]
    assert "kit:saviynt_jml_flow.png" in iga_only
