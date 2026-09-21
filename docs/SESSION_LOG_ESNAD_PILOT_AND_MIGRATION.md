# Session Log — ESNAD Pilot and Claude Code Migration

A narrative record of one continuous chat thread: completing in-flight
multi-vendor work, a live pilot run against a real inbound RFP that
surfaced nine separate bugs, a direct comparison against a real IV-authored
proposal for the same deal, four sprints of work built off that comparison,
and finally this migration from Claude Projects to Claude Code.

This is a history, not a reference. For current state, read `README.md`.
For durable rules and lessons, read `01`–`10`. This file exists so the
reasoning behind those files — why each decision got made, in what order,
under what pressure — isn't lost.

---

## 0. Starting point

This thread picked up mid-stream, continuing work from a prior session on
Bank BTPN (a ForgeRock migration) and a new RFP-intake path built for
reading an uploaded client RFP/SOW directly rather than only running the
22-area interview. That prior session had also started a **multi-vendor**
capability — ESNAD (Saudi Mining Services Company) was the RFP that
triggered it, since its SOW requires two separate IAM products (Ping
Identity for Access Management/CIAM, Saviynt for IGA/PAM), and the system
had never needed to represent two vendors as distinct workstreams before.

The multi-vendor build was **incomplete** at the start of this thread:
`generate_diagram_spec`'s second and third call sites hadn't been updated
to pass the new `vendor_scope_map`, and none of it had tests.

## 1. Finishing the multi-vendor build

Completed the wiring (`vendor_scope_map` reaching all three
`generate_diagram_spec` call sites, including a standalone REST endpoint
that doesn't look up a stored session and needed the map passed explicitly
in its request body). Wrote 27 tests covering `split_vendors` parsing,
per-vendor heading and retrieval fan-out, the `vendor_scope_bucket` dynamic
follow-up question, folding multi-turn vendor-scope answers, and the
explicit vendor-attribution clause injected into drafting and diagram
prompts. Full suite: 455 → 482 passing. Packaged and handed off for
deploy.

## 2. Rating a generated ESNAD proposal — and finding the real gap wasn't there yet

Asked to check a generated ESNAD proposal against the RFP and rate it. The
proposal itself was reasonably solid, but two problems stood out
immediately: the output **filename named the vendor instead of the
client**, and the compliance matrix cited **zero of ESNAD's 56 numbered
requirements** despite them having been successfully extracted earlier in
the same session.

Traced the requirements gap to its root: `rfp_intake.py`'s vision
extraction found all 56 requirements correctly, but nothing ever persisted
them past the extraction request — `document_engine`'s compliance-matrix
call passed a hardcoded `None` for requirements, so it fell back to
re-deriving them from `rfp_text` via a second LLM call, and `rfp_text` is
legitimately empty for a scanned RFP with no text layer. Fixed by
persisting the extracted requirements as JSON in the session's stored
answers and having the compliance matrix prefer them.

Traced the filename corruption separately: a later correction reply —
`"Ping Identity (Access Management, CIAM) and Saviynt (IGA, PAM)"` — had
no field labels, so the wide-sweep LLM extractor's own rule ("map a bare
comma list positionally to the questions in order") mapped it onto
`client_name`, `industry`, `country`, since those are declared first in
the 96-field schema. Fixed by giving the wide-sweep extraction path a
stricter prompt that forbids positional guessing, while leaving the
narrow, single-topic-bucket path (where positional guessing is genuinely
safe) untouched.

## 3. The diagram-plan editing bugs — found one at a time, live

With the user actually driving the chat, `add Privileged Access Management
Flow` failed with a generic "I didn't catch a change" error. Traced it to
`DIAGRAM_TYPE_MAP` — a fixed keyword dictionary the diagram-plan editor
matches free text against — having no entry anywhere near "privileged
access management". Added it.

Two turns later, `add Identity Lifecycle Flow diagram` failed the same
way. Same root cause, different missing entry.

At this point the user stopped the reactive patching and asked for a
proper count of the actual problem, not another one-off fix. Measured
directly: **23 of 30 reasonable domain phrasings** ("add Access Management
diagram", "add RBAC diagram", "add Federation diagram", and so on) failed
against the fixed vocabulary. A hand-maintained keyword dictionary was
never going to converge against the open-ended space of what a consultant
might type.

Rebuilt properly: the deterministic vocabulary match stays as the fast
path (instant, zero-cost, unchanged for anything it already covers), with
an LLM fallback for anything it doesn't recognize — constrained to the
diagram engine's own closed type set so it can't hallucinate something
unrenderable, and failing soft to the existing re-prompt on any error.
Verified against all 23 previously-failing phrases (all now succeed) plus
seven edge cases (LLM failure, invalid type returned, duplicate diagram,
the diagram-count cap, drop requests correctly never triggering an LLM
call, and the old deterministic function proven byte-for-byte unchanged
for backward compatibility). Checked the rest of the codebase for the same
fixed-vocabulary pattern and found no other instance.

## 4. The eligibility-gate "all met" bug

The RFP eligibility-gate prompt tells the user to reply "all met" to
proceed. Typing exactly that produced the same generic "I didn't catch a
value" error. The handler receiving the reply only recognized
`is_force()`'s drafting-gap vocabulary and the literal word "continue" —
"all met" was never wired in, despite the prompt promising it.

Fixed with a regex check for the phrase — with a negative lookbehind for
"not", since "not all met, we lack local presence" contains the substring
"all met" too and is exactly the disqualification concern the gate exists
to catch. Advancing past that input would have been worse than the
original bug.

## 5. The still-need gate blaming the client for its own missing fact

Mid-flow, a gate said *"Before the architecture proposal, I still need:
`industry`. These are IV's decisions, not the client's — an RFP does not
state them."* This was simply wrong: `industry` had already been correctly
extracted as "Mining" earlier in the same session, and it is a plain
client fact, never an IV decision. The code was applying one hardcoded
message to whatever field came back missing, regardless of which kind it
actually was. Fixed by checking the field against
`rfp_intake.IV_DECISION_FIELDS` and branching the message accordingly.

## 6. Generating the proposal — four more silent-failure sites

Generating the proposal produced a pre-flight gap-check message listing
nine empty fields, including `timeline_milestones` — which, like
`industry` before it, had already been correctly extracted. Same failure
shape, different location. Traced the actual mechanism this time: **four
separate call sites** discarded the return value of
`patch_intake_answers` (which returns `None` on any failure — network,
timeout, DB constraint) and showed the user a success message regardless.
A field answered correctly at intake could silently fail to save and
resurface as "missing" at a completely unrelated later gate, with no
visible connection to the original failure.

Fixed all four: the RFP-extraction persist, the RFP-review correction
handler, the still-need gap-fill handler, and the pre-flight drafting-gate
handler (the exact gate that had just produced the confusing message).
Each now checks the actual result, retries once, and tells the user
plainly if the save genuinely failed rather than claiming success.

## 7. Direct comparison against IV's real ESNAD proposal

With a full ESNAD proposal generated and the operator's boss's own
actual IV-authored submission for the same deal in hand, did a
section-by-section comparison — the first time in this project's history
Shilpi's output had been checked against a real answer rather than a
benchmark score or a reader's opinion.

**IV: 169 pages, 21,703 prose words, 66 images, heading depth to H5 with
roughly 90 sub-points per vendor product, four almost-fully-separate
workstream documents (one per product) each with its own solution
overview, architecture, implementation plan, RACI and BOQ.**

**Shilpi (pre-fix): 9,843 words, 11 images, two heading levels, one
generic IAM skeleton with vendor names substituted into headings rather
than genuinely organized by product.**

Shilpi's one clear advantage: 20 of IV's own numbered requirements cited
against zero in IV's own submission — IV does not do line-by-line
requirement mapping either.

Two live bugs surfaced by this comparison specifically: the vendor-split
parser only understood a parenthesized capability list, so the
"...for Access Management and CIAM, Saviynt for..." phrasing (no
parentheses — exactly how the operator actually typed it) split on every
comma and "and", producing four fake vendors and headings like "Why
CIAM"; and the sizing tables were internally incoherent — Production
correctly said N/A for a SaaS deployment while DR garbled a "mirrors
production" instruction against a table with nothing to mirror, and
Development invented a specific "4 vCPU, 16 GB" hardware spec for a
product that is never deployed on IV or client infrastructure.

This comparison became the basis for four sprints of structural work,
explicitly planned to avoid another live test run until they were all
done — test runs cost real LLM credits and the operator asked to hold
them to the end.

## 8. Sprint 1 — correctness

- **Vendor-split parser rebuilt** to read a connector word
  ("for"/"covering"/"delivering"/"providing") to separate a vendor name
  from its capability list, rather than only understanding parentheses.
  Extensible to any future partner with no hardcoded vendor or capability
  vocabulary — verified against the exact live-failing input, a
  three-vendor case, and every previously-passing phrasing unchanged.
- **Sizing tables made coherent.** All four (Production/DR/UAT/
  Development) now share one `is_saas` decision instead of four
  independent guesses, explicitly cross-reference each other, and name
  the exact hallucinated pattern ("4 vCPU, 16 GB") as forbidden.
- **A second, unrelated bug found while checking for drift:**
  `rfp_vision.py`, a fully-built and separately-tested module, sat
  completely unused — it contained exactly the fix ESNAD's SOW needed
  (keep the *fuller* value when a field is mentioned on multiple pages,
  not just the first). Ported the merge logic into the live extraction
  path.
- 538 tests passing by the end of this sprint, including negative
  controls proving each new test actually catches the bug it targets —
  one control initially proved nothing because the revert string didn't
  actually match the file; caught by checking, redone properly.

## 9. Sprint 2 — workstream depth

- **A rendering cap silently flattened depth.** A model's own markdown
  headers inside its drafted prose were hard-capped at heading level 3
  regardless of how deep the markdown actually went, and the first level
  collided visually with the subsection's own heading. Raised to level 5
  (matching IV's own maximum) and corrected the base offset.
- **The cap alone would have changed nothing** — checked directly and
  found *zero* subsection instructions asked a model to structure its own
  output with headers at all. Rewrote the per-vendor Solution Overview
  instruction (already vendor-fanned-out from Sprint 1, so the fix
  benefits every current and future vendor) to explicitly request
  capability-area headers with sub-feature detail only where genuine
  evidence supports it.
- **RACI and the licence BOQ now fan out per vendor.** RACI gained a
  dynamic column per vendor (previously hardcoded to exactly two parties)
  matching IV's real column order, with an explicit instruction against
  marking every vendor R/A/C/I uniformly on every row. The BOQ splits into
  one table per vendor plus a singular rollup, matching IV's actual
  structure.
- 562 tests passing. One real near-miss: an early negative control edited
  a string that didn't exist in the file, so the "control" briefly proved
  nothing — caught by verifying the edit actually landed before trusting
  the result.

## 10. Sprint 3 — missing house sections

- Added **Project Resources**, an **Initial Project RAID Log**, and
  explicit **Scope Exclusions**, placed immediately after RACI to match
  IV's own document order, for both the implementation and migration
  proposal types (the migration-type RACI table turned out to have the
  identical hardcoded-columns problem Sprint 2 had just fixed for the
  implementation type — fixed there too, for consistency).
- **Post-Production Support split into its own section** with real
  AMC/SLA tiers, out of what had been one folded paragraph inside
  Knowledge Transfer — matching IV's own structure, where this is a full
  section, not a training-and-support afterthought.
- **Two "built but never wired" gaps caught by pre-existing tests doing
  exactly their job:** the new section had no entry in the
  discovery-answer routing map, so it would have drafted ungrounded
  regardless of what the consultant supplied; and the new RAID log
  duplicated content the document's appendix pack already generated as a
  generic boilerplate placeholder, so the appendix-supersession logic
  needed extending to skip itself once the richer body version existed.
- 569 tests passing.

## 11. Sprint 4 — partner product corpus infrastructure

The operator explicitly widened scope here: not just Ping Identity and
Saviynt, but all nine of IV's OEM partners — Ping Identity, Okta, Oracle,
IBM Verify, SailPoint, CyberArk, Semperis, Saviynt, Microsoft Entra ID —
and asked for the infrastructure built generically enough that a tenth
partner is a curation task, not a code change.

- **A second corpus, live in Supabase**, structurally separate from the
  proposal-history vault: `partner_products` / `partner_product_chunks`,
  mirroring the existing schema and RLS pattern, plus a
  `match_partner_product_chunks` retrieval function — tested directly
  against the real database. Zero rows ingested; gathering real material
  is Sprint 7, deliberately not started yet.
- **Retrieval and drafting wired through**, with a design decision worth
  keeping visible: a well-documented product must never mask a genuine
  need for client-specific review, so the "weak evidence, needs SME
  review" trigger is computed from proposal-history evidence only, never
  blended with product-corpus evidence. Verified with a negative control
  that initially passed for the wrong reason (the test setup made the
  distinguishing condition unreachable) — caught, and the test rebuilt to
  actually isolate the line it was meant to protect.
- **Two unrelated findings while inspecting the live database:**
  `public.visual_assets` (939 rows of proposal imagery) has Row Level
  Security disabled outright, exposing it to the Supabase anon key — not
  fixed, since fixing it requires deciding real access policies first, or
  enabling RLS with none just blocks all access instead of securing it.
  And the Sprint 4 schema change was applied directly against the live
  database and has no corresponding file under `supabase/migrations/` —
  the repo's migration history is now behind the live schema.
- 581 tests passing.

## 12. README.md brought current

The repo's README — 886 lines, its own maintained source of truth per
`05_SESSION_KICKOFF.md` — hadn't been updated since 2026-09-09 and
predated all of the above. Updated it in nine localized edits rather than
a rewrite: the status badge and dashboard date, a new dated entry
documenting everything in sections 2–11 above, the "would a senior
architect sign it" gating question rewritten to point at the real IV
comparison instead of an abstract opinion, the two new security/infra
findings added to Known Gaps (and a now-false absolute claim — "port 8080
is the only item with a live security consequence" — corrected), an
internal inconsistency already present before this session ("seven scored
runs" contradicting "eighteen" stated elsewhere) fixed, and the
Repository Guide file tree corrected to include four real backend modules
it was missing entirely.

Deliberately left alone: the overall completion percentage and Phase 6's
row, since the README has an explicit rule against hand-adjusting those
by feel and there was no principled new number to give them — flagged as
pending a deliberate re-score instead. Also left alone, for lack of any
evidence either way: whether the repo has been made private, whether a
senior architect gave a verdict on a draft, whether an MSS run has
happened. Stated as still-open rather than assumed resolved.

## 13. Architecture handoff document

Produced a full infrastructure handoff (`ARCHITECTURE`, `CONFIGURED BY
HAND`, `DECISIONS AND CONSTRAINTS`, `GOTCHAS`) grounded in a fresh clone
plus direct inspection of the live Supabase project rather than memory —
confirming, among other things, that Row Level Security is schema-present
but not actually load-bearing today: every RLS policy is gated on
`org_members`, which has zero rows, so the system only works because the
brain connects with the service-role key, which bypasses RLS entirely,
and every row is stamped with one hardcoded org ID. This is architecturally
single-tenant today, with multi-tenant scaffolding sitting dormant, not
actively enforced — worth knowing before anyone "cleans up" the RLS
policies assuming they're protecting anything yet.

## 14. This migration — CLAUDE.md and the `docs/` restructure

Proposed, then built, a `CLAUDE.md` for the repo root and a `docs/`
structure carrying the seven original project-knowledge files across —
`01`, `02`, `03`, `04`, `05` unchanged; `06_NUANCES_AND_GROUND_RULES.md`
split three ways (product decisions, corpus rules, technical conventions,
since it mixed three genuinely unrelated audiences under one "ground
rules" umbrella); `07_FUTURE_VISION_AND_SPRINTS.md` split two ways
(near-term sprints, future vision — the original file's own text already
drew this line). Flagged rather than silently carried forward: `03` and
the near-term-sprints half of `07` are stale relative to everything in
this log, and `03`'s job now visibly overlaps with the README's own
Progress Dashboard with no stated precedence between them. This file was
added last, at the operator's request, specifically so the reasoning
above — not just the current end-state — survives the move to a tool that
reads files from disk instead of a maintained project-knowledge panel.

---

## What to read next

If you're picking this project up cold: `05_SESSION_KICKOFF.md`, then
`README.md`, then `01_OPERATING_MANUAL.md`. This file is for
understanding *why* things are the way they are, not for orienting to
*what* they are — start there instead.
