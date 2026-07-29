# Sarvam — Forward Phase Plan

> Written 2026-07-29, after the first end-to-end validated run (discovery →
> diagram plan → per-diagram approval → drafting → DOCX/PDF with embedded
> diagrams). Supersedes the ad-hoc backlog for sequencing purposes;
> `docs/BACKLOG.md` remains the list of small deferred chores.

## Principle behind this ordering

The project is feature-rich and evidence-poor. Every remaining quality decision —
model choice, proposal length, whether diagram polish matters, how much corpus is
enough — is currently argued from intuition because **no Sarvam output has ever
been scored against a known-good human proposal**. So validation comes before
hardening, and hardening before scale.

The one thing not deferred is anything that would make validation itself
untrustworthy (see Phase 6.0 leakage check).

---

## Phase 6 — Validation (immediate)

Goal: replace opinion with measurement. Nothing here needs new architecture.

### 6.0 — Recreation test (single proposal, ground truth)

Pick ONE historical proposal, extract its inputs, feed them through the 22-area
discovery interview, and compare Sarvam's output against the human original.

**Leakage check first — this decides whether the test means anything.**
11 proposals are already ingested. If the chosen proposal is one of them, Sarvam
will retrieve its own source text and reproduce it, and the result will look
excellent while proving nothing. Confirm the candidate is NOT in the corpus:

```sql
select client_name, iam_vendor, proposal_type, year
from proposals order by client_name;
```

Pick a proposal that does **not** appear in that list. If the preferred candidate
is in it, either choose another or re-run the test with that proposal's chunks
excluded from retrieval.

**Protocol**
1. Choose a non-ingested proposal with a clear vendor + client + scope.
2. Extract the 22 areas of discovery input from it (client, vendor, scale, scope,
   architecture, integrations, compliance, timeline, commercials …). Record these
   verbatim as the test fixture so the run is repeatable.
3. Run a fresh Sarvam chat end to end using only those inputs.
4. Score the output against the human original using the sheet below.
5. Keep the fixture, the generated DOCX and the score in `docs/evals/`.

**Scoring sheet** (1–5 each, with a one-line justification — the justification
matters more than the number):

| Dimension | Question |
|---|---|
| Structural completeness | Does it contain the sections the human proposal has? What is missing entirely? |
| Technical accuracy | Are the vendor product names, roles and integration patterns correct? Any invented capability? |
| Specificity | Does it name the actual systems and counts, or does it retreat into generic IAM prose? |
| Grounding | Are citations present and do they support the claim? Are `[SME REVIEW]` markers where evidence genuinely ran out? |
| Diagram fidelity | Do the diagrams reflect the stated architecture (zones, regions, clusters, protocols)? |
| Rework estimate | What fraction would Ashish have to rewrite to send this? (This is the headline number.) |
| Length | Pages vs the human original — recorded, NOT optimised for. |

**Exit criterion:** a written verdict on whether the gap to "sendable" is length,
grounding, structure, or tone. Every later phase depends on that answer.

### 6.1 — Corpus expansion

Bulk-ingest the IV proposal bank. Bounds every quality metric downstream.

- Download the vendor + client folders from the `Sales-SoWs` Drive bank.
- Run `scripts/corpus_manifest.py` over a local mirror; review the CSV by hand.
- Ingest only `tier == ingest` rows, **in vendor batches**, re-running a known
  query after each batch to confirm retrieval improved rather than got noisier.
- Do NOT ingest `RFP/` (client-authored), decks, questionnaires, product
  comparisons or effort estimates — see the manifest script's rationale.
- Add `year` / `outcome` filtering or recency weighting before the corpus grows:
  the bank goes back to 2023 and stale architectures will otherwise be cited as
  current.

### 6.2 — Scored pilot (5–10 RFPs)

- Use the `RFP/` folder as the test set — these are real client RFPs and are the
  reason not to ingest them as IV content.
- Run each end to end; score with the 6.0 sheet.
- Involve Ashish for technical review and Rajnish for the "would you send this"
  judgement. **Nobody but Imran has used Sarvam to date; that is the real gap.**

### 6.3 — Evaluation harness

Turn 6.0/6.2 from an exercise into a repeatable suite.

- Store fixtures (discovery answers) + expected-content notes per case.
- A script that runs a fixture, generates, and reports the measurable parts
  (page count, citation density, `[SME REVIEW]` count, section coverage,
  diagram node/edge counts).
- Makes model and prompt changes measurable instead of guesswork — the absence of
  this is why several fixes this session broke their neighbours.

### 6.4 — Cost + usage telemetry

Log tokens and cost per proposal, per model, per call type. Estimated cost is
~$0.45/proposal on GLM-5.2 (144K in / 56K out), but this has never been measured.
Makes the model question self-answering.

---

## Phase 7 — Hardening & access (post-pilot)

Goal: make it safe for people other than Imran to use.

### 7.1 — Network exposure and TLS

`deploy/docker-compose.yml` binds Open WebUI as `8080:8080` — all interfaces,
plain HTTP, public EC2 IP. Client proposals and IV's proposal bank are therefore
served unencrypted over the internet.

- Restrict the security group to known IPs. *(This is a console change measured in
  minutes and does not need to wait for this phase — do it whenever convenient.)*
- Terminate TLS with Caddy or nginx in front of OWUI; obtain a certificate.
- Keep the brain localhost-only (already correct).

### 7.2 — Phase 4 auth / multi-tenancy

- Supabase Auth wired through OWUI so there is a real user identity.
- Populate `approved_by` on diagrams — today the approval gate records *that*
  something was approved but not *who* approved it.
- Multi-tenancy under existing RLS.

### 7.3 — Backup and recovery

- Scheduled `pg_dump` of Supabase to S3.
- Chunks are re-derivable from the bank; intake sessions, approved diagrams and
  generated proposals are not.
- The free-tier project has already idle-paused once.

### 7.4 — CI

- Run the test suite on push (GitHub Actions).
- ~175 tests currently execute only when run by hand.

---

## Phase 8 — Quality and scale (evidence-led)

Everything here should be justified by pilot findings, not assumed.

- **Hybrid search** — BM25 + reciprocal rank fusion alongside vector similarity.
  Do this if the pilot shows retrieval missing obvious matches.
- **Model selection A/B** — with 6.3 in place, compare drafting chains on real
  fixtures. Cost is not the binding constraint (~$0.04–$2.13 per proposal across
  the entire candidate range); quality decides.
- **Proposal length** — the 100+ page target is currently an unvalidated proxy
  for "impressive". Full depth measures 42pp; the `deep` tier (6 subsection
  facets) is merged but never re-measured. Resolve with pilot evidence: if
  reviewers do not ask for more length, close the item.
- **Diagram visual parity** — auto-layout cannot reproduce IV's hand-composed
  decks. If the pilot says diagram polish blocks client use, choose between
  editable export (D2 supports PPTX) or a designer-built SVG template library for
  the ~4 recurring diagram types. Otherwise leave it.
- **Durable diagram spec-template store** (per vendor + diagram type) — deferred
  from Pass 4.
- **Client-logo sourcing** with approval-gated embedding — deferred from Pass 5.

---

## Phase 9 — Production

- Rollout beyond the pilot group.
- Monitoring and alerting on the brain and the database.
- Operational runbook and key-rotation schedule.
- Re-apply Sarvam/IV branding in Open WebUI (see `docs/BACKLOG.md`).
- AWS budget alert (see `docs/BACKLOG.md`).

---

## Open items not yet placed

Tracked so they are not lost, but not yet worth a phase:

- Prompt/guidance versioning — changing diagram guidance silently changes all
  future output with no record of what produced a given proposal.
- Deploy friction — deploys are manual file copies by one person; CI (7.4) helps,
  but the bus factor remains.
