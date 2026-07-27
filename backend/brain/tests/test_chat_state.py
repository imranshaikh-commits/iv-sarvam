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
    legacy = "questions <!--sarvam:v1;mode=interview;session=old-1;bucket=6-->"
    state = cs.decode_marker(legacy)
    assert state is not None
    assert state.mode == cs.MODE_INTERVIEW
    assert state.session == "old-1"
    assert state.bucket == 6
    assert "sarvam" not in cs.strip_markers(legacy)


def test_legacy_markers_are_no_longer_emitted():
    marker = cs.encode_marker(cs.ChatState(mode=cs.MODE_ROUTER))
    assert "<!--" not in marker and "sarvam" not in marker


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
    assert cs.decode_marker("<!--sarvam:v1;mode=bogus-->") is None
    assert cs.decode_marker("<!--something:else-->") is None
    assert cs.decode_marker(cs._zw_encode("v1;mode=bogus")) is None
    assert cs.decode_marker("\u200d\u200d") is None  # empty payload


def test_decode_tolerates_bad_bucket_value():
    state = cs.decode_marker("<!--sarvam:v1;mode=interview;session=s;bucket=notanint-->")
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
    assert not cs.wants_restart("Acme Financial Services")


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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"ALL {len(fns)} CHAT STATE TESTS PASSED")
