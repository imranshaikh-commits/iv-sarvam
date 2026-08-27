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


def test_the_right_vendors_asset_is_shown():
    chosen = A.select_assets(LIB, "solution_overview", "SailPoint")
    assert chosen, "no asset selected for the proposed vendor"
    assert "sailpoint" in chosen[0]["vision_description"].lower()


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
