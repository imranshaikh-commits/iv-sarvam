> **Split from `06_NUANCES_AND_GROUND_RULES.md`** as part of the 2026-09-21
> Claude Code migration. See `06_PRODUCT_DECISIONS.md` for why this was
> split into three files.

# Shilpi — Corpus and Evidence Rules

What may and may not enter the proposal corpus, and how ingestion is
curated. Same standard as the rest of this project's decisions: evidence
beats precedent, but a change here is worth a conversation, not a silent
override.

---

## Corpus and evidence rules

**Never ingest client-authored documents into IV's voice bank.** The `RFP/` folder
in the Drive bank is client-written; ingesting it would make Shilpi ground IV's
voice in the client's words and cite it as IV past work. That folder is the
**pilot test set**, not bank content.

**Never ingest:** `RFP/`, `Questionaires/`, `Product Comparison/`, `Deck/`,
`PPT/`, `Demo/`, `AI/`, `Partner_Enablement/`, `Efforts/` (internal estimates and
commercials). Curate with `scripts/corpus_manifest.py` and review the CSV by hand
before ingesting anything.

**Ingest in vendor batches**, re-running a known query after each batch to confirm
retrieval improved rather than got noisier. Curate in bulk, ingest incrementally.

**More corpus is not automatically better.** The bank goes back to 2023. Old
proposals with superseded product versions will be retrieved and cited as
authoritative. A `year`/`outcome` filter or recency weighting is needed before
scaling up.

**Deliberately seek out MSS and migration proposals.** Coverage is 10
implementation / 1 MSS / **0 migration**, while the intake template supports all
three types. Ten more Ping implementations barely move the needle; two good
migration proposals open a proposal type that currently cannot be served.

**One Supabase project, not several databases.** ~13,000 chunks is small for
pgvector. The scaling problem is retrieval *precision*, not storage. The fixes
are metadata pre-filtering before vector search, an HNSW index, and two-tier
retrieval (find the closest past proposals, then search chunks within them).
Splitting into multiple databases would mean cross-database joins and merged
result sets in application code — solving a problem that does not exist.

---

> **Note (2026-09-21):** Sprint 4 added a SECOND corpus,
> `partner_products`/`partner_product_chunks`, for vendor product
> documentation (datasheets, architecture guides) rather than client
> engagement history. It is kept structurally separate from everything
> above for the same reason client-authored RFPs are excluded from this
> corpus: a vendor's marketing copy must never be cited as IV's own
> delivery history, or a client's words cited as IV's voice. Zero rows are
> ingested as of this note — see `SESSION_LOG_ESNAD_PILOT_AND_MIGRATION.md`
> and `09_NEAR_TERM_SPRINTS.md` (Sprint 7, not started) for what gathering
> real material for it will involve, across all nine OEM partners: Ping
> Identity, Okta, Oracle, IBM Verify, SailPoint, CyberArk, Semperis,
> Saviynt, Microsoft Entra ID.
