#!/usr/bin/env python3
"""Tests for scope-aware section and subsection selection.

Measured on the first migration proposal ever generated (Bank BTPN, ForgeRock
6.5.x -> 7.3, a scoped upgrade of three lower environments), against what IV
actually wrote for the same deal:

                        IV      Shilpi
    prose words      2,642       6,141   2.3x
    table words        962       2,703   2.8x
    top sections         8          15   1.9x

Three sections directly contradicted the answers given: Decommissioning for an
in-place version upgrade, Knowledge Transfer marked out of scope, and a
Commercial section whose licence/pricing/milestone fields were all skipped.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proposal_templates  # noqa: E402
import scope_filter as S  # noqa: E402

# The real BTPN discovery answers.
BTPN = {
    "is_migration": "Yes - version upgrade of an existing ForgeRock CIAM platform",
    "business_objectives": "Upgrade the existing ForgeRock CIAM platform to the latest version",
    "in_scope": "Upgrade the ForgeRock CIAM stack from V6.5.x to V7.3 on-premise",
    "versions": "Source ForgeRock 6.5.x, target ForgeRock 7.3",
    "current_state": "ForgeRock CIAM stack running on-premise at version 6.5.x",
    "out_of_scope": "Implementation, deployment and data migration on Pre-Production "
                    "and Production are NOT in scope. Organisational rollout, "
                    "communication, awareness and end-user training are BTPN's "
                    "responsibility.",
    "training": "Out of scope - end-user training is BTPN's responsibility",
    "license_included": "skip", "pricing_model": "skip",
    "payment_milestones": "skip", "taxes": "skip", "support_terms": "skip",
    "travel": "Onsite access provided if mutually agreed",
    "hardware_sizing_inputs": "Per node 8 vCPU, 32 GB memory, 500 GB disk",
    "ha_dr_requirements": "Production configured with high availability",
    "envs": "Development, SIT and UAT",
    "delivery_phases": "Project Management, Components Delivery, Requirements Gathering",
    "assumptions": "Success criteria documented in week 1",
    "similar_projects": "ForgeRock version upgrades",
}


def _ids(specs):
    return [s.id for s in specs]


def test_decommissioning_is_dropped_for_an_in_place_upgrade():
    """Nothing is being decommissioned when a platform is upgraded in place."""
    keep, dropped = S.select_sections(
        proposal_templates.get_template("migration"), BTPN)
    assert "decommissioning" not in _ids(keep)
    assert any(sid == "decommissioning" for sid, _ in dropped)


def test_decommissioning_survives_a_platform_replacement():
    """A move OFF one vendor ONTO another genuinely decommissions something."""
    replacing = dict(BTPN)
    replacing["business_objectives"] = ("Replace the legacy Oracle Access Manager "
                                        "platform with SailPoint IdentityIQ")
    replacing["in_scope"] = "Migrate from Oracle OAM and decommission the legacy estate"
    keep, _ = S.select_sections(
        proposal_templates.get_template("migration"), replacing)
    assert "decommissioning" in _ids(keep)


def test_a_section_the_client_excluded_is_dropped():
    keep, dropped = S.select_sections(
        proposal_templates.get_template("migration"), BTPN)
    assert "knowledge_transfer" not in _ids(keep)
    reason = next(r for sid, r in dropped if sid == "knowledge_transfer")
    assert "out of scope" in reason.lower()


def test_core_sections_are_never_dropped():
    """A proposal without these is not a proposal, whatever discovery says."""
    empty = {k: "skip" for k in BTPN}
    keep, _ = S.select_sections(proposal_templates.get_template("migration"), empty)
    for required in ("executive_summary", "company_profile", "current_state",
                     "migration_strategy"):
        assert required in _ids(keep), required


def test_no_answers_means_no_filtering():
    """Silence is not evidence against a section."""
    template = proposal_templates.get_template("migration")
    keep, dropped = S.select_sections(template, {})
    assert len(keep) == len(template) and not dropped


def test_the_filter_fails_safe_rather_than_gutting_a_proposal():
    """Fewer than five sections is more likely bad answers than a tiny deal."""
    template = proposal_templates.get_template("migration")
    hostile = {"out_of_scope": "training decommission commercial pricing "
                               "knowledge transfer retire licence cost"}
    keep, dropped = S.select_sections(template, hostile)
    assert len(keep) >= 5


def test_empty_commercial_tables_are_not_drafted():
    """BTPN produced a 6x6 Licence BOQ and 6x3 Payment Milestones entirely of
    "To be confirmed", against an IV proposal that had neither."""
    for heading in ("Licence Bill of Quantities", "Total Bill of Quantities",
                    "Payment Milestone - Licence",
                    "Payment Milestone - Resident Engineer"):
        assert not S.keep_subsection(heading, BTPN), heading


def test_subsections_with_real_inputs_survive():
    for heading in ("Proposed Production Hardware Sizing", "Migration Pattern",
                    "Rollback Position", "Proposed DR and Non-Production Sizing"):
        assert S.keep_subsection(heading, BTPN), heading


def test_an_unmapped_subsection_is_always_kept():
    assert S.keep_subsection("Some Heading Nobody Mapped", BTPN)


def test_a_section_is_never_stripped_to_nothing():
    """An empty section is a rendering bug; removal is the section filter's job."""
    spec = next(s for s in proposal_templates.get_template("implementation")
                if s.id == "commercial")
    kept = S.filter_subsections(spec, {k: "skip" for k in BTPN})
    assert kept, "commercial was stripped to zero subsections"


def test_dropped_sections_are_explainable():
    """A scoped-down proposal must be distinguishable from a broken one."""
    _, dropped = S.select_sections(
        proposal_templates.get_template("migration"), BTPN)
    text = S.describe_dropped(dropped)
    assert "decommissioning" in text and "knowledge transfer" in text
    assert S.describe_dropped([]) == ""


import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _runner import run_tests  # noqa: E402

run_tests(globals(), "SCOPE FILTER TESTS")


def test_the_filters_are_actually_wired_into_drafting():
    """CALL-SITE check. The tests above exercise scope_filter directly, so they
    pass whether or not document_engine calls it -- the built-but-never-wired
    failure this project has hit five times."""
    import inspect
    import document_engine
    gen = inspect.getsource(document_engine.generate_proposal)
    assert "scope_filter.select_sections" in gen, \
        "sections are drafted without the scope filter"
    draft = inspect.getsource(document_engine.draft_section)
    assert "scope_filter.filter_subsections" in draft, \
        "subsections are drafted without the evidence filter"


def test_omitted_sections_are_named_in_the_document():
    """CALL-SITE. Run 15 dropped three sections and said nothing, which makes a
    deliberately scoped proposal indistinguishable from a truncated one."""
    import io
    import document_engine
    from docx import Document
    docx_bytes = document_engine.assemble_docx(
        metadata={"client_name": "Bank BTPN", "proposal_type": "migration",
                  "dropped_sections": [
                      ("decommissioning", "this is an in-place version upgrade"),
                      ("knowledge_transfer", 'the client placed "training" out of scope')]},
        sections=[{"id": "current_state", "title": "Current State Assessment",
                   "content": "Body."}],
    )
    text = "\n".join(p.text for p in Document(io.BytesIO(docx_bytes)).paragraphs)
    assert "Sections omitted for this engagement" in text
    assert "Decommissioning" in text and "in-place version upgrade" in text
    assert "Knowledge Transfer" in text


def test_no_note_when_nothing_was_dropped():
    import io
    import document_engine
    from docx import Document
    docx_bytes = document_engine.assemble_docx(
        metadata={"client_name": "X", "proposal_type": "migration"},
        sections=[{"id": "current_state", "title": "Current State", "content": "b"}],
    )
    text = "\n".join(p.text for p in Document(io.BytesIO(docx_bytes)).paragraphs)
    assert "Sections omitted" not in text
