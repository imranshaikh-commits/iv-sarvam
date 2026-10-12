# Deep audit: remediation plan

**The plan is `ARCHITECT_DECISIONS.md`** (main session, 2026-10-12, on Imran's
instruction). It replaced the Fix / Park / Discard calls first proposed here
and in `BRIEFING_FOR_MAIN_SESSION.md`. Where the three documents disagree,
`ARCHITECT_DECISIONS.md` is right. Evidence for each finding is in
`RECONCILIATION.md`.

This file only tracks progress against it.

## The calls

| Decision | Count |
|---|---|
| Already fixed | 4 |
| Fix, in five phases | 49 |
| Parked, each with a trigger | 17 |
| Discarded | 2 |

## What changed from the first proposal

- Ten of the twelve discards became parked or fix-lite: they sit on the path
  the product is meant to take (a first colleague at release, a growing corpus,
  several roles on one proposal).
- F02 moved from last to Phase 0. F03 moved from parked to Phase 2.
- F17: drafting needs the architecture step finished (every planned diagram
  approved or skipped), not "at least one approved".
- F16: Word tenders are converted to PDF, not refused.
- F72: the capability lists on the stack stay, filtered by what is in scope.
- F08 and F10: measured on the ESNAD run before the wording is chosen.
- F09, F29, F22, F53, F55, F56, F67, F49: the cheap half of each is now in a
  phase.

## Progress

| Phase | Findings | Branch | State |
|---|---|---|---|
| 0: can be rebuilt, tells no secrets | F01 F43 F60 F02 F06 F50 F48 F70 F51 F71 F41 F52 F49 | `audit/phase-0` | built 2026-10-12, committed locally, NOT yet pushed; awaiting push, CI, main-session review and merge. Report: `PHASE_0_REPORT.md` |
| 1: the document tells the truth | F14 F61 F72 F54 F56 F42 F10 F24 F08 F13 F31 | `audit/phase-1` (stacked on `audit/phase-0`) | built 2026-10-13, committed locally, NOT yet pushed; awaiting push, CI, review, merge and Run 1 (ESNAD). Report: `PHASE_1_REPORT.md` |
| 2: the chat tells the truth, each colleague sees their own | F19 F21 F18 F69 F17 F03 F39 F40 F57 F53 F55 | `audit/phase-2` (stacked on `audit/phase-1`) | built 2026-10-13, committed locally, NOT yet pushed; awaiting push, CI, review, merge and a walk-through in chat. Report: `PHASE_2_REPORT.md` |
| 3: reading the tender and the evidence | F11 F12 F15 F16 F28 F30 F26 F27 F62 F67 F09 F29 F22 | `audit/phase-3` | not started |
| 4: release hardening | F46 | main session and Imran | not started |

Each phase: one commit per finding with a test that fails before the fix, CI
green, a phase report, review by the main session, then merge to `main` and a
deploy by Imran.
