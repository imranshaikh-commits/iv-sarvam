#!/usr/bin/env python3
"""Score a generated proposal (.docx), optionally against IV's own proposal
for the same deal. The automated second tester: it counts so a human only
has to judge.

WHY THIS EXISTS
---------------
Shilpi has one human tester. Every run used to be scored by hand (dump the
docx, count words, images, repeats, markers) and the counts drifted between
reviews. This makes the same measurement every time and turns the release
gate's "passes the scorecard" into a command with an exit code.

WHAT IT MEASURES
----------------
Size and shape: prose words, table words, tables, images, headings per level,
executive-summary words. Hygiene: [SME REVIEW] markers, "To be confirmed"/TBC
cells, degenerate paragraphs (document_qa.is_degenerate), the most-repeated
figures (ESNAD 09-25 said "5,000" 57 times; IV said it 4). Correctness hooks
from an optional deal config: other clients' names (leaks), systems that must
be named, terms that must not appear (invented systems). Gantt: a chart
directly under a table with week columns.

Thresholds are relative to the baseline where one is given, absolute
otherwise. Deal configs hold client names, so they live in docs/evals/
(gitignored), never in this repo.

USAGE
    python3 scripts/score_proposal.py run.docx
    python3 scripts/score_proposal.py run.docx --baseline iv.docx --config docs/evals/esnad.json
    python3 scripts/score_proposal.py run.docx --json          # machine-readable
Exit code 0 = every gate passes, 1 = at least one fails.

Config (all keys optional):
    {"baseline": "path/to/iv.docx", "client": "ESNAD",
     "must_mention": ["Nafath", "Power BI"], "forbid": ["Azure AD"],
     "other_clients": ["Amlak", "BTPN"]}
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "brain"))

from docx import Document  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402

try:
    from document_qa import is_degenerate  # noqa: E402
except Exception:  # noqa: BLE001 - the scorecard still runs without it
    is_degenerate = None

_NUMBER_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+\b")
_SME_RE = re.compile(r"\[SME REVIEW")
_TBC_RE = re.compile(r"(?i)\bto be confirmed\b|\bTBC\b|\bTBD\b")


def _text(el) -> str:
    return "".join(t.text or "" for t in el.iter(qn("w:t")))


def measure(path: str) -> dict:
    """Everything countable about one document, in body order."""
    doc = Document(path)
    styles = {s.style_id: s.name for s in doc.styles}
    m = collections.Counter()
    heads = collections.Counter()
    prose: list[str] = []
    table_text: list[str] = []
    exec_words, in_exec = 0, False
    last_table_weeks = False
    for el in doc.element.body.iterchildren():
        if el.tag == qn("w:tbl"):
            m["tables"] += 1
            rows = el.findall(qn("w:tr"))
            cells = [[_text(c).strip() for c in r.findall(qn("w:tc"))] for r in rows]
            m["table_words"] += sum(len(c.split()) for r in cells for c in r)
            m["tbc"] += sum(1 for r in cells for c in r if _TBC_RE.search(c))
            table_text.extend(c for r in cells for c in r)
            head = " ".join(cells[0]).lower() if cells else ""
            last_table_weeks = "start week" in head and "end week" in head
            continue
        if el.tag != qn("w:p"):
            continue
        imgs = len(list(el.iter(qn("a:blip"))))
        if imgs:
            m["images"] += imgs
            if last_table_weeks:
                m["gantt_charts"] += 1
            last_table_weeks = False
            continue
        text = _text(el).strip()
        if not text:
            continue
        last_table_weeks = False
        ppr = el.find(qn("w:pPr"))
        sid = ppr.find(qn("w:pStyle")).get(qn("w:val")) if ppr is not None and ppr.find(qn("w:pStyle")) is not None else ""
        hm = re.match(r"Heading (\d)", styles.get(sid, sid) or "")
        if hm:
            level = int(hm.group(1))
            heads[level] += 1
            if level == 1:
                in_exec = text.lower().startswith("executive summary")
            continue
        prose.append(text)
        words = len(text.split())
        m["prose_words"] += words
        m["paragraphs"] += 1
        if in_exec:
            exec_words += words
    body = "\n".join(prose)
    m["sme"] = len(_SME_RE.findall(body))
    m["tbc"] += len(_TBC_RE.findall(body))
    m["degenerate"] = sum(1 for p in prose if is_degenerate and len(p.split()) > 40
                          and is_degenerate(p)[0])
    # Figures count across prose AND tables: a reader sees both.
    repeats = collections.Counter(
        _NUMBER_RE.findall(body + "\n" + "\n".join(table_text))).most_common(3)
    return {**dict(m), "exec_summary_words": exec_words,
            "headings": {f"H{k}": heads[k] for k in sorted(heads)},
            "top_repeated_figures": repeats,
            "max_figure_repeats": repeats[0][1] if repeats else 0,
            "_body": body + "\n" + "\n".join(table_text)}


def _mentions(body: str, term: str) -> bool:
    return re.search(rf"(?i)\b{re.escape(term)}\b", body) is not None


def gates(run: dict, base: dict | None, cfg: dict) -> list[tuple[str, bool, str]]:
    """[(gate, passed, detail)]. Relative to the baseline where one exists."""
    out = []

    def rel(key, share, floor):
        want = max(floor, int(base.get(key, 0) * share)) if base else floor
        out.append((f"{key} >= {want}", run.get(key, 0) >= want,
                    f"{run.get(key, 0)}" + (f" (IV {base.get(key, 0)})" if base else "")))

    rel("prose_words", 0.45, 3000)
    rel("table_words", 0.70, 1500)
    rel("tables", 0.70, 10)
    rel("images", 0.50, 10)
    out.append(("exec_summary_words >= 400", run["exec_summary_words"] >= 400,
                str(run["exec_summary_words"])))
    cap = max(15, (base or {}).get("max_figure_repeats", 0) + 5)
    out.append((f"max_figure_repeats <= {cap}", run["max_figure_repeats"] <= cap,
                str(run["top_repeated_figures"])))
    out.append(("sme <= 20", run.get("sme", 0) <= 20, str(run.get("sme", 0))))
    out.append(("tbc <= 25", run.get("tbc", 0) <= 25, str(run.get("tbc", 0))))
    out.append(("degenerate == 0", run.get("degenerate", 0) == 0, str(run.get("degenerate", 0))))
    body = run["_body"]
    leaks = [c for c in cfg.get("other_clients", []) if _mentions(body, c)]
    out.append(("no other client named", not leaks, ", ".join(leaks) or "none"))
    missing = [t for t in cfg.get("must_mention", []) if not _mentions(body, t)]
    out.append(("named systems all mentioned", not missing, ", ".join(missing) or "all"))
    bad = [t for t in cfg.get("forbid", []) if _mentions(body, t)]
    out.append(("no forbidden terms", not bad, ", ".join(bad) or "none"))
    if cfg.get("expect_gantt", True):
        out.append(("gantt chart present", run.get("gantt_charts", 0) >= 1,
                    str(run.get("gantt_charts", 0))))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("docx")
    ap.add_argument("--baseline")
    ap.add_argument("--config")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cfg = json.loads(Path(a.config).read_text()) if a.config else {}
    base_path = a.baseline or cfg.get("baseline")
    run = measure(a.docx)
    base = measure(base_path) if base_path else None
    result = gates(run, base, cfg)
    ok = all(p for _, p, _ in result)
    public = {k: v for k, v in run.items() if not k.startswith("_")}
    if a.json:
        print(json.dumps({"passed": ok, "run": public,
                          "baseline": {k: v for k, v in (base or {}).items() if not k.startswith("_")},
                          "gates": [{"gate": g, "passed": p, "detail": d} for g, p, d in result]},
                         indent=2))
    else:
        print(f"{Path(a.docx).name}")
        for k in ("prose_words", "table_words", "tables", "images", "exec_summary_words",
                  "sme", "tbc", "degenerate", "gantt_charts"):
            print(f"  {k:20s} {public.get(k, 0):>7}" + (f"   IV {base.get(k, 0)}" if base else ""))
        print(f"  {'headings':20s} {public['headings']}" + (f"   IV {base['headings']}" if base else ""))
        for g, p, d in result:
            print(f"  [{'PASS' if p else 'FAIL'}] {g}: {d}")
        print("PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
