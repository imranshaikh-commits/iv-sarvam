"""QA gate tests — written against the ACTUAL defects in the 2026-08-07 Amlak run."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import document_qa as qa  # noqa: E402


def test_phrase_level_degeneration_detected():
    """REGRESSION: the previous check looked for the same WORD repeating. The
    model's real failure was the same PHRASE repeating, so it sailed through."""
    bad = ("The platform enforces controls. " + "stated constraints " * 16)
    degenerate, reason = qa.is_degenerate(bad)
    assert degenerate, "phrase-level padding not detected"
    assert "repeats" in reason


def test_real_amlak_degenerate_paragraph():
    """Verbatim shape from the live run: long sentence then a phrase loop."""
    real = ("Amlak International will gain automated user lifecycle management. "
            + "encrypted storage secrets TLS HTTPS web app connections role-based UI "
              "task RBAC ABAC model auditable logs SoD policy checks " * 6)
    degenerate, _ = qa.is_degenerate(real)
    assert degenerate


def test_legitimate_technical_prose_not_flagged():
    """Vendor names and IAM terms repeat legitimately — no false positives."""
    good = (
        "SailPoint IdentityIQ provides access certification for managers, application "
        "owners and entitlement owners. The platform integrates with Active Directory "
        "for identity lookup and with the HRMS as the authoritative source for joiner, "
        "mover and leaver events. Provisioning covers on-premises and cloud "
        "applications, and de-provisioning is automated on termination. SailPoint also "
        "supports segregation-of-duties policy checks with detective and preventive "
        "controls, plus password management including self-service reset."
    )
    degenerate, reason = qa.is_degenerate(good)
    assert not degenerate, f"false positive on clean prose: {reason}"


def test_short_paragraphs_never_flagged():
    assert not qa.is_degenerate("Short text repeated repeated repeated.")[0]


def test_citations_fully_stripped():
    """User directive: citation markers gone completely from client output."""
    src = "unified architecture [6]. The scope covers deployment [1][7]. HRMS feeds it [3]."
    out = qa.strip_citations(src)
    assert "[" not in out and "]" not in out
    assert "architecture." in out, "punctuation not tidied after stripping"
    assert "deployment." in out


def test_meta_commentary_new_variants_removed():
    """REGRESSION: banning 'the retrieved evidence' just made the model rephrase
    to 'Evidence does not confirm...' — so match the SUBJECT, not the wording."""
    for sentence in (
        "Evidence does not confirm specific SailPoint product edition version number.",
        "Evidence cited originates from prior engagements with different clients.",
        "The retrieved evidence originates from InspiritVision proposals.",
        "The evidence base lacks Amlak-specific volumetrics.",
    ):
        assert qa.strip_meta_commentary(sentence).strip() == "", sentence


def test_real_proposal_content_survives_meta_stripping():
    keep = ("SailPoint IdentityIQ delivers certification campaigns for managers, "
            "application owners and entitlement owners.")
    assert qa.strip_meta_commentary(keep).strip() == keep


def test_meta_sentences_are_extractable_for_review():
    src = ("The platform is deployed on-premises. Evidence does not confirm the "
           "HRMS product used as authoritative source.")
    found = qa.extract_meta_commentary(src)
    assert len(found) == 1 and "HRMS product" in found[0]


def test_qa_text_reports_what_it_changed():
    src = "Architecture is centralised [2]. Evidence does not confirm the edition."
    out, issues = qa.qa_text(src)
    assert "[2]" not in out
    assert any("citation" in i for i in issues)
    assert any("meta-commentary" in i for i in issues)


def test_truncation_keeps_good_prose_and_ends_cleanly():
    good = "The platform integrates with Active Directory. Provisioning is automated."
    out = qa.truncate_at_degeneration(good + " " + "stated constraints " * 16)
    assert "stated constraints stated constraints" not in out
    assert out.rstrip().endswith((".", "!", "?"))


def test_qa_report_names_degenerate_sections():
    sections = [
        {"title": "Good Section", "content": "Clean prose about identity governance."},
        {"title": "Bad Section",
         "content": "Intro sentence here. " + "stated constraints " * 16},
    ]
    rep = qa.qa_report(sections)
    assert "Bad Section" in rep["degenerate_sections"]
    assert "Good Section" not in rep["degenerate_sections"]


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
    print(f"ALL {passed} DOCUMENT QA TESTS PASSED")
