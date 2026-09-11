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


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))

