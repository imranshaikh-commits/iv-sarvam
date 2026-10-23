#!/usr/bin/env python3
"""How accurately the brain reads real tenders: field accuracy per tender, and
whether two reads of the same file agree.

WHY THIS EXISTS
---------------
Design 2026-10-14, part 1e (docs/audits/DESIGN_RFP_READING_AND_MATRIX.md).
Run 1 read ESNAD's SOW wrong in three ways nobody saw until the proposal. The
target before v1.0: every critical fact right on all five test tenders, and
two consecutive reads identical.

FIXTURES ARE CLIENT CONTENT
---------------------------
They live in docs/evals/rfp/, which is gitignored (check with
`git check-ignore -v docs/evals/rfp/x.json`). One JSON file per tender, next
to the tender file itself:

    {"tender": "ESNAD SOW (scanned)",
     "file": "esnad_sow.pdf",
     "expected": {
        "population_by_domain": {"contains": ["5,000", "10,000", "50"]},
        "user_count": {"contains": ["5,000"], "not_contains": ["including"]},
        "out_of_scope": {"missing": true}}}

Each entry may also carry "pages" and "source" (where the tender states it);
the scorer ignores both, and the report prints the pages only.

"contains": every piece must be in the value (case-insensitive; a number must
match a whole number, so "50" is not found in "5,000"). "not_contains": none
may be. "missing": the tender does not state it, so nothing may be read.
"not_saas": drafting must not read the value as a SaaS deployment
(document_engine._looks_like_saas), e.g. a tender that lists SaaS only as an
option.

HOW TO RUN (it calls models, so on demand, never in CI). On the host, inside
the running brain, which has the keys and the PDF tools:

    docker cp docs/evals/rfp sarvam-brain:/tmp/rfp-evals
    docker exec -i sarvam-brain python - /tmp/rfp-evals < scripts/rfp_accuracy.py

Each tender is read twice from scratch (no stored reading); --once reads it
once. Nothing from the tenders is printed except field ids and the values the
reader produced for fields it got wrong.
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import sys
import tempfile


def _numbers(s: str) -> set:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s or "")}


def _has(value: str, piece: str) -> bool:
    if re.fullmatch(r"[\d,.]+", piece.strip()):
        return piece.strip().replace(",", "") in _numbers(value)
    return piece.lower() in (value or "").lower()


def score_fact(value, expected: dict) -> bool:
    """Is what was read right against the expected entry?"""
    if expected.get("missing"):
        return not value
    if not value:
        return False
    if expected.get("not_saas"):
        import document_engine        # drafting's own reading of a deployment answer
        if document_engine._looks_like_saas(value):
            return False
    return (all(_has(value, p) for p in expected.get("contains", []))
            and not any(_has(value, p) for p in expected.get("not_contains", [])))


def score_tender(answers: dict, expected: dict) -> dict:
    import rfp_intake
    wrong = [{"field": f, "got": answers.get(f), "expected": e}
             for f, e in expected.items() if not score_fact(answers.get(f), e)]
    return {"scored": len(expected), "right": len(expected) - len(wrong), "wrong": wrong,
            "no_expected_value": [fid for _, fid in rfp_intake.CRITICAL_FACTS
                                  if fid not in expected]}


def differences(first: dict, second: dict) -> list[str]:
    """Fields whose value differs between two reads (or is in only one)."""
    return sorted(k for k in set(first) | set(second) if first.get(k) != second.get(k))


async def read_with_the_brain(path: str) -> tuple[dict, list[int]]:
    """(answers, pages not read) from the brain's own reader, from scratch: no
    stored reading is used."""
    import app
    import intake_template
    import rfp_intake
    with tempfile.TemporaryDirectory(prefix="rfp-eval-") as tmp:
        pdf, why = await asyncio.to_thread(rfp_intake.as_pdf, path, tmp)
        if not pdf:
            raise RuntimeError(why)
        ex = await rfp_intake.extract_rfp(pdf, os.path.basename(path),
                                          intake_template.client_fact_ids(),
                                          app.rfp_structured_call, os.path.join(tmp, "pages"))
    return ex.answers(), ex.not_read_pages()


async def run(folder: str, read=read_with_the_brain, twice: bool = True) -> list[dict]:
    results = []
    for fixture in sorted(glob.glob(os.path.join(folder, "*.json"))):
        with open(fixture, encoding="utf-8") as fh:
            spec = json.load(fh)
        path = os.path.join(os.path.dirname(fixture), spec["file"])
        first, not_read = await read(path)
        result = {"tender": spec.get("tender") or spec["file"],
                  **score_tender(first, spec.get("expected") or {}),
                  "not_read_pages": not_read}
        if twice:
            second, second_not_read = await read(path)
            scored = score_tender(second, spec.get("expected") or {})
            result["second_right"], result["second_wrong"] = scored["right"], scored["wrong"]
            result["second_read_differs"] = differences(first, second)
            critical = {fid for _, fid in __import__("rfp_intake").CRITICAL_FACTS}
            result["critical_differ"] = [f for f in result["second_read_differs"] if f in critical]
            result["second_read_not_read"] = second_not_read
        results.append(result)
    return results


def report(results: list[dict]) -> str:
    lines = []
    for r in results:
        same = r.get("second_read_differs")
        gaps = sorted(set(r.get("not_read_pages") or []) | set(r.get("second_read_not_read") or []))
        both = (f"read 1: {r['right']}/{r['scored']} right, "
                f"read 2: {r['second_right']}/{r['scored']} right"
                if "second_right" in r else f"{r['right']}/{r['scored']} right")
        lines.append(f"{r['tender']}: {both}"
                     + ("" if same is None else
                        # Two reads with pages missing prove nothing about stability.
                        "; reads not comparable (pages not read)" if gaps else
                        "; two reads identical" if not same else
                        f"; two reads differ on {', '.join(same)}"))
        if same is not None and not gaps:
            crit = r.get("critical_differ") or []
            lines.append(f"  critical facts differing: {len(crit)}/7"
                         + (f" ({', '.join(crit)})" if crit else ""))
        if gaps:
            lines.append(f"  NOT READ: {len(gaps)} page(s), p. {', '.join(str(p) for p in gaps)}"
                         " (failed calls or unreadable scans: score this tender again)")
        wrong = ([("WRONG", w) for w in r["wrong"]]
                 + [("WRONG (read 2)", w) for w in r.get("second_wrong") or []])
        for label, w in wrong:
            # Only the expected pieces and pages: a fixture's notes quote the tender.
            exp = {k: v for k, v in w["expected"].items()
                   if k in ("contains", "not_contains", "missing", "not_saas")}
            pages = ", ".join(str(p) for p in w["expected"].get("pages") or [])
            lines.append(f"  {label} {w['field']}: got {w['got']!r}, expected {exp}"
                         + (f" (p.{pages})" if pages else ""))
        if r["no_expected_value"]:
            lines.append(f"  no expected value written for: {', '.join(r['no_expected_value'])}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print("usage: rfp_accuracy.py <fixture folder> [--once]")
        return 2
    results = asyncio.run(run(args[0], twice="--once" not in argv))
    print(report(results))
    print("JSON:")
    print(json.dumps(results))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
