> **Split from `06_NUANCES_AND_GROUND_RULES.md`** as part of the 2026-09-21
> Claude Code migration. That single file mixed product decisions, corpus
> curation rules, and engineering conventions under one "ground rules"
> umbrella — three different audiences with no reason to load all three at
> once. This file holds the product/business decisions and the naming and
> cost sections. See also `07_CORPUS_RULES.md` and
> `08_TECHNICAL_CONVENTIONS.md`.

# Shilpi — Product Decisions

The decisions, conventions and domain facts established while building this
system. `02_LESSONS.md` records mistakes; this records **what we decided and
why**. When a future change seems to contradict something here, that is worth a
conversation rather than a silent override — but these are decisions, not
scripture, and evidence beats precedent.

---

## Product principles

**The V1 contract: no drafting until the architecture is approved.** This is a
hard gate, enforced in the chat flow — a "generate the proposal" request before
diagram approval is refused. It exists because a proposal built on an unreviewed
architecture is worse than no proposal.

**One diagram at a time, with approval between each.** Originally the whole set
was generated then presented, which repeatedly exhausted the time budget and
dropped diagrams silently. Agreeing a *plan* first (which generates nothing),
then producing one diagram per turn, removed the failure mode structurally
instead of tuning around it. It also gives a real review point per diagram.
*(Imran's design, and better than the alternative.)*

**IV makes RFPs exhaustive.** A client RFP is never complete; IV supplies sizing,
cluster topology, phasing and commercial structure. The 22-area interview asks
for those things precisely because a consultant provides them. Shilpi is a
*drafting* tool, not an architect — a system inventing server specifications for
a client proposal is a failure mode, not a capability. Evaluation fixtures must
therefore supply the full expert picture, not just RFP inputs.

**Citations are removed completely** from generated proposals. Not kept in an
internal-review variant — gone. *(Imran asked twice before this was actioned;
product directives should not need repeating.)*

**Length is not a goal.** The 100+ page target was closed as an unvalidated proxy
for "impressive". Shilpi already produces 2.7× the human original's prose while
conveying less structured information. Shorter and tighter is progress.

**IV proposals are table-heavy and concise; Shilpi is prose-heavy and bloated.**
25 tables vs 9, 6,648 prose words vs 18,008. Sizing, RACI, timeline, BOQ and
payment milestones belong in *tables* — scannable and precise. This is the
current biggest quality gap.

**Fail honestly rather than present an empty artefact.** An empty diagram with a
title, or a proposal section that quietly omits supplied facts, is worse than an
error message. Correct once with an explicit instruction, then say it did not
work.

**Every stock image needs human approval before it lands in a client document** —
the same discipline as the diagram approval gate. An unreviewed vendor screenshot
inserted silently is worse than no image.

---

## Naming and identity

**Shilpi** (शिल्पी) — Sanskrit for *architect, craftsman, one who shapes*.
Pronounced *SHIL-pee*. Renamed from **Sarvam** in August 2026.

**Internal identifiers deliberately kept as `sarvam`:** Docker container names
(`sarvam-webui`, `sarvam-brain`), the compose service keys and project name, the
`scripts/sarvam.env` path, and the GitHub repo (`iv-sarvam`) plus the local
directory `~/iv-sarvam`. Renaming the containers would break Open WebUI's stored
connection to the brain (held in OWUI's own database inside the persistent
volume); renaming the repo would break existing clones and remotes. Neither is
visible to a user. Only `WEBUI_NAME` and `MODEL_ID` changed.

**Historical documents were not rewritten.** `SPRINT_*`, `SESSION_LOG_*`,
`DAY_1_COMPLETE.md`, `HANDOVER.md` still say Sarvam and should — they are
accurate records of their date. `PROJECT.md` and `BACKLOG.md` carry a one-line
rename note instead.

---

## Economics

Estimated **≈ $0.45 per full proposal** on GLM 5.2 (144K input / 56K output
across 24 drafting calls + 4 diagram specs). The full candidate range across
models is **$0.04 to $2.13**.

**Cost is not the binding constraint.** The entire spread is about $2 for a
document that takes a consultant days. Choose on quality. For long-form
generation with short input and long output, output token price dominates.

> **Note (2026-09-21):** `README.md` also carries a `## Costs` section with
> overlapping figures. Nothing currently designates which is canonical —
> worth deciding before the two drift apart the way `03_CURRENT_STATE.md`
> and the README's dashboard did. See
> `SESSION_LOG_ESNAD_PILOT_AND_MIGRATION.md`.
