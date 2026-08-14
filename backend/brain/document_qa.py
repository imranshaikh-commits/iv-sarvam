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
from typing import Optional

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

# NOTE: the old _SME_TRAILER_RE lived here. It required a dash prefix and was
# superseded by _REVIEW_ASIDE_RE below, which matches bracketed asides too.


# Review asides appended to otherwise fine sentences. Matched by SHAPE — a
# bracketed or dash-led aside whose content is about confirming/verifying rather
# than about the client's solution — not by phrasing.
#
# The previous version required a DASH prefix, so it removed
# "— needs SME confirmation" and left "(needs SME confirmation)" untouched: 45
# survived into the run-3 document. Same lesson as the meta-commentary blocklist,
# one level down. Brackets, dashes and bare trailing clauses are all covered.
_REVIEW_ASIDE_RE = re.compile(
    r"(?:"
    r"\s*[\(\[\{][^)\]\}]{0,400}?"                      # bracketed aside
    r"\b(?:SME|to\s+be\s+confirmed|TBC|TBD|needs?\s+confirmation|"
    r"requires?\s+confirmation|not\s+confirmed|unverified)\b"
    r"[^)\]\}]{0,400}?[\)\]\}]"
    r"|"
    r"\s*[—–,-]{1,2}\s*(?:this\s+)?(?:would\s+|will\s+)?"  # dash/comma-led aside
    r"(?:needs?|requires?|pending|subject\s+to)\s+"
    r"(?:SME|client|customer|human)?\s*"
    r"(?:confirmation|input|review|clarification|validation|verification)"
    r"[^.!?]{0,120}"
    r"|"
    # Bare clause with no bracket or dash at all. These appear inside prose that
    # has already lost its punctuation, so the block is usually caught by the
    # degeneracy check too — but the marker must not survive if it is not.
    r"\s*\b(?:needs?|requires?)\s+SME\s+"
    r"(?:confirmation|input|review|clarification|validation|verification)"
    r"[^.!?]{0,140}"
    r")",
    re.I,
)


# Shilpi's OWN structural marker, inserted by the engine when a section could
# not be drafted at all. It must survive the aside stripper: the draft goes to an
# IV reviewer first, and silently deleting the flag that says "this section
# failed, write it yourself" would hide a hole rather than surface it.
SME_REVIEW_SENTINEL = "[SME REVIEW]"
_SENTINEL_GUARD = "\x00SMEREVIEW\x00"


def strip_review_markers(text: str) -> str:
    """Remove internal review asides from client-facing prose.

    Genuine gaps still need flagging, but in a review appendix rather than
    inline in a document going to a client. Callers collect what is removed.
    """
    if not text:
        return text
    out = text.replace(SME_REVIEW_SENTINEL, _SENTINEL_GUARD)
    out = _REVIEW_ASIDE_RE.sub("", out)
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    out = re.sub(r"\(\s*\)|\[\s*\]", "", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.replace(_SENTINEL_GUARD, SME_REVIEW_SENTINEL)


def count_review_markers(text: str) -> int:
    guarded = (text or "").replace(SME_REVIEW_SENTINEL, _SENTINEL_GUARD)
    return len(_REVIEW_ASIDE_RE.findall(guarded))


# --- em-dashes ---------------------------------------------------------------
# IV's own proposals use 2 em-dashes in 9,900 words (0.2 per 1,000). Run 3 used
# 203 in 15,551 (13.1 per 1,000), roughly 65x the house rate. Product directive:
# never use them.
_EM_DASH_RE = re.compile(r"\s*[—–]\s*")


def strip_em_dashes(text: str) -> str:
    """Replace em/en dashes with house-style punctuation.

    A dash between two clauses becomes a comma; a dash used as a label separator
    ("Tranche 2 — Life Cycle Management") becomes a colon-free space-hyphen-space
    so headings keep their shape. Distinguishing the two cases by whether the
    dash is followed by a capitalised word is imperfect, but a comma inside a
    heading reads far worse than a hyphen inside a sentence.
    """
    if not text:
        return text

    def _replace(match: re.Match) -> str:
        after = text[match.end():match.end() + 40].lstrip()
        # Label separator: next token starts a capitalised phrase or a digit.
        if after[:1].isupper() or after[:1].isdigit():
            return " - "
        return ", "

    out = _EM_DASH_RE.sub(_replace, text)
    out = re.sub(r",\s*,", ",", out)
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    return re.sub(r"[ \t]{2,}", " ", out)


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
    out = strip_review_markers(out)
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


# A second and third signal, because the phrase-repetition check was defeated by
# a failure mode that never repeats anything. Measured on Amlak run 3 against the
# human original as the control:
#
#                        IV control (n=51)      Shilpi run 3 (n=109)
#   longest run          max 75                 677, 645, then 119
#   function-word ratio  min 0.109, median .34  .028, .070, .098, .102, then .116
#
# THE FLOOR IS SET FOR ZERO FALSE POSITIVES, NOT MAXIMUM CATCH. A floor of 0.105
# would also catch the two marginal blocks at .098/.102, but leaves only 4/1000
# of margin below the control minimum on a 51-block sample — not enough evidence
# to justify it. A false positive triggers a pointless re-draft of good prose,
# which is worse than leaving a 43-word dense paragraph in place. Known miss,
# deliberately accepted; revisit when more human proposals are available to
# widen the control sample.
DEGENERATE_MAX_RUN = 120        # words with no sentence-ending punctuation
DEGENERATE_MIN_FUNCTION = 0.085  # share of tokens that are function words
DEGENERATE_MIN_WORDS_RATIO = 40  # ratio is meaningless on short blocks

# Function words. Not a content blocklist: these are the words English prose
# cannot do without. A thesaurus walk or a comma-less noun stack drops them,
# which is what makes their ABSENCE structural evidence rather than a guess.
_FUNCTION_WORDS = frozenset("""
the of and to a in is are for with on that by as be will from at this it or an
we our their its has have was were which not but can may all each per these
those such into than then there they them he she his her you your if when while
about across after before between during over under within without through
""".split())

# Structural lines that are not prose and must not be scored as prose. A GFM
# table row has almost no function words by construction and repeats its column
# shape on every line.
_TABLE_LINE_RE = re.compile(r"^\s*[|>]")
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s)")
# A genuine bullet is telegraphic and short. A 783-word line that merely OPENS
# with "1. " is prose, and dropping it hid the worst paragraph in Amlak run 3
# from the gate entirely. Length is what separates the two.
_MAX_LIST_ITEM_WORDS = 30


def _prose_only(text: str) -> str:
    """Drop table rows and genuine list items, leaving running prose."""
    kept: list[str] = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        if _TABLE_LINE_RE.match(line):
            continue
        if _LIST_MARKER_RE.match(line) and len(line.split()) <= _MAX_LIST_ITEM_WORDS:
            continue
        kept.append(line)
    return "\n".join(kept)


def longest_unpunctuated_run(text: str) -> int:
    """Longest span of words containing no sentence-ending punctuation.

    Prose comes in sentences. Text that runs hundreds of words without a full
    stop has stopped being prose, whatever the words are.
    """
    best = current = 0
    for token in (text or "").split():
        current += 1
        if re.search(r"[.!?][\"')\]]?$", token):
            best = max(best, current)
            current = 0
    return max(best, current)


def function_word_ratio(text: str) -> Optional[float]:
    """Share of tokens that are function words, or None if the block is short."""
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", _prose_only(text).lower())
    if len(words) < DEGENERATE_MIN_WORDS_RATIO:
        return None
    return sum(1 for w in words if w in _FUNCTION_WORDS) / len(words)


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
    """Is this text padded, collapsed or otherwise not prose? (verdict, reason).

    Three INDEPENDENT structural signals, because each previous fix was defeated
    by a failure mode one level more general than the one it caught:

      1. repetition   — a phrase repeats (the run-2 failure)
      2. run length   — hundreds of words with no full stop (the run-3 failure:
                        a single-pass thesaurus walk that never repeats, so
                        signal 1 scored it a clean zero)
      3. word density — function words vanish, leaving a noun stack

    Adding a fourth signal later is expected. Adding a list of banned words is
    not: a generative model routes around a blocklist and the next failure looks
    different again.
    """
    if not text or len(text.split()) < DEGENERATE_MIN_WORDS:
        return False, ""

    # Every check runs on PROSE ONLY. A 30-row markdown table repeats its column
    # shape on every line and has almost no function words, so scoring the raw
    # block flagged legitimate tables as degenerate and re-drafted them away.
    # That became reachable the moment drafted sections were allowed to contain
    # tables at all, which is exactly what IV's house style needs them to do.
    prose = _prose_only(text)
    if len(prose.split()) < DEGENERATE_MIN_WORDS:
        return False, ""

    count, phrase = repetition_score(prose)
    if count >= DEGENERATE_REPEATS:
        return True, f'phrase "{phrase[:60]}" repeats {count}x'

    run = longest_unpunctuated_run(prose)
    if run > DEGENERATE_MAX_RUN:
        return True, f"{run} words with no sentence-ending punctuation"

    ratio = function_word_ratio(prose)
    if ratio is not None and ratio < DEGENERATE_MIN_FUNCTION:
        return True, f"function-word ratio {ratio:.3f} (prose floor {DEGENERATE_MIN_FUNCTION})"

    return False, ""


def truncate_at_degeneration(text: str) -> str:
    """Cut text at the point it stops being prose, keeping the good part.

    Last-resort fallback when a re-draft also comes back degenerate. Cuts at a
    sentence boundary so the section never ends mid-clause.

    Handles collapse with NO repeated phrase (the run-3 failure) by walking
    sentences and stopping at the first one that is itself over-long or
    function-word starved. Both degenerate paragraphs in run 3 opened with
    legitimate content and collapsed part-way, so keeping the head is worth more
    than discarding the block.
    """
    degenerate, _ = is_degenerate(text)
    if not degenerate:
        return text

    _, phrase = repetition_score(text)
    if phrase and repetition_score(text)[0] >= DEGENERATE_REPEATS:
        first_word = phrase.split()[0]
        # Find the SECOND occurrence of the phrase's opening word after the
        # first — the repetition onset — then back off to the preceding
        # sentence end.
        positions = [m.start() for m in re.finditer(
            r"\b" + re.escape(first_word) + r"\b", text, re.I)]
        if len(positions) >= 2:
            head = text[:positions[1]]
            sentence_ends = list(re.finditer(r"[.!?](?:\s|$)", head))
            if sentence_ends:
                return head[: sentence_ends[-1].end()].rstrip()
            return head.rstrip()

    # No repetition to anchor on: keep sentences while they still look like prose.
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if not sentence.strip():
            continue
        words = sentence.split()
        if len(words) > DEGENERATE_MAX_RUN:
            break
        ratio = function_word_ratio(sentence)
        if ratio is not None and ratio < DEGENERATE_MIN_FUNCTION:
            break
        kept.append(sentence)
    return " ".join(kept).rstrip() if kept else ""


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
    n_review = count_review_markers(text)
    if n_review:
        issues.append(f"removed {n_review} review aside(s)")
    out = strip_meta_commentary(text)

    if strip_cites:
        n_cites = len(_CITATION_RE.findall(out))
        if n_cites:
            issues.append(f"stripped {n_cites} citation marker(s)")
        out = strip_citations(out)

    n_dashes = len(_EM_DASH_RE.findall(out))
    if n_dashes:
        issues.append(f"replaced {n_dashes} em-dash(es)")
    out = strip_em_dashes(out)

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
