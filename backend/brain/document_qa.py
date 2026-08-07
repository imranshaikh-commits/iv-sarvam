"""Deterministic quality gate for drafted proposal text.

WHY THIS IS CODE, NOT AN LLM REVIEWER
-------------------------------------
Every defect this module handles is a *counting* or *pattern* problem with an
objectively correct answer:

  - "does any 6-word phrase repeat 3+ times in this paragraph?"  -> count it
  - "does this sentence talk about the evidence base?"           -> match it
  - "are there [n] citation markers?"                            -> regex

A model asked to judge these would be slower, cost money, be non-deterministic,
and would still miss cases a counter catches every time. Worse, the same model
family that produced the degenerate text would be grading it.

There IS a place for a model reviewer — judgement calls like "is this
technically accurate for SailPoint" or "does this read like IV's voice" — but
that belongs after an eval harness exists to prove the reviewer helps. Adding it
now would be an unmeasured component in front of measurable bugs.

LESSON ENCODED HERE
-------------------
The previous attempt checked for the same WORD repeating consecutively. The
model's actual failure mode was the same PHRASE repeating
("stated constraints stated constraints...", "encrypted storage secrets
complete audit trail identity events centralized logging" x10). Checking the
specific thing that broke last time is how you get an infinite series of
near-misses. These checks are structural: n-gram frequency regardless of the
words involved, and sentence *subject* rather than a blocklist of phrasings.
"""

from __future__ import annotations

import logging
import re
from collections import Counter

log = logging.getLogger("shilpi.qa")

# --- citations --------------------------------------------------------------
# Inline evidence markers like [1], [12], or runs like [1][3][7]. These exist
# for internal traceability; the client-facing document must not carry them.
_CITATION_RE = re.compile(r"\s*\[\d{1,3}\](?:\s*\[\d{1,3}\])*")


def strip_citations(text: str) -> str:
    """Remove all [n] citation markers, tidying the punctuation they leave behind."""
    if not text:
        return text
    out = _CITATION_RE.sub("", text)
    # "...unified architecture ." -> "...unified architecture."
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out


# --- meta-commentary --------------------------------------------------------
# Sentences whose SUBJECT is the drafting process rather than the proposal.
# Deliberately matched on subject+verb shape, not on a blocklist of phrasings:
# the previous fix banned "the retrieved evidence" and the model simply switched
# to "Evidence does not confirm...", "Evidence cited originates from...".
_META_SENTENCE_RE = re.compile(
    r"(?:^|(?<=[.!?]\s))"                     # sentence start
    r"[^.!?]*?\b(?:the\s+)?evidence\b"        # subject is the evidence base
    r"\s+(?:cited\s+)?"
    r"(?:does\s+not|doesn't|do\s+not|is\s+|are\s+|originates|comes|references|"
    r"provided|available|base|suggests|indicates|confirms|contains|lacks)"
    r"[^.!?]*[.!?]\s*",
    re.I,
)

# "needs SME confirmation" style trailers appended to otherwise fine sentences.
_SME_TRAILER_RE = re.compile(
    r"\s*[—–-]{1,2}\s*(?:this\s+)?(?:would\s+)?needs?\s+SME\s+"
    r"(?:confirmation|input|review|clarification)[^.!?]*[.!?]",
    re.I,
)


def strip_meta_commentary(text: str) -> str:
    """Remove sentences that narrate the drafting/retrieval process.

    KNOWN LIMIT: only whole sentences are removed. A meta clause embedded
    mid-sentence ("...cloud applications but evidence does not specify which...")
    survives, because deleting the sentence would also delete the legitimate
    content around it. Measured on the live Amlak run: 9 of 10 removed, 1
    residual. The prompt-level ban is the primary defence; this is the net.

    Genuine gaps still need flagging — but in a dedicated review appendix, not
    inline in client-facing prose. Callers collect what is removed.
    """
    if not text:
        return text
    out = _META_SENTENCE_RE.sub("", text)
    out = _SME_TRAILER_RE.sub(".", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def extract_meta_commentary(text: str) -> list[str]:
    """Return the meta-commentary sentences found, for the review appendix."""
    if not text:
        return []
    found = [m.group(0).strip() for m in _META_SENTENCE_RE.finditer(text)]
    return [f for f in found if f]


# --- degeneration -----------------------------------------------------------
DEGENERATE_NGRAM = 6        # phrase length to test
DEGENERATE_REPEATS = 3      # this many occurrences of one phrase = degenerate
# Floor chosen empirically, not by intuition: measured against the live Amlak
# document, floors of 40/30/25/20 all caught 4/4 genuine cases with ZERO false
# positives across 113 clean paragraphs. 25 is used so shorter padded blocks are
# also caught, while staying well clear of legitimate short repetitive prose.
DEGENERATE_MIN_WORDS = 25


def repetition_score(text: str, n: int = DEGENERATE_NGRAM) -> tuple[int, str]:
    """Return (max repeat count, the offending phrase) for any n-gram in text."""
    words = re.findall(r"[A-Za-z][A-Za-z\-']*", (text or "").lower())
    if len(words) < n * 2:
        return 0, ""
    grams = [" ".join(words[i:i + n]) for i in range(len(words) - n)]
    if not grams:
        return 0, ""
    phrase, count = Counter(grams).most_common(1)[0]
    return count, phrase


def is_degenerate(text: str) -> tuple[bool, str]:
    """Is this text padded with repeated phrases? Returns (verdict, reason).

    Measured against the live Amlak run: 4 of 117 body paragraphs tripped this,
    and all 4 were genuinely degenerate — no false positives. The two worst were
    992 and 1054 words, i.e. paragraphs that ran to the token cap and padded.
    """
    if not text or len(text.split()) < DEGENERATE_MIN_WORDS:
        return False, ""
    count, phrase = repetition_score(text)
    if count >= DEGENERATE_REPEATS:
        return True, f'phrase "{phrase[:60]}" repeats {count}x'
    return False, ""


def truncate_at_degeneration(text: str) -> str:
    """Cut text at the point repetition begins, keeping the good prose before it.

    Last-resort fallback when a re-draft also comes back degenerate. Cuts at a
    sentence boundary so the section never ends mid-clause.
    """
    degenerate, _ = is_degenerate(text)
    if not degenerate:
        return text
    _, phrase = repetition_score(text)
    if not phrase:
        return text
    first_word = phrase.split()[0]
    # Find the SECOND occurrence of the phrase's opening word after the first —
    # the repetition onset — then back off to the preceding sentence end.
    positions = [m.start() for m in re.finditer(
        r"\b" + re.escape(first_word) + r"\b", text, re.I)]
    if len(positions) < 2:
        return text
    cut_at = positions[1]
    head = text[:cut_at]
    sentence_ends = list(re.finditer(r"[.!?](?:\s|$)", head))
    if sentence_ends:
        return head[: sentence_ends[-1].end()].rstrip()
    return head.rstrip()


# --- combined gate ----------------------------------------------------------
def qa_text(text: str, *, strip_cites: bool = True) -> tuple[str, list[str]]:
    """Clean one drafted block. Returns (cleaned_text, issues_found).

    Order matters: meta-commentary is removed before the degeneracy check, so a
    paragraph is not condemned for repetition that only existed inside the
    boilerplate we were going to delete anyway.
    """
    issues: list[str] = []
    if not text:
        return text, issues

    meta = extract_meta_commentary(text)
    if meta:
        issues.append(f"removed {len(meta)} meta-commentary sentence(s)")
    out = strip_meta_commentary(text)

    if strip_cites:
        n_cites = len(_CITATION_RE.findall(out))
        if n_cites:
            issues.append(f"stripped {n_cites} citation marker(s)")
        out = strip_citations(out)

    degenerate, reason = is_degenerate(out)
    if degenerate:
        issues.append(f"degenerate: {reason}")

    return out, issues


def qa_report(sections: list[dict]) -> dict:
    """Summarise QA findings across a whole document, for logging."""
    total_issues, degenerate_sections = 0, []
    for sec in sections or []:
        body = sec.get("content") or ""
        _, issues = qa_text(body)
        total_issues += len(issues)
        if any(i.startswith("degenerate") for i in issues):
            degenerate_sections.append(sec.get("title") or sec.get("id") or "?")
    return {
        "sections": len(sections or []),
        "issues": total_issues,
        "degenerate_sections": degenerate_sections,
    }
