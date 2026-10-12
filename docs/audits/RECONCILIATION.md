# Deep audit: reconciliation against current code

Checked 2026-10-12 against `bab2a5b` (`main` at `3a8c175` plus the audit
documents). The report audited `3a50830`. Every row below was checked in the
current code, not taken from the handoff table or commit messages. Line numbers
are for `bab2a5b`.

Suite on this commit: `749 passed, 2 skipped` (local venv, no keys).

Production facts were read from the live database catalog (read-only) on the
same day: PostgreSQL 17.6, pgvector 0.8.2, one organisation, `auth.users` 0
rows, `org_members` 0 rows, RLS on for every public table, six private storage
buckets.

## Totals

| Status | Count | Findings |
|---|---|---|
| Fixed | 4 | F04 F05 F20 F45 |
| Partly fixed | 14 | F01 F03 F08 F11 F17 F18 F19 F23 F38 F46 F47 F48 F50 F70 |
| Open | 54 | all others |

Of the 54 open, six cannot bite IV as deployed today (marked *dormant*): F02
(no auth users; depends on the signup setting), F25 (reranker off), F44 and F59
(one organisation), F66 and the asset half of F67 (library images off).

What was not verified: nothing was run against a live model, a real Open WebUI
with two users, or a rebuilt database. Whether Supabase signups are enabled is a
dashboard setting that could not be read.

## Fixed

| ID | Evidence |
|---|---|
| F04 another user's upload | `rfp_intake.py:128-181`: uploads are filtered to the requester's Open WebUI user id, and refused when ownership cannot be read. Test `test_rfp_intake.py:228`. Left: checked against a mock of Open WebUI's `file` table, not a live two-user run; two files from the same user in different chats are still told apart by time and name only. |
| F05 logo fetch | `app.py:1623-1713`: public hosts only, each redirect re-checked, 8 MB streamed cap, must decode as an image, only the brain's own temp logo is accepted as a path. Test `test_multi_vendor.py:2359`. Left: DNS rebinding between check and connect (noted in the code). |
| F20 lost concurrent answers | `supabase_client.py:170-190` calls `merge_intake_answers` (`sarvam_020`), present in production. Test `test_document_engine.py:2442`. Left: no revision check for two edits to the same field. |
| F45 deploy gate | `deploy/deploy.sh:26-37`: no pipe after pytest, deploy stops on failure. Test `scripts/test_deploy_gate.py:31`. Left: the host gate runs the brain suite only; CI runs brain and scripts. |

## Partly fixed

| ID | Done | Still open |
|---|---|---|
| F01 legacy definer RPC | Not in production: the only `match_proposal_chunks` is the text-signature one, SECURITY INVOKER, pinned search_path. | The files would recreate it: `001_init.sql:218-238` defines the vector-signature SECURITY DEFINER function and no later migration drops or revokes it (`sarvam_008:130-132` drops other signatures). |
| F03 no authn/authz | Service key on every route (`app.py:126-138`, test `test_export_engine.py:421`); a session opened by one user is not readable by another (`supabase_client.py:98-106`, `:166`). | Ownership is checked on session reads only. Not on the answer merge (`supabase_client.py:178`), the diagram endpoints (`app.py:3268-3332`), or the proposal id that chat approval takes from the marker (`app.py:4055`). Rows with no owner stay readable. |
| F08 compliance overstated | "confirmed during Discovery" is gone; no reference means "To be confirmed"; every row that is not plainly Compliant goes on the bid team's list (`document_engine.py:406-431`). | Any row with a keyword-matched section reference that is not `covered`, including `missing` and `needs-human`, prints "Compliant, subject to confirmation during Discovery" (`:424-427`). |
| F11 silent RFP caps | Requirements over 60 stay in the matrix as unassessed and are reported in chat (`app.py:2538`, `:2146-2150`, test `test_multi_vendor.py:2383`); pages past 60 are announced. | The 12,000-character cut on pasted text sets `truncated` but nothing reads it on the drafting path (`document_engine.py:3108` reads `unassessed` only). Requirement text is cut at 600 characters (`rfp_intake.py:676`). Section prompts see the first 4,000 characters (`document_engine.py:1441`, also F28). Pages past 60 are still not read. |
| F17 approval not enforced | Generation needs an intake session and refuses while any current diagram awaits review (`app.py:2869-2872`, `:2938-2953`, test `test_export_engine.py:380`). | A session with no diagrams at all passes. Discovery completeness is not checked. Approvals are not invalidated when answers change. Chat markers are unsigned. |
| F18 approve-all-versions | Old versions are retired, only the newest of each title is approved, and a failed write keeps the gate closed (`app.py:1991-2038`, `diagram_engine.py:999-1013`, test `test_multi_vendor.py:2410`). | It approves every current diagram awaiting review, not the one on screen: a diagram the user skipped is approved by the next "approve". |
| F19 fail-soft reported as success | Interview, gap-fill, RFP and late answers check the write and retry once (`app.py:3563-3595`, `:3796-3835`, `:3902-3934`, `:4130-4157`). | `save_plan` discards the result (`app.py:1738-1746`); a failed `complete_intake_session` is swallowed and the reply still says "Discovery is complete and saved" (`:3659`, `:3861-3872`); a diagram whose insert failed is shown as if stored (`:1818-1842`); the generated proposal row is fail-soft (`:3047-3068`). |
| F23 actor labels | Identity is set once for every route and the channel is authenticated (`app.py:129`). | The stamp is a separate fail-soft write (`supabase_client.py:109-119`); no decision history. |
| F38 no durable job | One draft per session, two overall, blocking work off the event loop (`app.py:2795-2821`, `:3115`, `:3122`). | In process only: a restart loses the run; no resume, no job table. |
| F46 backups | Storage mirror, Open WebUI volume script, restore in foreign-key order (`scripts/backup_tables.py:52-58`, tests `test_backup_tables.py:92`, `:108`). | A restore into an empty project has never been rehearsed. |
| F47 tests | Network blocked in tests (`tests/conftest.py`); CI builds and tests the real image. | No test applies the migration chain; no cross-user or failure-injection tests at the database. |
| F48 reproducible builds | Open WebUI pinned by digest; Python packages locked (`constraints.txt`). | Base image `python:3.12-slim` is a moving tag (`Dockerfile:1`); the D2 download has no checksum (`Dockerfile:33`). |
| F50 large modules, stale docs | `CLAUDE.md` and the runbook refreshed. | `app.py` 4,275 lines, `document_engine.py` 3,187; the Dockerfile COPY list is still manual (`Dockerfile:42`). |
| F70 readiness | Deploy runs the renderer-stack test inside the image with `SHILPI_REQUIRE_STACK=1` and checks the build tag (`deploy/deploy.sh:30`, `:40-43`). | Buckets and the upload mount are not gated; `/health` reports only some switches (`app.py:2674-2688`). |

## Open

### Security and database

| ID | Evidence |
|---|---|
| F02 auto-membership (*dormant*) | `001_init.sql:71-91`. In production the trigger `on_auth_user_created` is enabled; `auth.users` and `org_members` are empty. Exposure depends on the signup setting. |
| F06 content in logs | `app.py:4211-4214` logs the first 80 characters of every vault query plus client names and headings; `supabase_client.py:55-80` appends PostgREST error bodies. |
| F07 evidence inside the system prompt | `app.py:766-782`. |
| F43 clean rebuild fails | `sarvam_015:65`, `:73` declare `embedding vector` with an HNSW index. Production's column is `vector(1536)`, so the fix is to make the file say what production has. |
| F44 org id mismatch (*dormant*) | `app.py:101` (`IV_ORG_ID`, fixed default) against `ORG_ID` in the scripts and a random id seeded by `001_init.sql`. Production has the one expected id. |
| F51 key id in docs | `docs/AWS_SETUP_COMPLETE.md:102`. The repo is private now; the key's status is Imran's to confirm. |
| F59 cross-org foreign keys (*dormant*) | `001_init.sql:135-136`; `sarvam_005:36` (`generated_proposal_id` has no foreign key). One organisation. |
| F60 storage not in migrations | The files create two of the six buckets (`sarvam_013:93`, `sarvam_017:94`; the rest are comments at `001_init.sql:350-365`). Production has all six. The path-policy mismatch does not apply while only the service role reads storage. |

### Truth of the client document

| ID | Evidence |
|---|---|
| F09 negation-blind grounding | Quote check accepts 60% token overlap (`app.py:2462-2486`); section references are keyword overlap (`document_engine.py:368-397`). |
| F10 cleanup removes the warning, keeps the claim | `document_qa.py:99-121` strips "(needs SME confirmation)" and similar; `:51` strips any short bracket holding a digit, such as "[90 days]". |
| F13 mandated structure promised, not used | `rfp_intake.py:415-418` says "I will follow it"; `answers()` (`:226-228`) does not carry it and nothing downstream reads it. |
| F14 "do not continue" advances | `app.py:3653-3654`: any message containing the word "continue" advances the eligibility gate. |
| F24 scope filter | "training remains in scope" inside `out_of_scope` matches the exclusion keyword (`scope_filter.py:121-130`); fewer than five sections restores the whole template, exclusions included (`:195-198`); an emptied subsection list is restored (`:251-258`). |
| F28 section prompts miss facts | First 4,000 characters of the RFP (`document_engine.py:1441`); discovery facts cut at 3,500 characters with general fields first (`:682-713`). |
| F29 provenance dropped | `sections_meta` keeps counts only (`document_engine.py:3162-3170`). |
| F30 character count as grounding | `document_engine.py:89`, `:1435`. |
| F31 won shown as delivered | `document_engine.py:276-340`. |
| F42 truncated draft shipped as complete | `document_engine.py:832-859`: after one retry the tail is trimmed and plain text returned, with no flag. |
| F61 "SaaS is not permitted" read as SaaS | `document_engine.py:463-469`. |
| F64 reference cache | One hour, no invalidation (`app.py:286-322`). |

### Workflow and delivery

| ID | Evidence |
|---|---|
| F12 mixed scanned PDFs | `rfp_intake.py:230-241`: one average for the whole document. |
| F15 longest value wins | `rfp_intake.py:670-676`; requirements are not de-duplicated. |
| F16 non-PDF uploads accepted | `rfp_intake.py:65` accepts `.docx .doc .txt .md .rtf`; extraction is `pdftotext`/`pdftoppm` only (`:464-499`). |
| F21 skip and empty plan | Skipping the last diagram replies "All N diagrams approved" (`app.py:4049-4070`, `chat_state.py:924-930`); the skipped diagram stays `needs_review`, so drafting is then refused. A stored empty plan is replaced by the default (`app.py:1728`). |
| F22 artifacts not tied to the approved design | A second `generated_proposals` row is inserted at generation (`app.py:3052`); approved specs are re-rendered at export (`:2959-2960`). |
| F39 export warnings hidden | PDF error and storage notes are in the JSON (`app.py:3121-3177`); the chat reply reads neither (`:2123-2140`). |
| F40 links expire, no re-download | `app.py:2137`; there is no download endpoint; logos are temp paths (`:1706`). |
| F41 loose request parsing | `app.py:2705-2708`, `:2739-2745`, `:2808`; an unknown section id falls back to the full template (`document_engine.py:2981-2984`). `top_k` on generation is now validated (`app.py:2896-2901`). |
| F49 usage ledger | Missing cost recorded as zero (`document_engine.py:768-778`); no budget. |
| F52 streaming fallback | `app.py:4254-4273`; `:4246` assumes a JSON error body. |
| F69 approval without a render | REST: `app.py:3315-3329`. Chat: a failed render leaves no image (`:1823-1836`) and approval does not require one (`:2016-2029`). |

### Diagrams

| ID | Evidence |
|---|---|
| F53 Graphviz fallback drops shapes | `diagram_engine.py:256`, `:270-271`. |
| F54 stack renderer invents arrows | `diagram_engine.py:462-540` never reads `spec.edges`; arrows come from column order (`:517-523`). |
| F55 D2 id collisions | `diagram_engine.py:311-313`, `:596`. |
| F56 compaction merges unlike nodes | `diagram_engine.py:896-941`. |
| F57 dedup drops approved diagrams | `document_engine.py:2143-2159`; the only trace is a log line (`:2181-2184`). |
| F58 connectivity check | `diagram_engine.py:841-857`. |
| F71 zero-week timeline crashes assembly | `diagram_engine.py:1481-1485` divides by the largest week; called unguarded at `document_engine.py:1949`. |
| F72 stack shows products and features nobody stated | `diagram_engine.py:1352-1360`. This picture is added to every proposal without a review step (`app.py:2976-2987`). |

### Retrieval

| ID | Evidence |
|---|---|
| F25 fan-out re-sorts by similarity (*dormant*) | `document_engine.py:1084`. The reranker is off by default (`app.py:470`). |
| F26 160-character dedup key | `document_engine.py:1077`. |
| F27 product cap is global | `document_engine.py:1121-1125`: one pool sorted, then cut; the comment says per vendor. |
| F62 vendor detection | `app.py:188-196`: substring match, seven vendors. |
| F63 scorecard | Calls the RPC directly (`scripts/eval_retrieval.py:174`); the zero-hit result lacks keys the printer reads (`:241-245`, `:313`). |

### Ingestion and corpus tools

| ID | Evidence |
|---|---|
| F32 tables lose their headings | `scripts/ingest_v2.py:147-160`: paragraphs first, then all tables. |
| F33 embeddings not validated | `ingest_v2.py:510-512`, `:875`; `ingest_partner_products.py:269`, `:426`. |
| F34 partial ingest marked done | Parent row before chunks (`ingest_v2.py:558-597`), skipped by hash next run (`:942-948`); the command exits 0 after failures (`:955-980`). |
| F35 manifest hash not re-checked | `ingest_v2.py:831`. |
| F36 product identity is the URL | `ingest_partner_products.py:404`. |
| F37 flags and launcher | `ingest_v2.py:816`; `scripts/run_ingest.sh` still passes `--input`. |
| F65 assets joined by file name | `scripts/extract_visual_assets.py:222-226`. |
| F66 recurrence score (*dormant*) | `app.py:252-256`. Library images are off. |
| F67 unpaginated reads | `app.py:235-244`, `:266-274` (dormant, images off); `extract_visual_assets.py:222`; `_rest_all` has no order (`app.py:290-304`). |
| F68 new chunks have no topic | The ingest payload never sets `section_topic` (`ingest_v2.py:572-597`). |
