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


# ---------------------------------------------------------------------------
# Sizing the document to the engagement.
#
# IV wrote 53 subsections for Amlak (42-week greenfield SailPoint build) and 19
# for BTPN (scoped ForgeRock upgrade of three lower environments). Shilpi wrote
# 43 for BTPN -- almost exactly the size of IV's AMLAK proposal. The difference
# is not which topics are covered: IV writes ONE sizing table instead of four,
# ONE RACI instead of three, and no per-tranche tables.
# ---------------------------------------------------------------------------

_COMPACT = {"app_count": "skip", "duration": "skip",
            "in_scope": "Upgrade the ForgeRock CIAM stack from V6.5.x to V7.3",
            "out_of_scope": "Pre-Production and Production are NOT in scope",
            "is_migration": "Yes - version upgrade",
            "envs": "Development, SIT and UAT"}

_LARGE = {"app_count": "25 applications", "duration": "42 weeks",
          "envs": "Production, DR, UAT, Development",
          "in_scope": "greenfield SailPoint IdentityIQ build"}


def test_a_scoped_upgrade_reads_as_compact():
    assert S.is_compact_engagement(_COMPACT)


def test_a_long_greenfield_build_does_not():
    assert not S.is_compact_engagement(_LARGE)


def test_no_size_signals_means_not_compact():
    """Under-writing a large proposal costs far more than an over-long small
    one, so absence of evidence must not read as evidence of smallness."""
    assert not S.is_compact_engagement({})
    assert not S.is_compact_engagement({"client_name": "X"})


def test_one_sizing_table_for_a_compact_engagement():
    pairs = [("Proposed Production Hardware Sizing", "t"),
             ("Proposed DR and Non-Production Sizing", "t"),
             ("Capability Mapping - Current to Target", "t")]
    out = S.collapse_families(pairs, _COMPACT)
    sizing = [h for h, _ in out if "Sizing" in h]
    assert len(sizing) == 1, out
    assert any("Capability Mapping" in h for h, _ in out), "unrelated pair dropped"


def test_all_variants_survive_a_large_engagement():
    pairs = [("Proposed Production Hardware Sizing", "t"),
             ("Proposed DR and Non-Production Sizing", "t")]
    assert len(S.collapse_families(pairs, _LARGE)) == 2


def test_a_compact_section_is_capped_but_never_emptied():
    pairs = [(f"Sub {i}", "t") for i in range(8)]
    out = S.cap_for_scale(pairs, _COMPACT)
    assert 1 <= len(out) <= S.COMPACT_SUBSECTIONS_PER_SECTION
    assert S.cap_for_scale(pairs, _LARGE) == pairs


def test_the_whole_pipeline_lands_near_ivs_own_shape():
    """IV wrote 8 sections and 19 subsections for BTPN."""
    import proposal_templates
    ctx = {"client_name": "Bank BTPN", "iam_vendor": "ForgeRock",
           "proposal_type": "migration", "rfp_text": ""}
    answers = dict(_COMPACT, training="Out of scope")
    keep, _ = S.select_sections(proposal_templates.get_template("migration"), answers)
    total = 0
    for sec in keep:
        pairs = sec.render_subsections(ctx)
        pairs = S.filter_subsections(type("_S", (), {"subsections": pairs})(), answers)
        pairs = S.collapse_families(pairs, answers)
        pairs = S.cap_for_scale(pairs, answers)
        total += len(pairs)
    assert len(keep) <= 11, f"{len(keep)} sections against IV's 8"
    assert total <= 26, f"{total} subsections against IV's 19"


def test_the_scale_filters_are_wired_into_drafting():
    """CALL-SITE. The tests above pass whether or not document_engine calls
    them -- the built-but-never-wired failure this project has hit six times."""
    import inspect, document_engine
    src = inspect.getsource(document_engine.draft_section)
    assert "scope_filter.collapse_families" in src
    assert "scope_filter.cap_for_scale" in src


def test_the_implementation_task_list_exists():
    """IV's BTPN proposal carries a 34-row task list -- the largest artefact in
    it. Shilpi had nothing comparable; a RACI answers "who", not "what"."""
    import proposal_templates
    ctx = {"client_name": "X", "iam_vendor": "ForgeRock",
           "proposal_type": "migration", "rfp_text": ""}
    found = [(h, f) for sec in proposal_templates.get_template("migration")
             for h, f in sec.render_subsections(ctx)
             if "Implementation Task List" in h]
    assert found, "no Implementation Task List in the migration template"
    _, instruction = found[0]
    assert "Task, Comments" in instruction
    assert "15 rows" in instruction


# ---------------------------------------------------------------------------
# Engagement scale is a READING task, not a keyword match.
#
# Run 17 captured 95 of 96 fields -- the intake fix working -- and still
# produced 44 subsections against IV's 19. The keyword heuristic read the
# environments answer "Pre-Production and Production exist but are out of IV's
# scope", saw the word "production", and concluded production was IN scope. Same
# class of mistake as the colon-only parser: pattern matching where reading was
# required.
# ---------------------------------------------------------------------------

_RUN17 = {
    "app_count": "skip", "duration": "skip",
    "in_scope": "Upgrade the ForgeRock CIAM stack from V6.5.x to V7.3 on-premise",
    "out_of_scope": "end-user training is BTPN's responsibility",
    "is_migration": "true",
    "envs": ("Development, SIT and UAT are in IV's scope. Pre-Production and "
             "Production exist but are out of IV's scope"),
}


def test_production_named_but_excluded_reads_as_excluded():
    """THE run-17 failure, verbatim."""
    assert S.is_compact_engagement(_RUN17)


def test_production_genuinely_in_scope_still_reads_as_full():
    full = dict(_RUN17, envs="Production, DR, UAT and Development",
                in_scope="greenfield build")
    assert not S.is_compact_engagement(full)


def test_an_explicit_judgement_overrides_the_heuristic():
    """The model's reading wins; the heuristic is the offline fallback."""
    assert S.is_compact_engagement({S.SCALE_ANSWER_KEY: "compact"})
    assert not S.is_compact_engagement(
        {S.SCALE_ANSWER_KEY: "full", "app_count": "2", "duration": "4 weeks"})


def test_an_unrecognised_judgement_falls_back_to_the_heuristic():
    assert S.is_compact_engagement(dict(_RUN17, **{S.SCALE_ANSWER_KEY: "???"}))


def test_the_scale_judgement_is_wired_into_drafting():
    """CALL-SITE: the judgement must reach the filters, not just exist."""
    import inspect, document_engine
    src = inspect.getsource(document_engine.generate_proposal)
    assert "scale_fn(discovery_answers)" in src
    assert "scope_filter.SCALE_ANSWER_KEY" in src
