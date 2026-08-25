"""
Tests for chat_state.py — run with NO API keys / NO network.

Covers the machinery that unblocked the OWUI chat: state markers surviving the
messages round-trip, router classification, and bucket-by-bucket progression.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BRAIN = os.path.dirname(_HERE)
if _BRAIN not in sys.path:
    sys.path.insert(0, _BRAIN)

import chat_state as cs
from intake_template import get_intake_template


# --- marker round-trip ------------------------------------------------------
def test_marker_round_trip_all_modes():
    for state in (
        cs.ChatState(mode=cs.MODE_ROUTER),
        cs.ChatState(mode=cs.MODE_VAULT),
        cs.ChatState(mode=cs.MODE_INTERVIEW, session="abc-123", bucket=0),
        cs.ChatState(mode=cs.MODE_INTERVIEW, session="abc-123", bucket=7),
    ):
        decoded = cs.decode_marker(cs.encode_marker(state))
        assert decoded is not None
        assert decoded.mode == state.mode
        assert decoded.session == state.session
        if state.mode == cs.MODE_INTERVIEW:
            assert decoded.bucket == state.bucket


def test_marker_contains_only_invisible_characters():
    """The whole point: nothing the user can see. OWUI escapes HTML comments, so
    the marker must not rely on the renderer hiding anything."""
    marker = cs.encode_marker(cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=4))
    assert marker, "marker must not be empty"
    invisible = {"\u200b", "\u200c", "\u200d"}
    assert set(marker) <= invisible, f"marker leaked visible characters: {set(marker) - invisible!r}"
    # No angle brackets / HTML that a renderer could echo as text.
    for ch in "<>!-{}[]()#;=":
        assert ch not in marker


def test_marker_is_invisible_when_appended_to_prose():
    body = "**Client** - area 1 of 22"
    full = body + "\n\n" + cs.encode_marker(cs.ChatState(mode=cs.MODE_ROUTER))
    # Stripping the marker leaves the prose byte-identical.
    assert cs.strip_markers(full).strip() == body


def test_legacy_html_comment_markers_still_decode():
    """Threads started before the zero-width fix must keep advancing."""
    legacy = "questions <!--shilpi:v1;mode=interview;session=old-1;bucket=6-->"
    state = cs.decode_marker(legacy)
    assert state is not None
    assert state.mode == cs.MODE_INTERVIEW
    assert state.session == "old-1"
    assert state.bucket == 6
    assert "shilpi" not in cs.strip_markers(legacy)


def test_legacy_markers_are_no_longer_emitted():
    marker = cs.encode_marker(cs.ChatState(mode=cs.MODE_ROUTER))
    assert "<!--" not in marker and "shilpi" not in marker


def test_marker_embedded_in_prose_is_recoverable_and_strippable():
    body = "Here are the questions:\n- One\n"
    marker = cs.encode_marker(cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=2))
    full = body + "\n" + marker
    state = cs.decode_marker(full)
    assert state.mode == cs.MODE_INTERVIEW and state.session == "s1" and state.bucket == 2
    assert "Here are the questions" in cs.strip_markers(full)


def test_uuid_session_round_trips():
    uid = "cd986560-2dea-4c08-bda6-e3efb5e25654"
    state = cs.decode_marker(cs.encode_marker(
        cs.ChatState(mode=cs.MODE_INTERVIEW, session=uid, bucket=21)))
    assert state.session == uid and state.bucket == 21


def test_decode_rejects_junk_and_unknown_modes():
    assert cs.decode_marker("") is None
    assert cs.decode_marker("no marker here") is None
    assert cs.decode_marker("<!--shilpi:v1;mode=bogus-->") is None
    assert cs.decode_marker("<!--something:else-->") is None
    assert cs.decode_marker(cs._zw_encode("v1;mode=bogus")) is None
    assert cs.decode_marker("\u200d\u200d") is None  # empty payload


def test_decode_tolerates_bad_bucket_value():
    state = cs.decode_marker("<!--shilpi:v1;mode=interview;session=s;bucket=notanint-->")
    assert state is not None and state.bucket == 0


# --- state recovery from a messages array -----------------------------------
def test_fresh_thread_has_no_state():
    assert cs.find_chat_state([]) is None
    assert cs.find_chat_state([{"role": "user", "content": "Hi"}]) is None


def test_latest_assistant_marker_wins():
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "menu " + cs.encode_marker(cs.ChatState(mode=cs.MODE_ROUTER))},
        {"role": "user", "content": "1"},
        {"role": "assistant", "content": "q " + cs.encode_marker(
            cs.ChatState(mode=cs.MODE_INTERVIEW, session="s9", bucket=0))},
        {"role": "user", "content": "Acme, banking, India"},
        {"role": "assistant", "content": "q " + cs.encode_marker(
            cs.ChatState(mode=cs.MODE_INTERVIEW, session="s9", bucket=1))},
        {"role": "user", "content": "SailPoint, implementation"},
    ]
    state = cs.find_chat_state(messages)
    assert state.mode == cs.MODE_INTERVIEW
    assert state.session == "s9"
    assert state.bucket == 1  # advanced, not stuck at 0 — the original bug


def test_user_messages_cannot_forge_state():
    # A marker in a USER turn must be ignored — only assistant turns carry state.
    messages = [
        {"role": "user", "content": cs.encode_marker(
            cs.ChatState(mode=cs.MODE_INTERVIEW, session="evil", bucket=99))},
    ]
    assert cs.find_chat_state(messages) is None


def test_multimodal_assistant_content_is_handled():
    messages = [
        {"role": "assistant", "content": [
            {"type": "text", "text": "hi " + cs.encode_marker(cs.ChatState(mode=cs.MODE_VAULT))},
        ]},
    ]
    state = cs.find_chat_state(messages)
    assert state is not None and state.mode == cs.MODE_VAULT


def test_advanced_increments_bucket_only():
    s = cs.ChatState(mode=cs.MODE_INTERVIEW, session="s", bucket=3)
    nxt = s.advanced()
    assert nxt.bucket == 4 and nxt.session == "s" and nxt.mode == cs.MODE_INTERVIEW


# --- router classification --------------------------------------------------
def test_numeric_choices():
    assert cs.classify_router_choice("1") == cs.CHOICE_NEW_PROPOSAL
    assert cs.classify_router_choice("2") == cs.CHOICE_VAULT
    assert cs.classify_router_choice("3") == cs.CHOICE_DISCUSS
    assert cs.classify_router_choice(" 1. ") == cs.CHOICE_NEW_PROPOSAL


def test_phrase_choices():
    assert cs.classify_router_choice("I want to start a new proposal") == cs.CHOICE_NEW_PROPOSAL
    assert cs.classify_router_choice("new RFP came in today") == cs.CHOICE_NEW_PROPOSAL
    assert cs.classify_router_choice("search the vault please") == cs.CHOICE_VAULT
    assert cs.classify_router_choice("what did we propose for the bank?") == cs.CHOICE_VAULT
    assert cs.classify_router_choice("something else") == cs.CHOICE_DISCUSS


def test_lookup_phrasing_beats_new_proposal_phrasing():
    # Mentions "proposal" but is clearly a retrieval question.
    assert cs.classify_router_choice("show me a previous proposal") == cs.CHOICE_VAULT
    assert cs.classify_router_choice("have we done a SailPoint proposal before") == cs.CHOICE_VAULT


def test_ambiguous_returns_none_rather_than_guessing():
    for text in ("", "   ", "hmm", "ok", "sure", "asdfgh", "maybe later"):
        assert cs.classify_router_choice(text) is None


def test_restart_detection():
    assert cs.wants_restart("start over")
    assert cs.wants_restart("actually, let's restart")
    assert cs.wants_restart("back to the menu")
    assert cs.wants_restart("reset")
    assert not cs.wants_restart("Acme Financial Services")


def test_long_answers_never_trigger_restart():
    """REGRESSION: a 15-area interview was silently thrown away because a normal
    IAM pain point contained the word 'reset'. Long answers are answers."""
    real_answers = [
        "pain points: password reset volume overwhelming the helpdesk, no single view of "
        "user access, audit findings on orphaned accounts, slow app onboarding. decision "
        "criteria: technical depth, delivery certainty, local India presence, total cost.",
        "self-service password reset and account unlock are in scope for phase 2",
        "the runbook covers how to restart the PingFederate cluster nodes one at a time",
        "we need to reset all service account credentials as part of the cutover",
        "start again from the design phase if UAT sign-off fails, per the governance model",
        "monitoring: alert if the connector resets its session more than twice an hour",
    ]
    for text in real_answers:
        assert not cs.wants_restart(text), f"false restart on: {text[:60]}..."


def test_short_restart_phrases_still_work():
    for text in ("start over", "restart please", "reset", "scrap that",
                 "ok let's start again", "main menu", "cancel that", "abort"):
        assert cs.wants_restart(text), f"missed restart: {text}"


def test_skip_detection():
    assert cs.is_skip("skip")
    assert cs.is_skip("N/A")
    assert cs.is_skip(" none ")
    assert not cs.is_skip("none of our systems are cloud-based")


# --- bucket rendering -------------------------------------------------------
def test_bucket_messages_differ_per_index():
    tpl = get_intake_template(None)
    total = cs.bucket_count(tpl)
    assert total > 1
    first = cs.build_bucket_message(tpl, 0, first=True)
    second = cs.build_bucket_message(tpl, 1)
    assert first and second
    assert first != second, "each bucket must render distinct content"
    assert f"area 1 of {total}" in first
    assert f"area 2 of {total}" in second


def test_every_bucket_renders_nonempty_with_its_questions():
    tpl = get_intake_template(None)
    for i in range(cs.bucket_count(tpl)):
        msg = cs.build_bucket_message(tpl, i)
        assert msg.strip(), f"bucket {i} rendered empty"
        bucket = cs.get_bucket(tpl, i)
        assert bucket["title"] in msg
        for q in bucket["questions"]:
            assert q["label"] in msg


def test_out_of_range_bucket_returns_empty():
    tpl = get_intake_template(None)
    assert cs.build_bucket_message(tpl, 9999) == ""
    assert cs.get_bucket(tpl, 9999) is None
    assert cs.get_bucket(tpl, -1) is None


def test_required_marker_and_options_surface():
    tpl = get_intake_template(None)
    first = cs.build_bucket_message(tpl, 0, first=True)
    assert "(required)" in first
    # Some bucket somewhere must expose select options.
    all_text = "\n".join(cs.build_bucket_message(tpl, i) for i in range(cs.bucket_count(tpl)))
    assert "options:" in all_text


def test_completion_messages():
    done = cs.build_interview_complete_message(None)
    assert "complete" in done.lower()
    missing = cs.build_interview_complete_message(["client_name", "iam_vendor"])
    assert "client_name" in missing and "iam_vendor" in missing


def test_recap_line():
    assert "Acme" in cs.build_recap_line({"client_name": "Acme"})
    assert cs.build_recap_line({}).strip().startswith("_")


def test_router_message_offers_all_three_paths():
    msg = cs.ROUTER_MESSAGE
    assert "1" in msg and "2" in msg and "3" in msg
    assert "new proposal" in msg.lower()
    assert "past proposals" in msg.lower()




# --- architecture / drafting modes ------------------------------------------
def test_architecture_and_drafting_modes_round_trip():
    for mode in (cs.MODE_ARCHITECTURE, cs.MODE_DRAFTING):
        st = cs.ChatState(mode=mode, session="s-1", proposal="p-1")
        d = cs.decode_marker(cs.encode_marker(st))
        assert d.mode == mode and d.session == "s-1" and d.proposal == "p-1"


def test_proposal_id_survives_and_stays_invisible():
    st = cs.ChatState(mode=cs.MODE_ARCHITECTURE, session="cd986560-2dea-4c08-bda6-e3efb5e25654",
                      proposal="7e09e870-60f8-4df9-8fc2-26f193391b1a")
    marker = cs.encode_marker(st)
    assert set(marker) <= {"\u200b", "\u200c", "\u200d"}
    d = cs.decode_marker("Proposed Architecture\n\n" + marker)
    assert d.proposal == "7e09e870-60f8-4df9-8fc2-26f193391b1a"


def test_approval_intents():
    for text in ("approve", "approved", "looks good", "lgtm", "go ahead", "sign off"):
        assert cs.classify_architecture_intent(text) == cs.INTENT_APPROVE, text


def test_regenerate_and_draft_intents():
    assert cs.classify_architecture_intent("regenerate") == cs.INTENT_REGENERATE
    assert cs.classify_architecture_intent("try again please") == cs.INTENT_REGENERATE
    assert cs.classify_architecture_intent("generate the proposal") == cs.INTENT_DRAFT
    assert cs.classify_architecture_intent("ok now draft the proposal") == cs.INTENT_DRAFT


def test_long_feedback_is_rejection_never_accidental_approval():
    """The gate must not be passed by a paragraph that happens to say 'good'."""
    feedback = ("the overall shape is good but the DMZ should sit in front of the load "
                "balancer, and PingDirectory needs to be in the secure zone rather than "
                "alongside the reverse proxy, also add the SuccessFactors feed")
    assert cs.classify_architecture_intent(feedback) == cs.INTENT_REJECT


def test_ambiguous_review_reply_is_not_approval():
    for text in ("hmm", "ok?", "", "   ", "what do you think"):
        assert cs.classify_architecture_intent(text) != cs.INTENT_APPROVE


# --- OWUI task-prompt guard -------------------------------------------------
def test_owui_task_prompts_are_detected():
    for text in (
        "### Task:\nCreate a concise, 3-5 word title summarizing the chat history.",
        "### Task:\nGenerate 1-3 broad tags categorizing the main themes.",
        "JSON format: { \"title\": \"...\" }",
        "Chat History:\n<chat_history>\nUSER: hi\n</chat_history>",
        "Suggest 3-5 relevant follow-up questions",
    ):
        assert cs.is_owui_task_prompt(text), text


def test_real_consultant_answers_are_not_task_prompts():
    """No false positives — these must reach the interview handler."""
    for text in (
        "AWS, Tech, India",
        "deployment model: hybrid. required diagram types: solution/reference, deployment",
        "pain points: password reset volume overwhelming the helpdesk",
        "the task is to migrate 200 apps to PingFederate",
        "generate the proposal",
        "approve",
    ):
        assert not cs.is_owui_task_prompt(text), text


# --- diagram planning ------------------------------------------------------
def test_plan_diagrams_maps_iv_vocabulary_to_engine_types():
    planned = cs.plan_diagrams({
        "required_diagram_types": "solution/reference, deployment, integration/joiner flow"})
    types = [t for _, t in planned]
    assert "architecture" in types and "flow" in types
    assert len(planned) <= cs.MAX_DIAGRAMS_PER_ROUND


def test_plan_diagrams_accepts_list_and_caps_count():
    # No diagram_count answered -> the default applies, not the hard ceiling.
    planned = cs.plan_diagrams({"required_diagram_types": [
        "solution/reference", "deployment", "security", "auth/customer journey",
        "migration phases"]})
    assert len(planned) == cs.DEFAULT_DIAGRAMS_PER_ROUND


def test_plan_diagrams_always_returns_something_reviewable():
    for answers in ({}, {"required_diagram_types": ""}, {"required_diagram_types": "gibberish"}):
        planned = cs.plan_diagrams(answers)
        assert planned and planned[0][1] in ("architecture",)


def test_engine_types_are_valid():
    """Every mapped type must be one diagram_engine actually renders."""
    from diagram_engine import DIAGRAM_TYPES
    for engine_type in set(cs.DIAGRAM_TYPE_MAP.values()):
        assert engine_type in DIAGRAM_TYPES, engine_type


# --- text representation ---------------------------------------------------
def test_text_representation_renders_nodes_and_edges():
    spec = {
        "nodes": [{"id": "u", "label": "End Users"}, {"id": "gtm", "label": "Global Traffic Manager"},
                  {"id": "kc", "label": "Keycloak", "group": "Secure Zone"}],
        "edges": [{"source": "u", "target": "gtm"},
                  {"source": "gtm", "target": "kc", "label": "OIDC"}],
    }
    out = cs.build_spec_text_representation(spec)
    assert "End Users" in out and "Global Traffic Manager" in out
    assert "→" in out and "OIDC" in out
    assert "Secure Zone" in out


def test_text_representation_handles_empty_spec():
    assert cs.build_spec_text_representation({}) == "_(empty spec)_"


def test_architecture_message_states_the_gate_and_lists_diagrams():
    msg = cs.build_architecture_message([
        {"title": "Solution Architecture", "diagram_type": "architecture",
         "text_representation": "- **A**", "url": "https://example.invalid/x.png"},
    ])
    assert "approval" in msg.lower()
    assert "Solution Architecture" in msg
    assert "https://example.invalid/x.png" in msg
    assert "approve" in msg.lower()


def test_architecture_message_survives_missing_render():
    msg = cs.build_architecture_message([
        {"title": "Deployment", "diagram_type": "architecture",
         "text_representation": "- **B**", "url": None}])
    assert "render unavailable" in msg.lower()
    assert "- **B**" in msg


# --- diagram planning regressions (from live output review) ------------------
def test_requested_diagram_count_is_honoured():
    """REGRESSION: a 4th requested diagram ('security') was silently dropped by a
    hard cap of 3, so TLS/HSM/WAF had nowhere to appear."""
    planned = cs.plan_diagrams({
        "required_diagram_types": "solution/reference, deployment, integration/joiner flow, security",
        "diagram_count": "4"})
    assert len(planned) == 4
    assert any("security" in t.lower() for t, _ in planned)


def test_diagram_count_is_clamped_to_a_ceiling():
    planned = cs.plan_diagrams({
        "required_diagram_types": ", ".join(["solution/reference", "deployment", "security",
                                             "auth/customer journey", "migration phases",
                                             "integration", "user journey", "network"]),
        "diagram_count": "99"})
    assert len(planned) <= cs.MAX_DIAGRAMS_PER_ROUND


def test_missing_diagram_count_uses_the_default():
    planned = cs.plan_diagrams({
        "required_diagram_types": "solution/reference, deployment, security, network"})
    assert len(planned) == cs.DEFAULT_DIAGRAMS_PER_ROUND


def test_deployment_guidance_demands_topology_not_logical_flow():
    """REGRESSION: the generated deployment diagram just repeated the solution
    diagram — no zones, no load balancer, no HA."""
    g = cs.deployment_guidance_for("Deployment", "architecture").lower()
    assert "zone" in g
    assert "region" in g or "data centre" in g
    assert "load balancer" in g or "balancing" in g
    # And it must differ from the plain logical-architecture guidance.
    assert g != cs.DIAGRAM_TYPE_GUIDANCE["architecture"].lower()


def test_security_guidance_covers_the_specified_controls():
    g = cs.deployment_guidance_for("Security", "network").lower()
    for token in ("trust boundar", "waf", "key storage", "audit"):
        assert token in g, token


def test_every_engine_type_has_guidance():
    from diagram_engine import DIAGRAM_TYPES
    for t in DIAGRAM_TYPES:
        assert cs.DIAGRAM_TYPE_GUIDANCE.get(t), t


def test_every_word_our_own_messages_suggest_is_understood():
    """REGRESSION: the timeout message said "Say regenerate to retry it" while the
    classifier rejected "retry" — the user typed our own word and got nothing."""
    for word in ("retry", "retry it", "regenerate", "try again", "again",
                 "another attempt", "one more time"):
        assert cs.classify_architecture_intent(word) == cs.INTENT_REGENERATE, word
    for word in ("approve", "approved", "looks good"):
        assert cs.classify_architecture_intent(word) == cs.INTENT_APPROVE, word
    assert cs.is_skip("skip")


def test_guidance_always_demands_edges():
    """A diagram is a graph: every type's guidance must ask for connections."""
    for title, dtype in (("Solution / Reference", "architecture"),
                         ("Deployment", "architecture"),
                         ("Security", "network"),
                         ("Integration / Joiner Flow", "flow"),
                         ("Auth Journey", "sequence")):
        g = cs.deployment_guidance_for(title, dtype)
        assert "EDGES ARE MANDATORY" in g, title
        assert "label" in g.lower(), title
        assert "Title Case" in g, title


# Shared runner: collects tests at EXIT, so appending a test below this
# line cannot silently skip it. Four files previously lost appended
# tests to an inline loop that read globals() at call time.
import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _runner import run_tests  # noqa: E402

run_tests(globals(), "CHAT STATE TESTS")
