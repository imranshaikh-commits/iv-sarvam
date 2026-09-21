> **Split from `07_FUTURE_VISION_AND_SPRINTS.md`** as part of the
> 2026-09-21 Claude Code migration. See `09_NEAR_TERM_SPRINTS.md` for the
> committed near-term half — everything in this file is direction, not
> commitment, exactly as the original file said.

# Shilpi — Future Vision

Where something here is speculative, it says so throughout — nothing in
this file is scheduled or committed. Treat it as direction, not a plan.

---

## Future state — what Shilpi becomes

### 1. From drafting tool to institutional memory

Today the corpus is a retrieval source. At maturity it is IV's **accumulated
proposal intelligence**: every engagement ingested, tagged by vendor, sector,
region, proposal type and outcome. The valuable question stops being "draft me a
proposal" and becomes "what did we propose last time we replaced Keycloak in a
GCC bank, and did we win?"

**The outcome loop is the piece that does not exist yet.** Recording won/lost
against each proposal, and weighting retrieval toward what actually won, turns
the bank from an archive into a feedback system. Nothing else on this list
compounds the way that does. *(Speculative — not designed or scoped.)*

### 2. Multi-user, role-aware

Blocked on Phase 4 auth. At maturity:

- Sales leads run discovery; solution architects review diagrams; the commercial
  owner completes pricing — each seeing the stage that is theirs.
- `approved_by` is populated, so the approval gate records *who* signed off.
- Proposals are shared artefacts with history, not one person's chat thread.

### 3. Beyond implementation proposals

The intake template already supports implementation, MSS and migration, but only
implementation has grounding. Beyond that, the same machinery plausibly extends
to **SoWs, RFP response matrices, and effort estimates** — all documents IV
already produces from the same discovery inputs. Each needs its own template and
corpus, not new architecture. *(Speculative.)*

### 4. Regional and linguistic reach

IV delivers into Saudi Arabia, Qatar and the wider GCC. Arabic output, or
bilingual proposals, is a plausible differentiator and a genuine engineering
question (RTL rendering in DOCX, retrieval across languages). Worth raising only
when a client actually asks. *(Speculative.)*

### 5. House-style visual parity

Auto-layout cannot reproduce IV's hand-composed decks, and no amount of renderer
tuning changes that. The two honest routes remain **editable export** (D2
supports PPTX, so a designer finishes in minutes) or a **designer-built SVG
template library** for the ~4 recurring diagram types. This is a design
investment, not an engineering one.

---

## What success actually looks like

Not page count. Not test count. Not corpus size.

**A reviewer opens a Shilpi draft, makes edits rather than rewrites, and sends
it.** The rework fraction in `04_EVAL_BASELINE.md` is the measure, and everything
in this document is subordinate to moving that number.

A useful secondary signal: **someone other than Imran chooses to use it** without
being asked to. Until that happens, the project has one user and one opinion.

> **Note (2026-09-21):** the ESNAD pilot is the first genuine step toward
> this — a real inbound RFP, not a recreation exercise. See
> `SESSION_LOG_ESNAD_PILOT_AND_MIGRATION.md`. The secondary signal above
> ("someone other than Imran") has still not happened as of this note.
