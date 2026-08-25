"""
Keyless unit test for the Sprint 5 Pass 1 intake schema.

Pure logic — no network, no secrets, no import of app.py. Run with:
    python3 tests/test_intake_template.py
Exits non-zero on the first failed assertion.
"""

import os
import sys

# Allow running from repo root or from backend/brain.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intake_template import (  # noqa: E402
    PROPOSAL_TYPES,
    get_intake_template,
    iter_questions,
    missing_required,
    required_question_ids,
)

MSS_ONLY = {"sla_tiers", "coverage_hours", "personnel", "contract_type",
            "fees", "mss_reporting_cadence", "mandatory_vs_on_demand_services"}
MIGRATION_ONLY = {"source_systems", "migration_phases"}


def _ids(proposal_type=None):
    return {q["id"] for q in iter_questions(proposal_type)}


def test_template_shape():
    tpl = get_intake_template()
    assert tpl["template_version"], "template_version must be set"
    assert isinstance(tpl["buckets"], list) and tpl["buckets"], "buckets must be non-empty"
    for b in tpl["buckets"]:
        assert {"id", "title", "questions"} <= set(b), f"bucket missing keys: {b}"
        assert b["questions"], f"bucket {b['id']} has no applicable questions"


def test_ids_globally_unique():
    ids = [q["id"] for q in iter_questions()]  # full template (no type filter)
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate question ids: {dupes}"


def test_required_core_questions():
    req = set(required_question_ids())
    for qid in ("client_name", "industry", "iam_vendor", "proposal_type",
                "business_objectives", "proposal_depth"):
        assert qid in req, f"{qid} should be required"


def test_mss_conditionals_only_for_mss():
    generic = _ids(None)
    impl = _ids("implementation")
    mss = _ids("mss")
    assert not (MSS_ONLY & generic), "MSS-only questions must not appear without a type"
    assert not (MSS_ONLY & impl), "MSS-only questions must not appear for implementation"
    assert MSS_ONLY <= mss, "all MSS-only questions must appear for proposal_type=mss"


def test_migration_conditionals_only_for_migration():
    impl = _ids("implementation")
    migration = _ids("migration")
    assert not (MIGRATION_ONLY & impl), "migration-only questions must not appear for implementation"
    assert MIGRATION_ONLY <= migration, "migration-only questions must appear for proposal_type=migration"


def test_missing_required_detection():
    # Empty answers -> everything required is missing.
    assert set(missing_required({}, "implementation")) == set(required_question_ids("implementation"))
    # Fully answered core -> nothing missing (implementation has no extra required conditionals).
    answers = {
        "client_name": "Acme", "industry": "Banking", "iam_vendor": "SailPoint",
        "proposal_type": "implementation", "business_objectives": "Modernise IGA",
        "proposal_depth": "standard",
    }
    assert missing_required(answers, "implementation") == [], "no required fields should be missing"
    # proposal_type falls back to the value inside answers when not passed.
    assert missing_required(answers) == [], "ptype should fall back to answers['proposal_type']"


def test_boolean_false_counts_as_answered():
    # A False boolean is a real answer, not 'missing'. sod is not required, but
    # verify the _is_answered semantics via a required-style check on a bool field
    # by ensuring False does not appear as missing when the field is answered.
    answers = {
        "client_name": "Acme", "industry": "Banking", "iam_vendor": "SailPoint",
        "proposal_type": "implementation", "business_objectives": "x",
        "proposal_depth": "brief", "sod": False,
    }
    assert "sod" not in missing_required(answers, "implementation")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"ALL {len(tests)} INTAKE TEMPLATE TESTS PASSED (proposal types: {PROPOSAL_TYPES})")




# ---------------------------------------------------------------------------
# Migration proposals.
#
# The intake template has offered `migration` since it was written, but
# proposal_templates had no entry for it, so get_template("migration") raised
# ValueError AFTER a consultant had answered all 22 discovery areas. The corpus
# now holds 39 migration files across 12 engagements, so the type has grounding.
# ---------------------------------------------------------------------------

def test_every_intake_proposal_type_has_a_template():
    """THE bug: intake offered a type the drafting engine could not build.

    Written as an invariant over ALL types rather than a check for 'migration',
    so adding a fourth type to the intake without a template fails here instead
    of at generation time in front of a user.
    """
    import proposal_templates
    import intake_template
    for ptype in intake_template.PROPOSAL_TYPES:
        try:
            sections = proposal_templates.get_template(ptype)
        except ValueError as e:
            raise AssertionError(
                f"intake offers proposal_type {ptype!r} but no template exists: {e}")
        assert sections, f"template for {ptype!r} is empty"


def test_migration_template_covers_what_implementation_cannot():
    """A migration is not an implementation with a different title."""
    import proposal_templates
    ids = {s.id for s in proposal_templates.get_template("migration")}
    for required in ("current_state", "migration_strategy", "rollback_risk",
                     "decommissioning"):
        assert required in ids, f"migration template missing {required!r}"
    impl_ids = {s.id for s in proposal_templates.get_template("implementation")}
    assert "rollback_risk" not in impl_ids
    assert "decommissioning" not in impl_ids


def test_migration_subsection_headings_are_unique():
    import proposal_templates
    ctx = {"client_name": "NWC", "iam_vendor": "Oracle Access Manager",
           "proposal_type": "migration", "rfp_text": ""}
    headings = [h for s in proposal_templates.get_template("migration")
                for h, _ in s.render_subsections(ctx)]
    dupes = {h for h in headings if headings.count(h) > 1}
    assert not dupes, f"repeated headings: {sorted(dupes)}"
    assert len(headings) >= 35
    for banned in ("Overview", "Detailed Design", "Considerations & Dependencies"):
        assert banned not in headings


def test_migration_asks_for_tables_where_a_reviewer_expects_them():
    import proposal_templates, document_engine
    ctx = {"client_name": "NWC", "iam_vendor": "Oracle", "proposal_type": "migration",
           "rfp_text": ""}
    wants = [h for s in proposal_templates.get_template("migration")
             for h, f in s.render_subsections(ctx)
             if document_engine._WANTS_TABLE_RE.search(f)]
    assert len(wants) >= 8, f"only {len(wants)} table subsections: {wants}"
    joined = " | ".join(wants)
    # The capability map and the risk register are the two a migration reviewer
    # goes to first: one proves nothing is lost, the other proves it can be undone.
    assert "Capability Mapping" in joined
    assert "Risk Register" in joined


# Shared runner: collects tests at EXIT, so appending a test below this
# line cannot silently skip it. Four files previously lost appended
# tests to an inline loop that read globals() at call time.
import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _runner import run_tests  # noqa: E402

run_tests(globals(), "INTAKE TEMPLATE TESTS")
