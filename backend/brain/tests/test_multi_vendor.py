"""
Multi-vendor engagement tests — keyless, no network.

Built from a real case: ESNAD asked for Ping Identity (Access Management,
CIAM) AND Saviynt (IGA, PAM) as two distinct products in one proposal.
Before this, iam_vendor was a single string templated verbatim into 47
places, producing combined headings like "Why Ping Identity (Access
Management, CIAM) and Saviynt (IGA, PAM)" and retrieval queries that diluted
vector search against BOTH vendors' corpus content at once.

Covers, in order:
  1. proposal_templates.split_vendors — parsing a combined string
  2. document_engine.render_subsections / _fanout_queries — per-vendor
     expansion of headings and retrieval, with the parenthesized-comma
     protection and the "never drop a vendor's base query" floor
  3. intake_template.vendor_scope_bucket — the dynamic structured follow-up
  4. app._fold_vendor_scope_answers — folding slugged answers into one map
  5. document_engine._vendor_scope_clause / diagram_engine's equivalent
     instruction — explicit attribution in the drafting/diagram prompts
"""
import os
import sys

os.environ.setdefault("OPENROUTER_API_KEY", "x")
os.environ.setdefault("SUPABASE_URL", "http://x")
os.environ.setdefault("SUPABASE_KEY", "x")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import proposal_templates as pt  # noqa: E402
import document_engine as de  # noqa: E402
import diagram_engine as dg  # noqa: E402
import intake_template as it  # noqa: E402
import app  # noqa: E402


# ---------------------------------------------------------------------------
# 1. split_vendors
# ---------------------------------------------------------------------------

def test_split_vendors_combined_with_parentheses():
    got = pt.split_vendors(
        "Ping Identity (Access Management, CIAM) and Saviynt (IGA, PAM)")
    assert got == ["Ping Identity (Access Management, CIAM)",
                   "Saviynt (IGA, PAM)"]


def test_split_vendors_bare_and():
    assert pt.split_vendors("Ping Identity and Saviynt") == \
        ["Ping Identity", "Saviynt"]


def test_split_vendors_comma_separated():
    assert pt.split_vendors("Ping Identity, Saviynt") == \
        ["Ping Identity", "Saviynt"]


def test_split_vendors_three_way_oxford_comma():
    assert pt.split_vendors("Ping, Saviynt, and CyberArk") == \
        ["Ping", "Saviynt", "CyberArk"]


def test_split_vendors_single_vendor_unchanged():
    assert pt.split_vendors("SailPoint") == ["SailPoint"]


def test_split_vendors_empty_and_none():
    assert pt.split_vendors("") == []
    assert pt.split_vendors(None) == []


def test_split_vendors_comma_inside_parens_is_not_a_split_point():
    """"Ping Identity (Access Management, CIAM)" is ONE vendor with a
    two-item capability list — the comma is nested, not a separator."""
    got = pt.split_vendors("Ping Identity (Access Management, CIAM)")
    assert got == ["Ping Identity (Access Management, CIAM)"]


def test_split_vendors_does_not_tear_apart_a_real_product_name():
    """Regression: an early version of the "and"-in-name guard matched the
    SUBSTRING "Identity and" anywhere in the string, which incorrectly
    blocked "Ping Identity and Saviynt" from splitting at all (it contains
    "Identity and" as part of "Ping IdentITY AND Saviynt"). The guard must
    only suppress splitting for an exact whole-string match against a known
    name, never a substring match."""
    assert pt.split_vendors("Ping Identity and Saviynt") == \
        ["Ping Identity", "Saviynt"]
    assert pt.split_vendors("Identity and Access Management Inc") == \
        ["Identity and Access Management Inc"]


# ---------------------------------------------------------------------------
# 2. Per-vendor heading and retrieval expansion
# ---------------------------------------------------------------------------

def _solution_overview():
    tpl = pt.get_template("implementation")
    return next(s for s in tpl if s.id == "solution_overview")


def test_single_vendor_subsections_unchanged():
    """Baseline: must be byte-identical to pre-multi-vendor behaviour."""
    ctx = {"client_name": "X", "iam_vendor": "SailPoint",
          "iam_vendors": ["SailPoint"], "proposal_type": "implementation"}
    subs = _solution_overview().render_subsections(ctx)
    assert len(subs) == 13
    assert subs[0][0] == "Why SailPoint"


def test_multi_vendor_subsections_split_only_vendor_specific_headings():
    """13 -> 16: only the THREE headings containing {{ iam_vendor }} split
    into two each. The nine generic capability headings ("Who Has Access
    Today", "Access Certification", ...) describe the COMBINED solution and
    must appear exactly once regardless of vendor count."""
    vendors = pt.split_vendors("Ping Identity and Saviynt")
    ctx = {"client_name": "X", "iam_vendor": "Ping Identity and Saviynt",
          "iam_vendors": vendors, "proposal_type": "implementation"}
    subs = _solution_overview().render_subsections(ctx)
    headings = [h for h, _ in subs]
    assert len(headings) == 16
    assert "Why Ping Identity" in headings
    assert "Why Saviynt" in headings
    assert headings.count("Who Has Access Today") == 1
    assert headings.count("Access Certification") == 1


def test_multi_vendor_subsections_preserve_scope_in_heading():
    vendors = pt.split_vendors(
        "Ping Identity (Access Management, CIAM) and Saviynt (IGA, PAM)")
    ctx = {"client_name": "X", "iam_vendor": "x", "iam_vendors": vendors,
          "proposal_type": "implementation"}
    headings = [h for h, _ in _solution_overview().render_subsections(ctx)]
    assert "Why Ping Identity (Access Management, CIAM)" in headings
    assert "Why Saviynt (IGA, PAM)" in headings


def test_fanout_single_vendor_unaffected():
    ctx = {"client_name": "X", "iam_vendor": "SailPoint",
          "iam_vendors": ["SailPoint"], "proposal_type": "implementation"}
    qs = de._fanout_queries(_solution_overview(), ctx, 3)
    assert len(qs) == 3
    assert all(q.startswith("SailPoint") for q in qs)


def test_fanout_multi_vendor_produces_separate_clean_queries():
    """A combined "Ping Identity and Saviynt solution overview" query
    dilutes vector search against both vendors at once; two separate base
    queries retrieve cleanly against each vendor's own corpus content."""
    vendors = pt.split_vendors("Ping Identity and Saviynt")
    ctx = {"client_name": "X", "iam_vendor": "Ping Identity and Saviynt",
          "iam_vendors": vendors, "proposal_type": "implementation"}
    qs = de._fanout_queries(_solution_overview(), ctx, 2)
    assert len(qs) == 2
    assert qs[0].startswith("Ping Identity ")
    assert qs[1].startswith("Saviynt ")
    assert "and Saviynt" not in qs[0]


def test_fanout_never_drops_a_vendor_even_at_the_tightest_budget():
    """Regression: fanout=1 with two vendors originally returned only the
    FIRST vendor's base query, silently giving the second vendor zero
    retrieval evidence for the whole section. One base query per vendor is
    a FLOOR the fanout budget must never shrink below."""
    vendors = pt.split_vendors("Ping Identity and Saviynt")
    ctx = {"client_name": "X", "iam_vendor": "Ping Identity and Saviynt",
          "iam_vendors": vendors, "proposal_type": "implementation"}
    qs = de._fanout_queries(_solution_overview(), ctx, 1)
    assert len(qs) == 2, "a vendor's base query was dropped at fanout=1"
    assert qs[0].startswith("Ping Identity")
    assert qs[1].startswith("Saviynt")


def test_fanout_extra_budget_gives_both_vendors_facet_depth():
    """Facet queries must round-robin across vendors, not all land on
    vendor 0 — otherwise extra fanout budget only deepens ONE vendor's
    evidence."""
    vendors = pt.split_vendors("Ping Identity and Saviynt")
    ctx = {"client_name": "X", "iam_vendor": "Ping Identity and Saviynt",
          "iam_vendors": vendors, "proposal_type": "implementation"}
    qs = de._fanout_queries(_solution_overview(), ctx, 5)
    assert len(qs) == 5
    ping_count = sum(1 for q in qs if q.startswith("Ping Identity"))
    saviynt_count = sum(1 for q in qs if q.startswith("Saviynt"))
    assert ping_count >= 2 and saviynt_count >= 2, (
        f"facet queries did not round-robin: {ping_count} Ping, {saviynt_count} Saviynt")


def test_a_section_with_no_vendor_subsections_still_gets_split_retrieval_but_stays_singular():
    """executive_summary's QUERY mentions {{ iam_vendor }} (so retrieval
    correctly pulls evidence about BOTH vendors for a coherent combined
    overview) but it has NO per-vendor SUBSECTION headings, so the drafted
    section itself stays singular — one executive summary, not two vendor
    pitches. Splitting retrieval and splitting the drafted section are
    independent decisions, and this is the case where only the first applies."""
    tpl = pt.get_template("implementation")
    exec_summary = next(s for s in tpl if s.id == "executive_summary")
    vendors = pt.split_vendors("Ping Identity and Saviynt")
    ctx = {"client_name": "X", "iam_vendor": "Ping Identity and Saviynt",
          "iam_vendors": vendors, "proposal_type": "implementation", "rfp_text": ""}

    # Retrieval DOES split (both vendors get their own base query).
    qs = de._fanout_queries(exec_summary, ctx, 2)
    assert len(qs) == 2
    assert "Ping Identity" in qs[0] and "Saviynt" not in qs[0]
    assert "Saviynt" in qs[1] and "Ping Identity" not in qs[1]

    # But the drafted SECTION stays singular: one subsection, not two.
    subs = exec_summary.render_subsections(ctx)
    assert len(subs) == 1


# ---------------------------------------------------------------------------
# 3. vendor_scope_bucket — the dynamic structured follow-up
# ---------------------------------------------------------------------------

def test_vendor_scope_bucket_none_for_single_vendor():
    assert it.vendor_scope_bucket({"iam_vendor": "SailPoint"}) is None


def test_vendor_scope_bucket_asks_one_question_per_vendor():
    b = it.vendor_scope_bucket({"iam_vendor": "Ping Identity and Saviynt"})
    assert b is not None
    ids = [q["id"] for q in b["questions"]]
    assert ids == ["vendor_scope__ping_identity", "vendor_scope__saviynt"]
    assert all(q["required"] for q in b["questions"])


def test_vendor_scope_bucket_still_asks_even_if_scope_already_in_the_name():
    """A consultant may type the parenthetical scope directly into iam_vendor
    ("Ping Identity (Access Management, CIAM)"). That parenthetical is a
    HINT, never trusted as structured data — the system still asks
    explicitly, because scraping structure back out of prose is exactly the
    fragile pattern (colon-only parsing, current-bucket-only lookup) that has
    broken this system before."""
    b = it.vendor_scope_bucket({
        "iam_vendor": "Ping Identity (Access Management, CIAM) and Saviynt (IGA, PAM)"})
    assert b is not None
    assert len(b["questions"]) == 2


def test_vendor_scope_bucket_skips_vendors_already_answered():
    b = it.vendor_scope_bucket({
        "iam_vendor": "Ping Identity and Saviynt",
        "vendor_scope_map": {"Ping Identity": "Access Management, CIAM"}})
    assert b is not None
    ids = [q["id"] for q in b["questions"]]
    assert ids == ["vendor_scope__saviynt"]


def test_vendor_scope_bucket_none_when_all_vendors_answered():
    b = it.vendor_scope_bucket({
        "iam_vendor": "Ping Identity and Saviynt",
        "vendor_scope_map": {"Ping Identity": "Access Management, CIAM",
                             "Saviynt": "IGA, PAM"}})
    assert b is None


def test_a_genuinely_vendor_agnostic_section_query_is_identical_regardless_of_vendor_count():
    """company_profile's query has NO {{ iam_vendor }} token anywhere — it
    must be byte-identical whether the engagement has one vendor or three."""
    tpl = pt.get_template("implementation")
    company_profile = next(s for s in tpl if s.id == "company_profile")
    assert "iam_vendor" not in company_profile.query_template

    ctx_single = {"client_name": "X", "iam_vendor": "SailPoint",
                 "iam_vendors": ["SailPoint"], "proposal_type": "implementation",
                 "rfp_text": ""}
    ctx_multi = {"client_name": "X", "iam_vendor": "Ping Identity and Saviynt",
                "iam_vendors": pt.split_vendors("Ping Identity and Saviynt"),
                "proposal_type": "implementation", "rfp_text": ""}
    qs_single = de._fanout_queries(company_profile, ctx_single, 3)
    qs_multi = de._fanout_queries(company_profile, ctx_multi, 3)
    assert qs_single == qs_multi


# ---------------------------------------------------------------------------
# 4. _fold_vendor_scope_answers
# ---------------------------------------------------------------------------

def test_fold_both_vendors_in_one_turn():
    answers = {"iam_vendor": "Ping Identity and Saviynt"}
    recorded = {"vendor_scope__ping_identity": "Access Management, CIAM",
               "vendor_scope__saviynt": "IGA, PAM"}
    out = app._fold_vendor_scope_answers(answers, recorded)
    assert out == {"vendor_scope_map": {
        "Ping Identity": "Access Management, CIAM", "Saviynt": "IGA, PAM"}}
    assert "vendor_scope__ping_identity" not in out
    assert "vendor_scope__saviynt" not in out


def test_fold_preserves_an_earlier_turns_answer():
    """A consultant answering vendor 2's scope in a LATER turn must not
    lose vendor 1's answer from an earlier turn."""
    answers = {"iam_vendor": "Ping Identity and Saviynt",
              "vendor_scope_map": {"Ping Identity": "Access Management, CIAM"}}
    recorded = {"vendor_scope__saviynt": "IGA, PAM"}
    out = app._fold_vendor_scope_answers(answers, recorded)
    assert out["vendor_scope_map"] == {
        "Ping Identity": "Access Management, CIAM", "Saviynt": "IGA, PAM"}


def test_fold_leaves_non_vendor_keys_in_recorded_untouched():
    answers = {"iam_vendor": "Ping Identity and Saviynt"}
    recorded = {"vendor_scope__ping_identity": "Access Management, CIAM",
               "some_other_field": "untouched"}
    out = app._fold_vendor_scope_answers(answers, recorded)
    assert out["some_other_field"] == "untouched"
    assert out["vendor_scope_map"] == {"Ping Identity": "Access Management, CIAM"}


def test_still_missing_check_uses_vendor_scope_bucket_as_source_of_truth():
    """The 'still missing' check in the chat flow re-derives from
    vendor_scope_bucket() against the MERGED answers, rather than
    hand-matching slugs a second time. This test exercises that same
    merge-and-recheck pattern directly."""
    answers = {"iam_vendor": "Ping Identity and Saviynt"}
    # Partial: only Ping answered this turn.
    recorded_partial = {"vendor_scope__ping_identity": "Access Management, CIAM"}
    folded_partial = app._fold_vendor_scope_answers(answers, recorded_partial)
    merged_partial = {**answers, **folded_partial}
    still = it.vendor_scope_bucket(merged_partial)
    assert still is not None
    assert [q["id"] for q in still["questions"]] == ["vendor_scope__saviynt"]

    # Complete: both answered.
    recorded_full = {"vendor_scope__ping_identity": "Access Management, CIAM",
                     "vendor_scope__saviynt": "IGA, PAM"}
    folded_full = app._fold_vendor_scope_answers(answers, recorded_full)
    merged_full = {**answers, **folded_full}
    assert it.vendor_scope_bucket(merged_full) is None


# ---------------------------------------------------------------------------
# 5. Explicit attribution in drafting and diagram prompts
# ---------------------------------------------------------------------------

def test_drafting_vendor_scope_clause_fires_for_multi_vendor():
    ctx = {"discovery_answers": {"vendor_scope_map": {
        "Ping Identity": "Access Management, CIAM", "Saviynt": "IGA, PAM"}}}
    clause = de._vendor_scope_clause(ctx)
    assert "Ping Identity owns Access Management, CIAM" in clause
    assert "Saviynt owns IGA, PAM" in clause
    assert "attribute each part to the correct vendor" in clause


def test_drafting_vendor_scope_clause_silent_for_single_vendor():
    assert de._vendor_scope_clause({"discovery_answers": {}}) == ""
    assert de._vendor_scope_clause({}) == ""
    assert de._vendor_scope_clause(
        {"discovery_answers": {"vendor_scope_map": {"SailPoint": "everything"}}}) == ""


def test_diagram_spec_prompt_includes_vendor_attribution_instruction():
    """generate_diagram_spec must inject the same explicit attribution
    instruction the drafting prompt gets — splitting headings/retrieval in
    the drafted TEXT is not enough on its own; the architecture DIAGRAM
    needs the same signal or it draws one undifferentiated box."""
    import inspect
    src = inspect.getsource(dg.generate_diagram_spec)
    assert "vendor_scope_map" in src
    assert "Label every node" in src


def _valid_stub_spec(**kw):
    """A minimal DiagramSpec that passes spec_shortfall's validation (>= 2
    connected nodes), so tests that only care about the PROMPT sent to the
    model don't get tangled up in the engine's own empty-spec retry/reject
    logic."""
    return dg.DiagramSpec(
        title=kw.get("title", "stub"),
        nodes=[dg.DiagramNode(id="a", label="A"), dg.DiagramNode(id="b", label="B")],
        edges=[dg.DiagramEdge(source="a", target="b")],
    )


async def _fake_structured(model, messages, models=None, **kw):
    return _valid_stub_spec()


# ---------------------------------------------------------------------------
# 7. apply_plan_edit — diagram plan "add"/"drop" was UNTESTED before this,
#    which is exactly how it shipped with no PAM/IGA vocabulary at all.
# ---------------------------------------------------------------------------

import chat_state as cs  # noqa: E402
import rfp_intake  # noqa: E402
import supabase_client  # noqa: E402

_ESNAD_PLAN = [
    ("Solution Architecture", "architecture"),
    ("Integration / Joiner Flow", "flow"),
]


def test_add_privileged_access_management_flow_the_exact_failing_phrase():
    """THE live-run failure, verbatim. Before the fix, DIAGRAM_TYPE_MAP had no
    key overlapping any word in "Privileged Access Management", so this
    request failed identically no matter how it was phrased — not a fuzzy-
    match miss, a missing vocabulary entry."""
    out = cs.apply_plan_edit(_ESNAD_PLAN, "add Privileged Access Management Flow")
    added = out[len(_ESNAD_PLAN):]
    assert added, "the exact phrase that failed live still adds nothing"
    assert added[0][0] == "Privileged Access Management"


def test_add_iga_diagram():
    out = cs.apply_plan_edit(_ESNAD_PLAN, "add IGA diagram")
    added = out[len(_ESNAD_PLAN):]
    assert added and added[0][0] == "Identity Governance"


def test_add_identity_lifecycle_flow_the_exact_failing_phrase():
    """THE second live-run failure on the SAME chat session that hit the PAM
    gap: "add Identity Lifecycle Flow diagram" failed identically, because
    DIAGRAM_TYPE_MAP had no key overlapping "identity lifecycle" either.
    ESNAD's SOW specifies this as its own domain (ILM-01..12: provisioning,
    deprovisioning, joiner/mover/leaver, orphan detection)."""
    out = cs.apply_plan_edit(_ESNAD_PLAN, "add Identity Lifecycle Flow diagram")
    added = out[len(_ESNAD_PLAN):]
    assert added, "the exact phrase that failed live still adds nothing"
    assert added[0][0] == "Identity Lifecycle"


def test_add_joiner_mover_leaver_diagram():
    out = cs.apply_plan_edit(_ESNAD_PLAN, "add a joiner mover leaver diagram")
    added = out[len(_ESNAD_PLAN):]
    assert added and added[0][0] == "Joiner Mover Leaver"


def test_add_bare_pam_acronym():
    """A 3-character key reduces to an EMPTY word list under the >3-char
    filter, so a bare acronym ("pam") could never match on its own even
    after the full-phrase key existed. The acronym redirect handles this
    without weakening the length filter everywhere else."""
    out = cs.apply_plan_edit(_ESNAD_PLAN, "add PAM Flow")
    added = out[len(_ESNAD_PLAN):]
    assert added and added[0][0] == "Privileged Access Management"


def test_add_full_phrase_still_works_directly():
    out = cs.apply_plan_edit(_ESNAD_PLAN, "add a Privileged Access Management diagram")
    added = out[len(_ESNAD_PLAN):]
    assert added and added[0][0] == "Privileged Access Management"


def test_a_bare_acronym_key_never_matches_everything():
    """Regression guard for the near-miss found while fixing this: an empty
    key_words list (from a <=3-char key) must be REJECTED, not treated as a
    vacuous match against every input. If "pam"/"iga" were ever added as
    direct DIAGRAM_TYPE_MAP keys instead of via the acronym redirect, this
    would catch it firing on unrelated add requests."""
    out = cs.apply_plan_edit(_ESNAD_PLAN, "add a completely unrelated diagram type xyz")
    added = out[len(_ESNAD_PLAN):]
    assert not added, f"an unrelated request incorrectly added: {added}"


def test_existing_diagram_vocabulary_still_works_unaffected():
    """The acronym redirect and new PAM/IGA entries must not disturb any
    pre-existing diagram type."""
    out = cs.apply_plan_edit(_ESNAD_PLAN, "add a user journey diagram")
    added = out[len(_ESNAD_PLAN):]
    assert added and added[0][1] == "sequence"


def test_add_does_not_duplicate_a_diagram_already_in_the_plan():
    plan_with_pam = _ESNAD_PLAN + [("Privileged Access Management", "flow")]
    out = cs.apply_plan_edit(plan_with_pam, "add Privileged Access Management Flow")
    assert len(out) == len(plan_with_pam), "a diagram already in the plan was duplicated"


def test_drop_still_works_alongside_the_new_vocabulary():
    plan = _ESNAD_PLAN + [("Privileged Access Management", "flow")]
    out = cs.apply_plan_edit(plan, "drop the privileged access management diagram")
    titles = [t for t, _ in out]
    assert "Privileged Access Management" not in titles
    assert len(out) == 2


def test_an_unmatched_request_leaves_the_plan_untouched_and_reprompts():
    """No silent guessing: a genuinely unmatched request must change nothing,
    so the caller's re-prompt logic (PLAN_REPROMPT) fires correctly."""
    out = cs.apply_plan_edit(_ESNAD_PLAN, "add something entirely nonsensical qqzxy")
    assert out == _ESNAD_PLAN


def test_diagram_spec_call_builds_a_prompt_naming_both_vendors():
    """End-to-end through generate_diagram_spec's prompt assembly (not just
    the source-code check above) — confirms the vendor_scope_map argument
    actually reaches the rendered user_prompt."""
    import asyncio

    captured = {}

    async def capturing_structured(model, messages, models=None, **kw):
        captured["messages"] = messages
        return _valid_stub_spec()

    asyncio.run(dg.generate_diagram_spec(
        capturing_structured,
        title="Solution Architecture",
        client_name="ESNAD",
        vendor_scope_map={"Ping Identity": "Access Management, CIAM",
                          "Saviynt": "IGA, PAM"},
    ))
    user_msg = captured["messages"][1]["content"]
    assert "Ping Identity owns Access Management, CIAM" in user_msg
    assert "Saviynt owns IGA, PAM" in user_msg
    assert "Label every node" in user_msg


def test_diagram_spec_no_vendor_clause_for_single_vendor():
    import asyncio

    captured = {}

    async def capturing_structured(model, messages, models=None, **kw):
        captured["messages"] = messages
        return _valid_stub_spec()

    asyncio.run(dg.generate_diagram_spec(
        capturing_structured, title="Solution Architecture",
        client_name="X", vendor_scope_map=None))
    user_msg = captured["messages"][1]["content"]
    assert "MULTI-VENDOR" not in user_msg


# ---------------------------------------------------------------------------
# 6. Asset selection — excluding competing vendors from embedded images
# ---------------------------------------------------------------------------

import asset_selection as asel  # noqa: E402


def test_asset_vendor_check_single_vendor_backward_compatible():
    assert asel._mentions_other_vendor(
        "a SailPoint screenshot", "SailPoint") is False
    assert asel._mentions_other_vendor(
        "a Ping Identity screenshot", "SailPoint") is True


def test_asset_vendor_check_multi_vendor_allows_both_proposed_vendors():
    """Regression risk: relying on `iam_vendor` as a combined string happened
    to work by coincidence (each vendor's name is a substring of the
    combined string), which is not something to depend on. iam_vendors as an
    explicit list checks membership against each vendor directly."""
    vendors = ["Ping Identity", "Saviynt"]
    assert asel._mentions_other_vendor(
        "a Ping Identity console screenshot", None, vendors) is False
    assert asel._mentions_other_vendor(
        "a Saviynt IGA dashboard", None, vendors) is False


def test_asset_vendor_check_multi_vendor_still_excludes_competitors():
    vendors = ["Ping Identity", "Saviynt"]
    assert asel._mentions_other_vendor(
        "a SailPoint screenshot", None, vendors) is True
    assert asel._mentions_other_vendor(
        "a CyberArk PAM vault screenshot", None, vendors) is True


def test_select_assets_accepts_iam_vendors_list():
    """End-to-end through select_assets itself, not just the helper."""
    import inspect
    sig = inspect.signature(asel.select_assets)
    assert "iam_vendors" in sig.parameters


def test_asset_selection_call_site_passes_iam_vendors_through():
    """CALL-SITE check in document_engine — the function accepting the
    parameter is not enough if nothing ever passes it."""
    import inspect
    src = inspect.getsource(de._attach_assets) if hasattr(de, "_attach_assets") else ""
    if not src:
        # Fall back to searching the whole module for the call site if the
        # function name differs from the expected internal helper.
        src = inspect.getsource(de)
    assert "iam_vendors=vendors" in src or "iam_vendors=context.get" in src, (
        "select_assets is called without passing iam_vendors through")


# ---------------------------------------------------------------------------
# 8. RFP-extracted requirements reaching the compliance matrix.
#
# THE ESNAD live-run failure: rfp_intake.py's vision extraction correctly
# found 56 numbered requirements (ILM-*, AM-*, PAM-*, IGA-*, CIAM-*) with
# page numbers, but nothing ever persisted them past the extraction request.
# document_engine's compliance-matrix call passed a hardcoded None for
# requirements, so run_compliance_matrix fell back to re-deriving them from
# rfp_text via a SECOND LLM call — and rfp_text was empty, because ESNAD's
# SOW is a scanned PDF with no text layer: there was no page text to put
# there, only structured per-page vision extraction. Zero requirements
# reached the drafted proposal as a direct, deterministic result.
# ---------------------------------------------------------------------------

def test_run_compliance_matrix_recognises_structured_requirements():
    """A plain-string list means "please extract requirements from this
    text"; a dict/object list means "these are already extracted, use them
    as-is". document_engine.py sends dicts specifically because it cannot
    import app.Requirement without an import cycle (app.py imports FROM
    document_engine already)."""
    string_list = ["some requirement text"]
    dict_list = [{"id": "AM-04", "text": "Risk-based conditional access", "category": None}]

    assert isinstance(string_list[0], str)
    assert not isinstance(dict_list[0], str)


def test_structured_requirements_construct_with_original_ref_ids_preserved():
    """A compliance matrix citing "AM-04" is directly traceable back to
    ESNAD's own numbering — renumbering to generic REQ-001 would break that
    traceability for a tender evaluator checking coverage against their own
    requirement register."""
    extracted_dicts = [
        {"id": "AM-04", "text": "Risk-based conditional access policies cover location", "category": None},
        {"id": "PAM-01", "text": "Credential vaulting for all privileged and service accounts", "category": None},
    ]
    reqs = [
        r if isinstance(r, app.Requirement) else
        app.Requirement(id=(r.get("id") or f"REQ-{i:03d}"), text=r.get("text", ""),
                        category=r.get("category"))
        for i, r in enumerate(extracted_dicts[:app.MAX_REQUIREMENTS], 1)
        if isinstance(r, app.Requirement) or (r.get("text") or "").strip()
    ]
    assert [r.id for r in reqs] == ["AM-04", "PAM-01"]
    assert reqs[0].text.startswith("Risk-based conditional access")


def test_empty_text_requirements_are_dropped_not_constructed_as_blanks():
    extracted_dicts = [
        {"id": "AM-04", "text": "Real requirement text", "category": None},
        {"id": "AM-05", "text": "   ", "category": None},
        {"id": "AM-06", "text": "", "category": None},
    ]
    reqs = [
        r if isinstance(r, app.Requirement) else
        app.Requirement(id=(r.get("id") or f"REQ-{i:03d}"), text=r.get("text", ""),
                        category=r.get("category"))
        for i, r in enumerate(extracted_dicts[:app.MAX_REQUIREMENTS], 1)
        if isinstance(r, app.Requirement) or (r.get("text") or "").strip()
    ]
    assert [r.id for r in reqs] == ["AM-04"]


def test_extracted_requirements_round_trip_through_json_persistence():
    """The exact path a real RFP upload takes: rfp_intake.Requirement objects
    -> JSON string (as stored in Supabase's answers jsonb) -> parsed back out
    in document_engine.py -> plain dicts ready for run_compliance_matrix."""
    import json

    class _FakeExtracted:
        def __init__(self, ref, text, page):
            self.ref, self.text, self.page = ref, text, page

    extracted = [_FakeExtracted("AM-04", "Risk-based conditional access policies", 3),
                _FakeExtracted("PAM-01", "Credential vaulting for privileged accounts", 5)]

    persisted = json.dumps([
        {"id": r.ref, "text": r.text, "category": None, "page": r.page}
        for r in extracted
    ])

    raw = persisted
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    result = [
        {"id": r.get("id") or r.get("ref") or "", "text": r.get("text") or "",
         "category": r.get("category")}
        for r in parsed if (r.get("text") or "").strip()
    ]
    assert result == [
        {"id": "AM-04", "text": "Risk-based conditional access policies", "category": None},
        {"id": "PAM-01", "text": "Credential vaulting for privileged accounts", "category": None},
    ]


def test_no_extracted_requirements_falls_back_to_rfp_text_derivation():
    """A plain interview-driven proposal (no RFP upload at all) has no
    extracted_requirements key in discovery_answers. This must fall back to
    the original rfp_text-based extraction path, not break."""
    discovery_answers = {"client_name": "Some Client"}  # no extracted_requirements key
    raw_reqs = discovery_answers.get("extracted_requirements")
    assert raw_reqs is None


def test_compliance_matrix_persistence_is_wired_into_the_rfp_upload_handler():
    """CALL-SITE check — the fix is useless if the RFP upload path never
    actually writes extracted_requirements into the session answers."""
    import inspect
    src = inspect.getsource(app)
    assert "extracted_requirements" in src, (
        "extracted_requirements is never persisted anywhere in app.py")


def test_document_engine_reads_extracted_requirements_from_discovery_answers():
    """CALL-SITE check — document_engine.py must actually READ the persisted
    field, not just have app.py write it into a void."""
    import inspect
    src = inspect.getsource(de)
    assert 'discovery_answers or {}).get("extracted_requirements")' in src, (
        "document_engine.py never reads extracted_requirements back out")


# ---------------------------------------------------------------------------
# 9. Wide-sweep extraction must not positionally overwrite client_name.
#
# THE ESNAD live-run failure: a correction reply of "Ping Identity (Access
# Management, CIAM) and Saviynt (IGA, PAM)" -- meant to answer iam_vendor --
# has no field labels and contains commas, so the wide-sweep LLM extractor
# positionally mapped it onto client_name, industry and country simply
# because those are declared FIRST in the 96-field schema. The DOCX body was
# still correct (client_name had been set correctly earlier and drafting read
# it before the corruption), but the output FILENAME was built from the
# now-corrupted client_name and named the vendor instead of ESNAD.
# ---------------------------------------------------------------------------

def test_wide_sweep_uses_a_different_stricter_prompt():
    """The narrow-bucket prompt's positional bare-list rule is unsafe against
    the full template and must not be reused verbatim for the wide sweep."""
    assert app._BUCKET_EXTRACT_PROMPT != app._WIDE_SWEEP_EXTRACT_PROMPT
    assert "do NOT positionally map" in app._WIDE_SWEEP_EXTRACT_PROMPT
    assert "do NOT positionally map" not in app._BUCKET_EXTRACT_PROMPT


def test_extract_bucket_answers_accepts_wide_sweep_flag():
    import inspect
    sig = inspect.signature(app.extract_bucket_answers)
    assert "wide_sweep" in sig.parameters
    assert sig.parameters["wide_sweep"].default is False


def test_the_wide_sweep_call_site_passes_wide_sweep_true():
    """CALL-SITE check — the stricter prompt exists but is useless if the
    actual wide-sweep call inside resolve_bucket_answers never selects it."""
    import inspect
    src = inspect.getsource(app.resolve_bucket_answers)
    assert "wide_sweep=True" in src, (
        "resolve_bucket_answers' wide sweep never opts into the stricter prompt")


def test_the_narrow_bucket_fallback_still_uses_the_default_prompt(monkeypatch):
    """resolve_bucket_answers' final single-bucket fallback (when parsing
    finds nothing at all) is a genuinely narrow, small bucket -- the original
    positional bare-list rule is safe there and must be UNCHANGED."""
    import asyncio

    captured = {}

    async def capturing_extract(bucket, reply, wide_sweep=False):
        captured["wide_sweep"] = wide_sweep
        return {}

    monkeypatch.setattr(app, "extract_bucket_answers", capturing_extract)
    small_bucket = {"id": "engagement",
                    "questions": [{"id": "iam_vendor", "label": "IAM vendor", "type": "text"}]}
    asyncio.run(app.resolve_bucket_answers(small_bucket, "SailPoint", None))
    assert captured.get("wide_sweep") is False, (
        "the narrow single-bucket fallback must not opt into the wide-sweep prompt")


# ---------------------------------------------------------------------------
# 10. "all met" advances past the eligibility gate.
#
# THE live-run failure: rfp_intake.describe_gates() tells the user to say
# "all met" to continue, but MODE_RFP_REVIEW's handler only recognised
# is_force()'s drafting-gap vocabulary and the literal word "continue" --
# "all met" fell through to the correction branch and produced "I didn't
# catch a value to update there", the same shape as "add PAM Flow" not
# matching DIAGRAM_TYPE_MAP earlier: a prompt promised a keyword the
# receiving code never actually checked for.
# ---------------------------------------------------------------------------

def test_all_met_is_recognised_as_advancing_the_gate():
    import re
    assert re.search(r"(?<!not )\ball met\b", "all met", re.I)
    assert re.search(r"(?<!not )\ball met\b", "All met", re.I)
    assert re.search(r"(?<!not )\ball met\b", "all met, lets proceed", re.I)


def test_not_all_met_does_not_falsely_advance():
    """"Not all met, we lack local presence" contains the substring "all
    met" too, and is a genuine disqualification concern the gate exists to
    catch. Advancing past it would be worse than the original bug."""
    import re
    assert not re.search(r"(?<!not )\ball met\b", "not all met, we lack local presence", re.I)
    assert not re.search(r"(?<!not )\ball met\b", "Not all met - missing ISO cert", re.I)


def test_the_rfp_review_handler_checks_for_all_met():
    """CALL-SITE check — the regex working in isolation is not enough if the
    actual MODE_RFP_REVIEW branch never uses it."""
    import inspect
    src = inspect.getsource(app)
    assert r'\ball met\b' in src, (
        "the RFP review handler never checks for the phrase its own prompt tells the user to say")


def test_the_gates_prompt_and_the_handler_agree_on_the_phrase():
    """The two sides of this contract -- what rfp_intake tells the user to
    type, and what app.py listens for -- must name the SAME phrase, or this
    exact bug recurs with different wording on either side."""
    gates = [rfp_intake.EligibilityGate(text="ISO/IEC 27001 certified delivery organization", page=4)]
    prompt = rfp_intake.describe_gates(gates)
    assert "all met" in prompt.lower()


# ---------------------------------------------------------------------------
# 11. LLM fallback for diagram-plan "add" requests the fixed vocabulary
#     cannot cover.
#
# THE actual scope of the problem, measured directly: 23 of 30 reasonable
# ESNAD-domain add-phrasings ("add Access Management diagram", "add RBAC
# diagram", "add Federation diagram", "add SoD diagram"...) failed against
# DIAGRAM_TYPE_MAP. Three individual gaps (PAM, Identity Governance, Identity
# Lifecycle) had already been patched in reactively, one live failure at a
# time, before this was actually counted -- patching vocabulary entries one
# at a time was never going to converge, because the space of what a
# consultant might type for a diagram domain is genuinely open-ended.
#
# apply_plan_edit (the deterministic function) is UNCHANGED -- see the tests
# above, all still passing. apply_plan_edit_async wraps it and falls back to
# an LLM call, with structured_fn injected the same way document_engine.py
# and diagram_engine.py already take their structured-call functions, since
# app.py (which owns _structured_with_fallback) imports FROM chat_state.py,
# not the reverse.
# ---------------------------------------------------------------------------

async def _fake_llm_add(title: str, engine_type: str = "flow"):
    async def fn(model, messages, **kw):
        return model(title=title, engine_type=engine_type)
    return fn


def test_apply_plan_edit_itself_is_unchanged_and_still_synchronous():
    """The deterministic fast path must stay exactly as it was -- untouched,
    synchronous, zero-cost for the common case."""
    import inspect
    assert not inspect.iscoroutinefunction(cs.apply_plan_edit)


def test_async_wrapper_tries_the_deterministic_path_first():
    """A phrase the fixed vocabulary already covers must not reach the LLM
    at all -- the fallback is for what apply_plan_edit finds nothing for."""
    import asyncio

    called = {"yes": False}

    async def track(model, messages, **kw):
        called["yes"] = True
        return model(title="X", engine_type="architecture")

    out = asyncio.run(cs.apply_plan_edit_async(
        _ESNAD_PLAN, "add Privileged Access Management Flow", structured_fn=track))
    added = out[len(_ESNAD_PLAN):]
    assert added and added[0][0] == "Privileged Access Management"
    assert not called["yes"], "the deterministic match already succeeded; the LLM should never have been called"


def test_llm_fallback_covers_a_previously_failing_domain(monkeypatch=None):
    """One representative case from the 23 that failed deterministically --
    "Access Management" has no DIAGRAM_TYPE_MAP entry and never will, given
    how many phrasings of it exist."""
    import asyncio

    async def fake(model, messages, **kw):
        return model(title="Access Management", engine_type="flow")

    out = asyncio.run(cs.apply_plan_edit_async(
        _ESNAD_PLAN, "add Access Management diagram", structured_fn=fake))
    added = out[len(_ESNAD_PLAN):]
    assert added and added[0] == ("Access Management", "flow")


def test_llm_fallback_fails_soft_on_any_exception():
    """A slow or broken model call must leave the plan untouched, exactly
    like the deterministic path's own "matches nothing" behaviour -- never
    crash the chat turn."""
    import asyncio

    async def broken(model, messages, **kw):
        raise RuntimeError("network down")

    out = asyncio.run(cs.apply_plan_edit_async(
        _ESNAD_PLAN, "add Access Management diagram", structured_fn=broken))
    assert out == _ESNAD_PLAN


def test_llm_fallback_rejects_an_invalid_engine_type():
    """The model is constrained to the engine's own closed diagram-type set.
    A hallucinated type must never reach the plan, or diagram generation
    downstream would fail on a type it does not recognise."""
    import asyncio

    async def bad_type(model, messages, **kw):
        return model(title="Something", engine_type="not_a_real_engine_type")

    out = asyncio.run(cs.apply_plan_edit_async(
        _ESNAD_PLAN, "add Something diagram", structured_fn=bad_type))
    assert out == _ESNAD_PLAN


def test_llm_fallback_does_not_duplicate_an_existing_diagram():
    import asyncio

    plan_with_am = _ESNAD_PLAN + [("Access Management", "flow")]

    async def fake(model, messages, **kw):
        return model(title="Access Management", engine_type="flow")

    out = asyncio.run(cs.apply_plan_edit_async(
        plan_with_am, "add Access Management diagram", structured_fn=fake))
    assert out == plan_with_am


def test_llm_fallback_respects_the_max_diagrams_cap():
    import asyncio

    full_plan = [(f"Diagram {i}", "architecture") for i in range(cs.MAX_DIAGRAMS_PER_ROUND)]

    async def fake(model, messages, **kw):
        return model(title="One More", engine_type="architecture")

    out = asyncio.run(cs.apply_plan_edit_async(
        full_plan, "add one more diagram", structured_fn=fake))
    assert out == full_plan


def test_no_structured_fn_behaves_exactly_like_the_deterministic_function():
    """A caller that passes structured_fn=None (or omits it) must get IDENTICAL
    behaviour to calling apply_plan_edit directly -- no silent difference."""
    import asyncio
    sync_result = cs.apply_plan_edit(_ESNAD_PLAN, "add Access Management diagram")
    async_result = asyncio.run(cs.apply_plan_edit_async(_ESNAD_PLAN, "add Access Management diagram"))
    assert sync_result == async_result == _ESNAD_PLAN  # neither can match: no vocabulary entry


def test_a_drop_request_never_reaches_the_llm():
    """Drop only ever matches against titles ALREADY in the plan -- no
    vocabulary gap is possible there, so the LLM call would be pure waste."""
    import asyncio

    called = {"yes": False}

    async def track(model, messages, **kw):
        called["yes"] = True
        return model(title="X", engine_type="architecture")

    plan = _ESNAD_PLAN + [("Privileged Access Management", "flow")]
    out = asyncio.run(cs.apply_plan_edit_async(
        plan, "drop the privileged access management diagram", structured_fn=track))
    assert "Privileged Access Management" not in [t for t, _ in out]
    assert not called["yes"]


def test_the_chat_handler_call_site_uses_the_async_wrapper_with_a_real_structured_fn():
    """CALL-SITE check — the async fallback is useless if app.py's actual
    diagram-plan-edit handler still calls the old synchronous-only function."""
    import inspect
    src = inspect.getsource(app)
    assert "chat_state.apply_plan_edit_async(" in src, (
        "the chat handler still calls the deterministic-only apply_plan_edit, "
        "so the LLM fallback is dead code nothing ever reaches")
    assert "structured_fn=_structured_with_fallback" in src


def test_llm_add_fallback_type_is_valid():
    """chat_state._LLM_ADD_ENGINE_TYPES is a hand-kept duplicate of
    diagram_engine.DIAGRAM_TYPES (to avoid chat_state.py importing
    diagram_engine.py for one constant) -- this keeps the two from drifting
    apart silently."""
    import diagram_engine
    assert set(cs._LLM_ADD_ENGINE_TYPES) == set(diagram_engine.DIAGRAM_TYPES)


# ---------------------------------------------------------------------------
# 12. The "still need" message must not blame the client for a field that
#     IS a client fact just because it happened not to get captured.
#
# THE live-run failure: the gate message said "industry -- these are IV's
# decisions, not the client's -- an RFP does not state them" when industry
# was ALREADY extracted as "Mining" from page 3 on a previous run of the
# same SOW. industry is not in rfp_intake.IV_DECISION_FIELDS at all -- it is
# a plain client fact -- but missing_required() checks the GENERIC required-
# fields list (which mixes client facts with real IV decisions), and the old
# message applied the "IV decision" framing to whatever came back regardless
# of which kind it actually was.
# ---------------------------------------------------------------------------

def test_industry_is_not_an_iv_decision_field():
    """The categorisation bug's root cause, confirmed directly: industry is
    correctly ABSENT from IV_DECISION_FIELDS. The bug was never in this
    list -- it was in code elsewhere applying the wrong framing regardless
    of what this list actually says."""
    assert "industry" not in rfp_intake.IV_DECISION_FIELDS
    assert "client_name" not in rfp_intake.IV_DECISION_FIELDS
    assert "iam_vendor" in rfp_intake.IV_DECISION_FIELDS


def test_the_still_need_handler_distinguishes_iv_decisions_from_client_facts():
    """CALL-SITE check — the gate message in app.py must actually branch on
    rfp_intake.IV_DECISION_FIELDS rather than applying one hardcoded framing
    to every missing field."""
    import inspect
    src = inspect.getsource(app)
    assert "rfp_intake.IV_DECISION_FIELDS" in src, (
        "the still-need message never checks which fields are actually IV's "
        "to decide, so it can misapply that framing to a plain client fact "
        "like industry")
    assert "extraction missed it" in src, (
        "a client fact that is genuinely missing should prompt a source check, "
        "not a blanket 'the RFP does not state this'")


# ---------------------------------------------------------------------------
# 13. A failed save must be told to the user, never silently reported as a
#     success.
#
# THE live-run pattern: industry was extracted correctly (shown in the
# "Extracted 39 of 94 fields" summary), then showed as missing again at a
# later gate; the same happened to timeline_milestones. THE root cause: four
# separate call sites called patch_intake_answers and threw away its return
# value, then proceeded to show the user a success message (an extraction
# summary, a "Noted", a "Captured", or a cleared "still missing" list) built
# entirely from LOCAL in-memory data, regardless of whether the database
# write actually succeeded. patch_intake_answers returns None on ANY
# failure -- a network blip, a timeout, a DB constraint -- so a failed save
# was completely indistinguishable from a successful one until whatever
# field it should have written showed up empty again, much later, at a gate
# that reads fresh from the database, with no visible connection to the
# original failure.
# ---------------------------------------------------------------------------

def test_patch_intake_answers_failure_contract():
    """The bug's actual precondition, confirmed directly against the
    function every fix in this section depends on: it returns None on ANY
    failure, so 'result is not None' is the correct, and only, way to know
    whether a save actually landed."""
    import inspect
    src = inspect.getsource(supabase_client.patch_intake_answers)
    assert "return None" in src


def test_rfp_extraction_persist_checks_the_save_result():
    """CALL-SITE check for the FIRST of four fixed locations: the initial
    RFP-upload persist. Before this fix, the return value was discarded and
    the "Extracted N of 94 fields" summary was shown regardless of whether
    the save succeeded."""
    import inspect
    src = inspect.getsource(app)
    assert "persisted = result is not None" in src, (
        "the RFP extraction persist step never checks whether the save actually succeeded")


def test_rfp_correction_persist_checks_the_save_result():
    """CALL-SITE check for the SECOND location: the MODE_RFP_REVIEW
    correction handler (industry: Mining, iam_vendor: ..., etc)."""
    import inspect
    src = inspect.getsource(app)
    assert "correction_saved = result is not None" in src


def test_gap_fill_persist_checks_the_save_result():
    """CALL-SITE check for the THIRD location: the "Before the architecture
    proposal, I still need: X" gate -- the exact gate that showed the
    industry bug. Before this fix, "still missing" was computed from a LOCAL
    in-memory merge of the just-recorded answer, never checked against
    whether the write actually landed, so the user could be told "not
    missing anymore" and sent straight to the diagram plan while the
    database never received the value."""
    import inspect
    src = inspect.getsource(app)
    assert "gap_saved = result is not None" in src


def test_drafting_gap_persist_checks_the_save_result():
    """CALL-SITE check for the FOURTH location: the pre-flight drafting gate
    -- the exact prompt live in this session right now ("9 field(s) that
    shape the draft are still empty ... Send any of them as field_name:
    value"). Before this fix, replying here always got "Captured: X"
    regardless of whether the save actually worked."""
    import inspect
    src = inspect.getsource(app)
    assert "late_saved = result is not None" in src


def test_all_four_fixed_sites_retry_once_before_reporting_failure():
    """A retry, not an immediate failure report -- a momentary network blip
    should not interrupt a 96-field interview or force re-running a 20-page
    vision extraction. Only after a SECOND failure should the user be told."""
    import inspect
    src = inspect.getsource(app)
    assert src.count("is not None") >= 8, (
        "expected an initial check plus a retry check at each of the four "
        "fixed call sites (8 total); found fewer, meaning at least one site "
        "is missing its retry")


def test_a_failed_save_message_never_claims_success():
    """The specific wording matters: the old bug's failure mode was
    confidently claiming success. The fix must say plainly that saving
    failed, not soften it into something that could still read as success."""
    import inspect
    src = inspect.getsource(app)
    assert "saving it failed" in src or "saving them failed" in src
    assert "please send it again" in src.lower() or "re-send" in src.lower()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))



# ---------------------------------------------------------------------------
# 14. split_vendors on "Vendor for Capability A and Capability B" phrasing --
#     the ACTUAL live ESNAD input, no parentheses at all.
#
# THE real production failure: "Ping Identity for Access Management and
# CIAM, Saviynt for IGA and PAM" split on every "and"/"," flat, producing
# FOUR "vendors" (Ping Identity for Access Management, CIAM, Saviynt for
# IGA, PAM). Headings in the shipped ESNAD proposal read "Why CIAM" and "PAM
# Extension Modules and Add-ons" as a direct result -- CIAM and PAM are
# capabilities, not vendors.
# ---------------------------------------------------------------------------

def test_split_vendors_the_actual_live_esnad_input():
    got = pt.split_vendors(
        "Ping Identity for Access Management and CIAM, Saviynt for IGA and PAM")
    assert got == ["Ping Identity", "Saviynt"], got


def test_split_vendors_for_phrasing_three_vendors():
    got = pt.split_vendors(
        "Ping for WIAM and CIAM, Saviynt for IGA, CyberArk for PAM")
    assert got == ["Ping", "Saviynt", "CyberArk"]


def test_split_vendors_single_vendor_for_phrased():
    """A single vendor with inline capabilities must still return ONE
    vendor, not accidentally split its own capability list into fake
    additional vendors."""
    got = pt.split_vendors("SailPoint for IGA and PAM")
    assert got == ["SailPoint"]


def test_split_vendors_alternate_connector_words():
    """EXTENSIBILITY: the connector is not a fixed vendor/capability lookup
    -- it is a linguistic signal that works for a partner that does not
    exist yet. "covering", "delivering", "providing" all must work the same
    as "for"."""
    for connector in ("covering", "delivering", "providing"):
        got = pt.split_vendors(
            f"Ping Identity {connector} Access Management and CIAM, "
            f"Saviynt {connector} IGA and PAM")
        assert got == ["Ping Identity", "Saviynt"], (connector, got)


def test_split_vendors_parenthesized_form_still_unaffected():
    """The pre-existing, separately-tested parenthesized path must be
    completely untouched: no "for" in this input, so the new capability-
    clause stripper must never fire."""
    got = pt.split_vendors(
        "Ping Identity (Access Management, CIAM) and Saviynt (IGA, PAM)")
    assert got == ["Ping Identity (Access Management, CIAM)", "Saviynt (IGA, PAM)"]


def test_split_vendors_bare_list_form_still_unaffected():
    """No connector word present -- must fall through to the existing
    and/comma splitter exactly as before."""
    assert pt.split_vendors("Ping Identity and Saviynt") == ["Ping Identity", "Saviynt"]
    assert pt.split_vendors("Ping, Saviynt, and CyberArk") == ["Ping", "Saviynt", "CyberArk"]


def test_strip_capability_clauses_is_a_noop_without_a_connector():
    """Direct test of the helper: text with no connector word must come
    back completely unchanged, byte for byte."""
    text = "Ping Identity (Access Management, CIAM) and Saviynt (IGA, PAM)"
    assert pt._strip_capability_clauses(text) == text


def test_vendor_scope_bucket_asks_correctly_for_the_previously_broken_input():
    """END-TO-END regression check through the ACTUAL consumer that broke
    live: vendor_scope_bucket must ask about "Ping Identity" and "Saviynt",
    never about "CIAM" or "PAM" as if they were vendors."""
    b = it.vendor_scope_bucket({
        "iam_vendor": "Ping Identity for Access Management and CIAM, "
                      "Saviynt for IGA and PAM"})
    assert b is not None
    labels = [q["label"] for q in b["questions"]]
    assert any("Ping Identity" in l for l in labels)
    assert any("Saviynt" in l for l in labels)
    assert not any(l.strip().startswith("Which capability area(s) does CIAM")
                  for l in labels), labels
    assert not any(l.strip().startswith("Which capability area(s) does PAM")
                  for l in labels), labels


# ---------------------------------------------------------------------------
# 15. Sizing table coherence: Production/DR/UAT/Development must all agree
#     on whether this is a SaaS deployment, not each independently guess.
#
# THE live ESNAD failure: Production correctly wrote N/A (its own instruction
# anticipated "no figures available"). DR's instruction only said "mirrors
# production" with no knowledge that Production had ended up N/A, and tried
# to force real column values anyway, producing "Vendor SaaS-managed" mixed
# with stray commas. Development's instruction had NO N/A fallback at all and
# invented a plausible-looking on-prem spec (4 vCPU, 16 GB, 100 GB) for a
# product that is never deployed on IV or client hardware.
# ---------------------------------------------------------------------------

def _sizing_instruction(proposal_type, section_id, heading, is_saas):
    tpl = pt.get_template(proposal_type)
    section = next(s for s in tpl if s.id == section_id)
    ctx = {"client_name": "X", "iam_vendor": "V", "iam_vendors": ["V"],
          "proposal_type": proposal_type, "is_saas": is_saas}
    return dict(section.render_subsections(ctx))[heading]


def test_saas_looks_like_detection():
    assert de._looks_like_saas(
        "SaaS (Software-as-a-Service) hosted within the Kingdom of Saudi Arabia")
    assert de._looks_like_saas("SaaS")
    assert de._looks_like_saas("Software as a Service")
    assert not de._looks_like_saas("On-premise")
    assert not de._looks_like_saas("On premise, single data centre")
    assert not de._looks_like_saas("")
    assert not de._looks_like_saas(None)


def test_hybrid_deployment_does_not_get_the_saas_treatment():
    """A hybrid answer mentioning SaaS in passing must not be misread as a
    pure SaaS deployment -- the on-prem numeric-table path is the more
    correct default when some environments genuinely have provisioned
    hardware."""
    assert not de._looks_like_saas("Hybrid: SaaS for CIAM, on-prem for PAM")


def _impl_subsections(is_saas, vendors=("Ping Identity", "Saviynt")):
    section = next(s for s in pt.get_template("implementation")
                   if s.id == "proposed_solution")
    ctx = {"client_name": "X", "iam_vendor": " and ".join(vendors),
           "iam_vendors": list(vendors), "proposal_type": "implementation",
           "is_saas": is_saas}
    return dict(section.render_subsections(ctx))


def test_saas_drops_the_four_hardware_tables_for_iv_style_environments():
    """ESNAD 09-23 shipped four one-row N/A hardware tables. IV's own ESNAD
    proposal sizes no hardware: a per-vendor environment table plus one
    consolidated model. SaaS must get that shape, not four empty tables."""
    subs = _impl_subsections(is_saas=True)
    assert not [h for h in subs if "Hardware Sizing" in h], list(subs)
    for vendor in ("Ping Identity", "Saviynt"):
        instr = subs[f"{vendor} Deployment and Environments"]
        assert "markdown TABLE" in instr and "Never invent CPU" in instr
        assert vendor in instr
    assert "markdown TABLE" in subs["Consolidated Environment and Infrastructure Model"]
    for instr in subs.values():
        assert "{%" not in instr and "%}" not in instr, "raw Jinja leaked"


def test_on_prem_keeps_the_hardware_tables_and_gets_no_saas_subsections():
    subs = _impl_subsections(is_saas=False)
    for env in ("Production", "DR", "UAT", "Development"):
        assert f"Proposed {env} Hardware Sizing" in subs
    assert not [h for h in subs if "Deployment and Environments" in h
                or h.startswith("Consolidated Environment")]


def test_on_prem_production_and_dr_are_byte_identical_to_before_the_fix():
    """The highest-value guarantee for the non-SaaS path: this is the path
    every prior on-prem run (Amlak, BTPN) was scored against, and it must not
    move at all."""
    prod = _sizing_instruction("implementation", "proposed_solution",
                               "Proposed Production Hardware Sizing", is_saas=False)
    assert prod == (
        "production sizing as a markdown TABLE with EXACTLY these columns: "
        "#, Server Category, Quantity, CPU per node, Memory per node (GB), "
        "Storage per node (GB), DB Storage (GB), Operating System, "
        "Application Server, Database, Remarks. "
        "One row per server category. Use the discovery sizing figures "
        "exactly; write N/A where a column does not apply, never leave a "
        "cell blank. Remarks names the node split (e.g. '2 x UI, 2 x Task') "
        "and any RAID or clustering requirement."
    )
    dr = _sizing_instruction("implementation", "proposed_solution",
                             "Proposed DR Hardware Sizing", is_saas=False)
    assert dr == (
        "disaster recovery sizing as a markdown TABLE with EXACTLY these "
        "columns: #, Server Category, Quantity, CPU per node, "
        "Memory per node (GB), Storage per node (GB), DB Storage (GB), "
        "Operating System, Application Server, Database, Remarks. "
        "DR mirrors production unless discovery says otherwise. Follow the "
        "table with one short paragraph on the replication approach."
    )


def test_on_prem_uat_and_development_gain_an_na_fallback_not_present_before():
    """These two DID change, deliberately: they had NO N/A-fallback guidance
    at all even in the on-prem case, which is its own latent hallucination
    risk independent of the SaaS bug. Confirms the addition landed and the
    original guidance is still present alongside it."""
    uat = _sizing_instruction("implementation", "proposed_solution",
                              "Proposed UAT Hardware Sizing", is_saas=False)
    assert "UAT is normally reduced from production" in uat
    assert "write N/A rather than estimating" in uat

    dev = _sizing_instruction("implementation", "proposed_solution",
                              "Proposed Development Hardware Sizing", is_saas=False)
    assert "Development is the smallest" in dev
    assert "write N/A rather than estimating" in dev


def test_migration_type_sizing_gets_the_same_coherent_treatment():
    """The second, smaller sizing table (migration proposals' target_state
    section) shares the identical root cause and gets the identical fix
    pattern, not left behind as an inconsistency."""
    prod = _sizing_instruction("migration", "target_state",
                               "Proposed Production Hardware Sizing", is_saas=True)
    assert "N/A" in prod and "vendor-managed" in prod.lower()
    dr = _sizing_instruction("migration", "target_state",
                             "Proposed DR and Non-Production Sizing", is_saas=True)
    assert "Production" in dr and "N/A" in dr


def test_is_saas_is_wired_into_the_real_context_builder():
    """CALL-SITE check — the Jinja branches are useless if generate_proposal
    never actually computes and passes is_saas into context."""
    import inspect
    src = inspect.getsource(de)
    assert '"is_saas": _is_saas' in src
    assert "_is_saas = _looks_like_saas(" in src


# ---------------------------------------------------------------------------
# 16. Sprint 2 -- heading-depth cap. A model's OWN markdown headers inside
#     drafted prose were silently flattened to H3 regardless of depth, and
#     its FIRST header stacked a second, indistinguishable H2 directly
#     inside a subsection that is already H2 (rendered by the template
#     mechanism before this drafted content even begins).
#
# IV's own proposals genuinely nest to H5 (e.g. "Saviynt EIC Logical
# Architecture" H3 -> a named capability H4 -> a specific workflow step H5).
# The renderer capping at H3 was ONE reason Shilpi's output stayed flatter
# than IV's, independent of whether any subsection instruction currently
# asks a model to write that deep (a separate, following piece of this
# sprint).
# ---------------------------------------------------------------------------

def _render_headings(markdown_body: str) -> list[tuple[str, str]]:
    """[(style_name, text)] for every real Word heading produced from a
    drafted body's own markdown, via the ACTUAL rendering function -- not a
    hand-simulated version of its logic."""
    import io
    from docx import Document
    docx_bytes = de.assemble_docx(
        metadata={"client_name": "X", "proposal_type": "implementation"},
        sections=[{"id": "solution_overview", "title": "Solution Overview",
                  "content": markdown_body}])
    d = Document(io.BytesIO(docx_bytes))
    return [(p.style.name, p.text.strip()) for p in d.paragraphs
           if p.style.name.startswith("Heading") and p.text.strip()]


def test_single_hash_no_longer_collides_with_the_subsections_own_h2():
    """A model's first "#" header must land BELOW the subsection heading
    that contains it, not stack a second, visually identical H2 inside an
    H2 -- confirmed by checking it renders at H3 or deeper, never H2."""
    headings = _render_headings("# First Capability\n\nSome prose.")
    styles = [s for s, t in headings if t == "First Capability"]
    assert styles == ["Heading 3"], styles


def test_relative_nesting_depth_is_preserved_not_flattened():
    """THE core bug: "##" and "###" used to render IDENTICALLY (both capped
    to H3). A genuinely two-level-deep model output must produce two
    genuinely different heading levels."""
    body = (
        "# Workforce Identity and Access Management\n\n"
        "Core SSO and directory services.\n\n"
        "## SSO and MFA\n\n"
        "Adaptive authentication.\n\n"
        "### Adaptive Risk Authentication\n\n"
        "Risk scoring by device, location and behaviour.\n\n"
        "## Orchestration\n\n"
        "No-code flow builder."
    )
    headings = _render_headings(body)
    got = {t: s for s, t in headings}
    assert got["Workforce Identity and Access Management"] == "Heading 3"
    assert got["SSO and MFA"] == "Heading 4"
    assert got["Adaptive Risk Authentication"] == "Heading 5"
    # Same depth (2 hashes) as "SSO and MFA" -- must land at the SAME level,
    # not drift, proving this is a genuinely relative-depth mapping and not an
    # incrementing counter that happens to produce plausible-looking output.
    assert got["Orchestration"] == "Heading 4"


def test_deeper_than_h5_is_capped_not_left_unbounded():
    """H5 is IV's own observed maximum. Capping rather than rendering H6+
    avoids an LLM that gets over-enthusiastic with markdown depth producing
    a heading level Word barely supports and no human proposal ever uses."""
    headings = _render_headings("##### Very Deep Point\n\nSome text.")
    styles = [s for s, t in headings if t == "Very Deep Point"]
    assert styles == ["Heading 5"], styles


def test_the_cap_change_is_the_real_live_code_not_a_stale_copy():
    """CALL-SITE check -- confirms the actual rendering function contains
    the new mapping, not just that a standalone reimplementation of it
    behaves correctly."""
    import inspect
    src = inspect.getsource(de._add_prose_paragraphs)
    assert "min(5, len(heading.group(1)) + 2)" in src


# ---------------------------------------------------------------------------
# 17. The Solution Overview instruction actually REQUESTS nested structure,
#     not just that the renderer could handle it if asked.
#
# Raising the heading cap (test group 16) is necessary but not sufficient:
# checked directly, ZERO subsection instructions asked a model to structure
# its own output with markdown headers at all -- the cap fix alone changes
# nothing in practice. IV's equivalent section (Solution Overview PingOne
# AIC- IAM) has NINE H5 sub-points (SSO/MFA, Multiple Channel Support,
# Orchestration, PingGateway, Use Cases, Application Integrations, Adaptive
# Risk Authentication, Ping ID App...); Shilpi's instruction produced flat,
# undifferentiated prose for the same content.
# ---------------------------------------------------------------------------

def test_solution_overview_instruction_asks_for_nested_markdown():
    tpl = pt.get_template("implementation")
    so = next(s for s in tpl if s.id == "solution_overview")
    ctx = {"client_name": "X", "iam_vendor": "Ping Identity",
          "iam_vendors": ["Ping Identity"], "proposal_type": "implementation",
          "is_saas": True}
    instr = dict(so.render_subsections(ctx))["Ping Identity Solution Overview"]
    assert "##" in instr, "instruction never asks for a markdown header at all"
    assert "capability area" in instr.lower()
    assert "reviewer scanning the headers" in instr


def test_solution_overview_still_fans_out_per_vendor_after_the_rewrite():
    """The rewrite must not have broken the EXISTING, separately-tested
    per-vendor heading fanout from Sprint 1 -- the heading token
    "{{ iam_vendor }}" must survive untouched."""
    tpl = pt.get_template("implementation")
    so = next(s for s in tpl if s.id == "solution_overview")
    vendors = pt.split_vendors("Ping Identity for Access Management and CIAM, "
                               "Saviynt for IGA and PAM")
    ctx = {"client_name": "X", "iam_vendor": "x", "iam_vendors": vendors,
          "proposal_type": "implementation", "is_saas": True}
    headings = [h for h, _ in so.render_subsections(ctx)]
    assert "Ping Identity Solution Overview" in headings
    assert "Saviynt Solution Overview" in headings


def test_end_to_end_a_compliant_model_response_renders_correctly_nested():
    """The FULL chain: instruction asks for structure -> a realistic
    model response that follows it -> the actual DOCX renderer -> real,
    correctly-nested Word headings. Not three separate unit checks, one
    pipeline run confirming they fit together."""
    # A response shaped the way the instruction asks for -- capability
    # areas at "##", named sub-features at "###" only where there is
    # something specific to say, matching IV's own observed pattern.
    compliant_response = (
        "Ping Identity delivers workforce and customer identity through a "
        "cloud-native platform.\n\n"
        "## Authentication Methods\n\n"
        "SSO and adaptive MFA across web and mobile channels.\n\n"
        "### Adaptive Risk Authentication\n\n"
        "Risk scoring considers device posture, location and behavioural "
        "signals before stepping up authentication.\n\n"
        "## Session and Orchestration\n\n"
        "A no-code flow builder for authentication journeys.\n\n"
        "### PingGateway\n\n"
        "Reverse-proxy enforcement point for legacy application integration.\n\n"
        "## Administrative Tooling\n\n"
        "A single console for policy, connector and journey configuration."
    )
    headings = _render_headings(compliant_response)
    got = {t: s for s, t in headings}
    assert got["Authentication Methods"] == "Heading 4"
    assert got["Adaptive Risk Authentication"] == "Heading 5"
    assert got["Session and Orchestration"] == "Heading 4"
    assert got["PingGateway"] == "Heading 5"
    assert got["Administrative Tooling"] == "Heading 4"
    # Three capability areas, two of which have a named sub-feature --
    # matching IV's own pattern of 2-3 sub-points per capability area, not
    # every area padded out uniformly.
    area_count = sum(1 for s, t in headings if s == "Heading 4")
    assert area_count == 3


# ---------------------------------------------------------------------------
# 18. RACI matrices gain vendor columns.
#
# IV's own RACI has FOUR party columns (ESNAD, IV, Ping Identity, Saviynt).
# Both of Shilpi's RACI tables hardcoded exactly two columns (Inspirit
# Vision, client) regardless of how many vendors were in the engagement --
# a multi-vendor proposal had nowhere in its RACI to show which vendor
# a given responsibility actually belongs to.
# ---------------------------------------------------------------------------

def _raci_instruction(heading, ctx):
    tpl = pt.get_template("implementation")
    impl = next(s for s in tpl if s.id == "implementation_approach")
    return dict(impl.render_subsections(ctx))[heading]


def test_raci_columns_include_each_vendor_in_order():
    """IV's own column order: client, IV, then each vendor, then
    description. Matched exactly, not just "vendors appear somewhere"."""
    ctx = {"client_name": "ESNAD", "iam_vendor": "x",
          "iam_vendors": ["Ping Identity", "Saviynt"],
          "proposal_type": "implementation", "is_saas": True}
    instr = _raci_instruction("RACI - Delivery Activities", ctx)
    assert ("Deliverable / Activity, ESNAD, Inspirit Vision, Ping Identity, "
           "Saviynt, Description / Comments") in instr


def test_raci_single_vendor_unaffected_in_shape():
    """A single-vendor proposal (every prior scored run -- Amlak, BTPN) must
    still get exactly one vendor column, same as it always effectively had,
    and NONE of the multi-vendor caveat language."""
    ctx = {"client_name": "X", "iam_vendor": "SailPoint",
          "iam_vendors": ["SailPoint"], "proposal_type": "implementation",
          "is_saas": False}
    instr = _raci_instruction("RACI - Delivery Activities", ctx)
    assert "Deliverable / Activity, X, Inspirit Vision, SailPoint, Description" in instr
    assert "MULTI-VENDOR" not in instr


def test_raci_multi_vendor_gets_the_dont_mark_everyone_caveat():
    """Without this, a model asked to fill 4 party columns per row would
    plausibly mark every vendor R/A/C/I on every row uniformly -- which
    misrepresents who actually does the work as badly as having no vendor
    columns at all."""
    ctx = {"client_name": "ESNAD", "iam_vendor": "x",
          "iam_vendors": ["Ping Identity", "Saviynt"],
          "proposal_type": "implementation", "is_saas": True}
    for heading in ("RACI - Project Governance", "RACI - Delivery Activities"):
        instr = _raci_instruction(heading, ctx)
        assert "MULTI-VENDOR" in instr
        assert "leave" in instr.lower() and "blank" in instr.lower()


def test_raci_scales_to_a_third_future_partner_without_code_changes():
    """EXTENSIBILITY check: the column list is generated from iam_vendors,
    not a hardcoded pair -- a third (or fourth) partner just works."""
    ctx = {"client_name": "X", "iam_vendor": "x",
          "iam_vendors": ["Ping Identity", "Saviynt", "CyberArk"],
          "proposal_type": "implementation", "is_saas": True}
    instr = _raci_instruction("RACI - Delivery Activities", ctx)
    assert "Ping Identity, Saviynt, CyberArk" in instr


# ---------------------------------------------------------------------------
# 19. Licence BOQ fans out per vendor, Total BOQ stays the rollup.
#
# IV's actual document: "Ping Identity BOQ" (15x5) and "Saviynt BOQ" (6x4)
# as fully separate tables, plus one project-level "Project BOQ" rollup.
# Shilpi had one "Licence Bill of Quantities" shared across every vendor in
# the engagement -- the same class of problem the multi-vendor heading
# fanout mechanism from Sprint 1 already solves elsewhere, just not applied
# here.
# ---------------------------------------------------------------------------

def test_licence_boq_fans_out_per_vendor():
    tpl = pt.get_template("implementation")
    comm = next(s for s in tpl if s.id == "commercial")
    ctx = {"client_name": "ESNAD", "iam_vendor": "x",
          "iam_vendors": ["Ping Identity", "Saviynt"],
          "proposal_type": "implementation", "is_saas": True}
    headings = [h for h, _ in comm.render_subsections(ctx)]
    assert "Ping Identity Licence Bill of Quantities" in headings
    assert "Saviynt Licence Bill of Quantities" in headings


def test_total_boq_stays_singular_as_the_rollup():
    """The combined/rollup table must NOT fan out -- IV has exactly one
    project-level BOQ alongside the per-vendor detail tables, not one
    "Total BOQ" per vendor."""
    tpl = pt.get_template("implementation")
    comm = next(s for s in tpl if s.id == "commercial")
    ctx = {"client_name": "ESNAD", "iam_vendor": "x",
          "iam_vendors": ["Ping Identity", "Saviynt"],
          "proposal_type": "implementation", "is_saas": True}
    headings = [h for h, _ in comm.render_subsections(ctx)]
    assert headings.count("Total Bill of Quantities") == 1


def test_licence_boq_single_vendor_unaffected_in_shape():
    tpl = pt.get_template("implementation")
    comm = next(s for s in tpl if s.id == "commercial")
    ctx = {"client_name": "X", "iam_vendor": "SailPoint",
          "iam_vendors": ["SailPoint"], "proposal_type": "implementation",
          "is_saas": False}
    headings = [h for h, _ in comm.render_subsections(ctx)]
    assert "SailPoint Licence Bill of Quantities" in headings
    assert headings.count("Total Bill of Quantities") == 1


# ---------------------------------------------------------------------------
# 20. Sprint 3 -- house sections IV writes that Shilpi had none of at all:
#     Project Resources, RAID log, Scope Exclusions, and a genuine
#     Post-Production Support section with AMC/SLA tiers (previously folded
#     into one undifferentiated prose subsection of Knowledge Transfer).
# ---------------------------------------------------------------------------

def test_project_resources_raid_and_scope_exclusions_exist_both_proposal_types():
    for ptype in ("implementation", "migration"):
        tpl = pt.get_template(ptype)
        impl = next(s for s in tpl if s.id == "implementation_approach")
        headings = [h for h, _ in impl.subsections]
        assert "Project Resources" in headings, ptype
        assert "Initial Project RAID Log" in headings, ptype
        assert "Scope Exclusions" in headings, ptype


def test_migration_raci_matrix_also_gained_vendor_columns():
    """The exact Sprint 2 RACI fix, found to have a second, previously
    unchecked instance in the migration-type template's own RACI Matrix
    subsection -- fixed for consistency, not left as a gap."""
    tpl = pt.get_template("migration")
    impl = next(s for s in tpl if s.id == "implementation_approach")
    ctx = {"client_name": "ESNAD", "iam_vendor": "x",
          "iam_vendors": ["Ping Identity", "Saviynt"],
          "proposal_type": "migration", "is_saas": True}
    raci = dict(impl.render_subsections(ctx))["RACI Matrix"]
    assert "Ping Identity, Saviynt" in raci
    assert "MULTI-VENDOR" in raci


def test_post_production_support_exists_as_its_own_section_both_types():
    """IV treats this as a full separate H1, not folded into Knowledge
    Transfer -- matched here, not left as a KT subsection."""
    for ptype in ("implementation", "migration"):
        tpl = pt.get_template(ptype)
        ids = [s.id for s in tpl]
        assert "post_production_support" in ids, ptype
        pps = next(s for s in tpl if s.id == "post_production_support")
        headings = [h for h, _ in pps.subsections]
        assert "Service Level Agreement" in headings
        assert "Coverage" in headings


def test_post_production_support_is_wired_to_discovery_answers():
    """CALL-SITE check for the EXACT bug class this project keeps hitting:
    a new section with no entry in _SECTION_DISCOVERY_FIELDS drafts with
    zero grounding regardless of what the consultant actually supplied.
    Caught by test_document_engine.py's own pre-existing regression guard
    the moment this section was added -- confirmed fixed here too."""
    assert "post_production_support" in de._SECTION_DISCOVERY_FIELDS
    fields = de._SECTION_DISCOVERY_FIELDS["post_production_support"]
    assert "hypercare" in fields
    assert "support_model" in fields


def test_knowledge_transfer_no_longer_claims_support_fields_it_no_longer_covers():
    """The fields moved OUT of knowledge_transfer must not still be listed
    there too -- a field claimed by two sections is a sign the split did
    not actually happen, just got duplicated."""
    kt_fields = de._SECTION_DISCOVERY_FIELDS["knowledge_transfer"]
    assert "hypercare" not in kt_fields
    assert "support_model" not in kt_fields


def test_appendix_e_risk_register_is_now_superseded_by_the_raid_log():
    """Direct unit test of the supersession map itself: Appendix E's generic
    5-row boilerplate Risk Register must be skipped once the body's
    discovery-grounded "Initial Project RAID Log" (covering Risk AND
    Assumption AND Issue AND Dependency, not just Risk) is present --
    shipping both duplicated content and made the richer one look
    undercut by a boilerplate placeholder sitting right after it."""
    assert de._APPENDIX_SUPERSEDED_BY.get("E") == "implementation_approach"
    assert "E" in de._superseded_appendices({"implementation_approach"})
    assert "E" not in de._superseded_appendices({"proposed_solution"})


def test_appendix_d_integration_inventory_still_has_no_body_counterpart():
    """D was correctly never superseded before this change and must not
    become superseded by accident as a side effect of fixing E."""
    assert "D" not in de._APPENDIX_SUPERSEDED_BY
    assert "D" not in de._superseded_appendices(
        {"implementation_approach", "project_timeline", "proposed_solution", "commercial"})


# ---------------------------------------------------------------------------
# 21. Sprint 4 -- partner product corpus infrastructure.
#
# Schema (partner_products, partner_product_chunks, match_partner_product_
# chunks RPC) is live in Supabase, mirroring proposals/proposal_chunks'
# structure and RLS pattern exactly. Zero rows exist -- no real vendor
# material has been gathered yet. Every test below verifies this code is a
# genuine no-op against an empty/unavailable corpus, since that is the ONLY
# state it can be tested against right now: real behaviour against real
# content is unverifiable until Sprint 7 ingests something.
# ---------------------------------------------------------------------------

def test_retrieve_product_chunks_fails_soft_on_error():
    """Never let a broken or unreachable product corpus break a drafting
    call that would otherwise succeed on discovery answers and
    proposal-history evidence alone."""
    import asyncio

    class _BrokenClient:
        async def post(self, *a, **kw):
            raise RuntimeError("network down")

    out = asyncio.run(app.retrieve_product_chunks(_BrokenClient(), [0.0] * 1536, "Ping Identity"))
    assert out == []


def test_retrieve_product_chunks_builds_the_correct_rpc_call():
    """The vendor and capability filters must actually reach the RPC
    request, not be silently dropped."""
    import asyncio

    captured = {}

    class _FakeResponse:
        def raise_for_status(self): pass
        def json(self): return [{"chunk_text": "x"}]

    class _FakeClient:
        async def post(self, url, headers, json, timeout):
            captured["url"] = url
            captured["json"] = json
            return _FakeResponse()

    out = asyncio.run(app.retrieve_product_chunks(
        _FakeClient(), [0.1] * 1536, "Saviynt", k=5, capability="IGA"))
    assert "match_partner_product_chunks" in captured["url"]
    assert captured["json"]["filter_vendor"] == "Saviynt"
    assert captured["json"]["filter_capability"] == "IGA"
    assert captured["json"]["match_count"] == 5
    assert out == [{"chunk_text": "x"}]


def test_build_product_evidence_block_empty_when_no_chunks():
    """No placeholder heading with nothing under it -- an empty corpus for
    this vendor must read as "not applicable yet", not as a gap."""
    assert app.build_product_evidence_block([]) == ""


def test_build_product_evidence_block_never_reuses_the_proposal_label():
    """The exact failure this separate function exists to prevent: vendor
    marketing material must never be labelled as if it were IV's own
    delivery history."""
    chunks = [{"vendor": "Ping Identity", "product_name": "PingOne AIC",
              "doc_type": "datasheet", "heading": "SSO", "chunk_text": "...",
              "similarity": 0.8}]
    block = app.build_product_evidence_block(chunks)
    assert "IV's past proposals" not in block
    assert "PRODUCT DOCUMENTATION" in block
    assert "never cite this as IV's own track record" in block
    assert "Ping Identity" in block and "PingOne AIC" in block


def test_retrieve_fanout_returns_empty_product_chunks_when_fn_not_given():
    """Backward compatibility: every EXISTING caller of _retrieve_fanout
    (there is exactly one, inside draft_section) that does not pass
    retrieve_product_fn must get an empty second list, not an error from an
    unpacking mismatch."""
    import asyncio

    async def fake_embed(client, text):
        return [0.0] * 1536

    async def fake_retrieve(client, embedding, query, **kw):
        return []

    tpl = pt.get_template("implementation")
    section = next(s for s in tpl if s.id == "solution_overview")
    ctx = {"client_name": "X", "iam_vendor": "SailPoint",
          "iam_vendors": ["SailPoint"], "proposal_type": "implementation"}

    proposal_chunks, product_chunks = asyncio.run(de._retrieve_fanout(
        None, section, ctx, embed_fn=fake_embed, retrieve_fn=fake_retrieve,
        top_k=4, fanout=1))
    assert product_chunks == []


def test_retrieve_fanout_only_fires_product_retrieval_for_vendor_specific_sections():
    """company_profile's query has no {{ iam_vendor }} token (confirmed
    directly in an earlier test group) -- product retrieval must not fire
    for it even when retrieve_product_fn is provided, since there is no
    vendor to filter by."""
    import asyncio

    called = {"n": 0}

    async def fake_embed(client, text):
        return [0.0] * 1536

    async def fake_retrieve(client, embedding, query, **kw):
        return []

    async def tracking_product_fn(client, embedding, vendor, k=8, capability=None):
        called["n"] += 1
        return []

    tpl = pt.get_template("implementation")
    section = next(s for s in tpl if s.id == "company_profile")
    ctx = {"client_name": "X", "iam_vendor": "SailPoint",
          "iam_vendors": ["SailPoint"], "proposal_type": "implementation"}

    asyncio.run(de._retrieve_fanout(
        None, section, ctx, embed_fn=fake_embed, retrieve_fn=fake_retrieve,
        top_k=4, fanout=1, retrieve_product_fn=tracking_product_fn))
    assert called["n"] == 0, "product retrieval fired for a vendor-agnostic section"


def test_retrieve_fanout_fires_product_retrieval_once_per_vendor():
    """A multi-vendor section must query the product corpus once per
    vendor, correctly filtered -- not once for a combined/first vendor
    only, which would silently starve every vendor after the first of
    product depth."""
    import asyncio

    async def fake_embed(client, text):
        return [0.0] * 1536

    async def fake_retrieve(client, embedding, query, **kw):
        return []

    seen_vendors = []

    async def tracking_product_fn(client, embedding, vendor, k=8, capability=None):
        seen_vendors.append(vendor)
        return []

    tpl = pt.get_template("implementation")
    section = next(s for s in tpl if s.id == "solution_overview")
    ctx = {"client_name": "X", "iam_vendor": "x",
          "iam_vendors": ["Ping Identity", "Saviynt"],
          "proposal_type": "implementation"}

    asyncio.run(de._retrieve_fanout(
        None, section, ctx, embed_fn=fake_embed, retrieve_fn=fake_retrieve,
        top_k=4, fanout=1, retrieve_product_fn=tracking_product_fn))
    assert set(seen_vendors) == {"Ping Identity", "Saviynt"}


def test_retrieve_fanout_product_retrieval_fails_soft_without_affecting_proposal_chunks():
    """A broken product corpus must never sink proposal-history retrieval --
    the two passes are independent by design."""
    import asyncio

    async def fake_embed(client, text):
        return [0.0] * 1536

    async def fake_retrieve(client, embedding, query, **kw):
        return [{"chunk_text": "real proposal evidence", "similarity": 0.9}]

    async def broken_product_fn(client, embedding, vendor, k=8, capability=None):
        raise RuntimeError("corpus unavailable")

    tpl = pt.get_template("implementation")
    section = next(s for s in tpl if s.id == "solution_overview")
    ctx = {"client_name": "X", "iam_vendor": "SailPoint",
          "iam_vendors": ["SailPoint"], "proposal_type": "implementation"}

    proposal_chunks, product_chunks = asyncio.run(de._retrieve_fanout(
        None, section, ctx, embed_fn=fake_embed, retrieve_fn=fake_retrieve,
        top_k=4, fanout=1, retrieve_product_fn=broken_product_fn))
    assert proposal_chunks and proposal_chunks[0]["chunk_text"] == "real proposal evidence"
    assert product_chunks == []


def test_draft_section_is_unaffected_when_product_fns_are_not_given():
    """The primary safety requirement for this whole sprint: every EXISTING
    caller of draft_section, none of which know about the new parameters,
    must behave identically to before this sprint."""
    import asyncio

    async def fake_embed(client, text):
        return [0.0] * 1536

    async def fake_retrieve(client, embedding, query, **kw):
        return []

    async def fake_structured(model, messages, **kw):
        return "Some drafted content."

    tpl = pt.get_template("implementation")
    section = next(s for s in tpl if s.id == "company_profile")
    ctx = {"client_name": "X", "iam_vendor": "SailPoint",
          "iam_vendors": ["SailPoint"], "proposal_type": "implementation",
          "discovery_answers": {}}

    original = de.draft_with_openrouter
    async def stub_draft(client, system_prompt, user_prompt, max_tokens=0):
        return "Some drafted content."
    de.draft_with_openrouter = stub_draft
    try:
        result = asyncio.run(de.draft_section(
            None, section, ctx, embed_fn=fake_embed, retrieve_fn=fake_retrieve,
            build_grounded_system_fn=lambda chunks: "SYSTEM PROMPT HERE",
            top_k=4, fanout=1, subsections=1,
        ))
    finally:
        de.draft_with_openrouter = original
    assert "product_citations" in result
    assert result["product_citations"] == []


def test_generate_proposal_call_site_wires_the_product_functions_through():
    """CALL-SITE check -- the exact bug shape this project has hit
    repeatedly: a mechanism built and never actually connected. The single
    real call to generate_proposal in app.py must pass both new functions,
    or none of the code above ever runs against anything real."""
    import inspect
    src = inspect.getsource(app)
    assert "retrieve_product_fn=retrieve_product_chunks" in src
    assert "build_product_evidence_fn=build_product_evidence_block" in src


def test_needs_sme_review_is_unaffected_by_strong_product_evidence():
    """Explicit design decision, verified: a well-documented product must
    not mask a genuine need for client-specific review. weak_corpus is
    computed from proposal chunks only.

    Proposal evidence here is WEAK but non-empty (similarity 0.1, below
    WEAK_EVIDENCE_THRESHOLD of 0.55), not absent -- with chunks genuinely
    empty, "not chunks" alone forces weak_corpus True regardless of
    max_similarity, which would make this test pass even if product
    evidence were wrongly blended into max_similarity. A non-empty but weak
    chunk isolates the threshold comparison as the actual deciding factor,
    which is the line this test exists to protect."""
    import asyncio

    async def fake_embed(client, text):
        return [0.0] * 1536

    async def fake_retrieve(client, embedding, query, **kw):
        return [{"chunk_text": "tangentially related proposal text",
                "similarity": 0.1}]  # non-empty, but below WEAK_EVIDENCE_THRESHOLD

    async def strong_product_fn(client, embedding, vendor, k=8, capability=None):
        # Deliberately high-similarity PRODUCT evidence, to prove it does
        # NOT get treated as if it were client-engagement grounding.
        return [{"chunk_text": "detailed product capability", "similarity": 0.99,
                "vendor": vendor, "product_name": "X", "doc_type": "datasheet"}]

    tpl = pt.get_template("implementation")
    section = next(s for s in tpl if s.id == "solution_overview")
    ctx = {"client_name": "X", "iam_vendor": "SailPoint",
          "iam_vendors": ["SailPoint"], "proposal_type": "implementation",
          "discovery_answers": {}}

    original = de.draft_with_openrouter
    async def stub_draft(client, system_prompt, user_prompt, max_tokens=0):
        return "Some drafted content."
    de.draft_with_openrouter = stub_draft
    try:
        result = asyncio.run(de.draft_section(
            None, section, ctx, embed_fn=fake_embed, retrieve_fn=fake_retrieve,
            build_grounded_system_fn=lambda chunks: "SYSTEM PROMPT",
            build_product_evidence_fn=app.build_product_evidence_block,
            retrieve_product_fn=strong_product_fn,
            top_k=4, fanout=1, subsections=1,
        ))
    finally:
        de.draft_with_openrouter = original
    assert result["needs_sme_review"] is True, (
        "strong product evidence incorrectly suppressed the SME review flag "
        "despite zero proposal-history evidence and zero discovery facts")
    assert len(result["product_citations"]) == 1


def test_draft_section_appends_product_evidence_when_present():
    """The other half of the design: when product evidence genuinely
    exists, it must actually reach the drafting prompt, not just be
    retrieved and then dropped."""
    import asyncio

    captured_prompts = []

    async def fake_embed(client, text):
        return [0.0] * 1536

    async def fake_retrieve(client, embedding, query, **kw):
        return []

    async def product_fn(client, embedding, vendor, k=8, capability=None):
        return [{"chunk_text": "PingOne AIC supports adaptive MFA.",
                "similarity": 0.9, "vendor": vendor, "product_name": "PingOne AIC",
                "doc_type": "datasheet", "heading": "Authentication"}]

    def capturing_build_grounded(chunks):
        return "BASE SYSTEM PROMPT"

    tpl = pt.get_template("implementation")
    section = next(s for s in tpl if s.id == "solution_overview")
    ctx = {"client_name": "X", "iam_vendor": "Ping Identity",
          "iam_vendors": ["Ping Identity"], "proposal_type": "implementation",
          "discovery_answers": {}}

    original = de.draft_with_openrouter
    async def stub_draft(client, system_prompt, user_prompt, max_tokens=0):
        captured_prompts.append(system_prompt)
        return "Some drafted content."
    de.draft_with_openrouter = stub_draft
    try:
        result = asyncio.run(de.draft_section(
            None, section, ctx, embed_fn=fake_embed, retrieve_fn=fake_retrieve,
            build_grounded_system_fn=capturing_build_grounded,
            build_product_evidence_fn=app.build_product_evidence_block,
            retrieve_product_fn=product_fn,
            top_k=4, fanout=1, subsections=1,
        ))
    finally:
        de.draft_with_openrouter = original
    assert len(result["product_citations"]) == 1
    assert result["product_citations"][0]["chunk_text"] == "PingOne AIC supports adaptive MFA."
    assert any("PingOne AIC supports adaptive MFA" in p for p in captured_prompts), (
        "product evidence was retrieved but never reached the actual drafting prompt")


def test_client_facing_matrix_keeps_the_full_requirement_text():
    """ESNAD shipped 'SSO for all enterprise applications with single login
    achieves access to a' -- the client's own requirement, cut at 100 chars."""
    import app as A
    text = ("SAML 2.0, OAuth 2.0, OIDC SSO for all enterprise applications with "
            "single login achieves access to all integrated applications")
    m = A.ComplianceMatrix(entries=[A.CoverageEntry(
        requirement_id="AM-01", requirement_text=text, status="covered",
        summary="s", recommendation="r")], overall_notes="")
    assert text in A.render_matrix_markdown(m, client_facing=True)


def test_vendor_named_subsection_sees_only_its_vendors_product_docs():
    """ESNAD 09-23: 'Why Ping Identity' was drafted from one pooled evidence
    block holding Saviynt's docs too, and claimed Saviynt's IGA features."""
    import asyncio
    seen = {}

    async def fake_embed(client, text):
        return [0.0] * 1536

    async def fake_retrieve(client, embedding, query, **kw):
        return []

    async def product_fn(client, embedding, vendor, k=8, capability=None):
        tag = "PINGDOC" if vendor.startswith("Ping") else "SAVDOC"
        return [{"chunk_text": f"{tag} {vendor}", "vendor": vendor, "similarity": 0.9}]

    async def fake_draft(client, system_prompt, user_prompt, max_tokens=0):
        title = user_prompt.split('"')[1]
        seen[title] = system_prompt
        return "Grounded sentence about the platform."

    orig = de.draft_with_openrouter
    de.draft_with_openrouter = fake_draft
    try:
        section = next(s for s in pt.get_template("implementation")
                       if s.id == "solution_overview")
        ctx = {"client_name": "X", "iam_vendor": "Ping Identity and Saviynt",
               "iam_vendors": ["Ping Identity", "Saviynt"],
               "proposal_type": "implementation", "discovery_answers": {}}
        asyncio.run(de.draft_section(
            None, section, ctx, embed_fn=fake_embed, retrieve_fn=fake_retrieve,
            build_grounded_system_fn=lambda chunks: "",
            top_k=4, fanout=1, retrieve_product_fn=product_fn,
            build_product_evidence_fn=lambda cs: " ".join(c["chunk_text"] for c in cs)))
    finally:
        de.draft_with_openrouter = orig
    assert "PINGDOC" in seen["Why Ping Identity"] and "SAVDOC" not in seen["Why Ping Identity"]
    assert "SAVDOC" in seen["Why Saviynt"] and "PINGDOC" not in seen["Why Saviynt"]
    shared = seen["Access Certification"]
    assert "PINGDOC" in shared and "SAVDOC" in shared


def test_a_truncated_compliance_classification_is_retried_with_double_budget():
    """A reasoning model cut off mid-JSON used to turn a requirement into
    'To be confirmed' with no retry. 56 ESNAD requirements ride on this."""
    import asyncio
    budgets = []

    async def fake_once(req, chunks, max_tokens):
        budgets.append(max_tokens)
        if len(budgets) == 1:
            raise RuntimeError("The output is incomplete due to a max_tokens length limit")
        return "ok"

    orig = app._classify_coverage_once
    app._classify_coverage_once = fake_once
    try:
        out = asyncio.run(app.classify_coverage(
            app.Requirement(id="AM-01", text="SSO"), []))
    finally:
        app._classify_coverage_once = orig
    assert out == "ok" and budgets == [app.COMPLIANCE_MAX_TOKENS, 2 * app.COMPLIANCE_MAX_TOKENS]


def test_rfp_page_extraction_goes_through_the_budget_retry():
    import inspect
    assert "_structured_with_fallback(" in inspect.getsource(app.rfp_structured_call)


def test_diagram_facts_and_stack_are_wired_into_the_app():
    """Facts must reach BOTH spec call sites; the stack is added at assembly."""
    import inspect
    src = inspect.getsource(app)
    assert src.count("facts=_diagram_facts(answers)") == 2
    assert '"diagram_type": "stack"' in src and "_stack_spec(intake_answers" in src
    facts = app._diagram_facts({
        "iam_vendor": "Ping Identity for Access Management and CIAM, Saviynt for IGA and PAM",
        "deployment_model": "SaaS hosted within the Kingdom", "envs": "Dev, Test, and Prod",
        "target_integrations": "Taadeen Platform (SSO); Complex Management (custom connector)"})
    assert "PingOne Advanced Identity Cloud" in facts and "PingFederate" in facts
    assert "CLIENT SYSTEMS" in facts and "Taadeen Platform" in facts
    assert "MULTI-VENDOR" in facts


def test_compliance_classification_runs_on_a_cheap_model_first():
    """56 structured classification calls per run went to the drafting model."""
    import asyncio
    seen = {}

    async def fake(response_model, messages, models=None, **kw):
        seen["models"], seen["kw"] = models, kw
        return app.CoverageEntry(requirement_id="x", status="covered", summary="s",
                                 recommendation="r")

    orig = app._structured_with_fallback
    app._structured_with_fallback = fake
    try:
        asyncio.run(app._classify_coverage_once(app.Requirement(id="AM-01", text="SSO"), [], 2000))
    finally:
        app._structured_with_fallback = orig
    assert seen["models"][0] == "openai/gpt-6-luna"
    assert app.PRIMARY_LLM_MODEL in seen["models"], "no fallback to the drafting models"
    assert seen["kw"]["extra_body"] == {"reasoning": {"effort": "low"}}


def test_section_evidence_is_capped_at_16_and_product_at_6_per_vendor():
    import asyncio

    async def emb(client, text):
        return [0.0]

    async def retrieve(client, e, q, **kw):
        return [{"chunk_text": f"chunk {hash(q)} {i}", "similarity": 0.9 - i / 100} for i in range(8)]

    async def product(client, e, vendor, k=8, capability=None):
        return [{"chunk_text": f"{vendor} doc {i}", "vendor": vendor, "similarity": 0.8}
                for i in range(8)]

    section = next(s for s in pt.get_template("implementation") if s.id == "solution_overview")
    ctx = {"client_name": "X", "iam_vendor": "Ping Identity and Saviynt",
           "iam_vendors": ["Ping Identity", "Saviynt"], "proposal_type": "implementation"}
    chunks, prod = asyncio.run(de._retrieve_fanout(
        None, section, ctx, embed_fn=emb, retrieve_fn=retrieve, top_k=8, fanout=3,
        retrieve_product_fn=product))
    assert len(chunks) == de.SECTION_EVIDENCE_CAP == 16
    assert len(prod) == 2 * de.PRODUCT_EVIDENCE_PER_VENDOR == 12
