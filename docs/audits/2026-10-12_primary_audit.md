# Shilpi / iv-sarvam — independent code audit

Reviewed 12 October 2026. Repository snapshot: `3a5083025d555501a49365e32153962ba537a5d4` (30 September 2026).

## Verdict

This is a serious, domain-informed prototype with useful retrieval, structured discovery, document generation and regression coverage. Keep those foundations. It is not yet dependable enough for unattended proposal production or broad shared use. The main problem is that important promises—approval, compliance, attachment identity and successful deployment—are not consistently enforced at the boundaries where they matter.

I would continue a supervised pilot, correct the high-priority issues below, and require an IAM/bid reviewer to approve every external document. A wholesale rewrite is unnecessary; a focused reliability and security pass is necessary.

## Scope and evidence

Read the backend orchestration, chat state, intake, retrieval/migrations, generation/export/QA paths, deployment, backups and representative tests. Ran local synthetic reproductions with external calls mocked. Inspected the repository, not the deployed EC2 instance, production Supabase policies/data, or actual confidential proposals. No production calls, paid model generations, database changes, commits or repository edits were made.

The findings distinguish executable local reproductions from static code findings and architectural risks. Network exposure in production, applied migrations, live model availability, actual document quality and restore viability still require environment-specific verification. This is not a full dependency-CVE or git-history secret audit.

**Test baseline:** Python 3.12.14; installed the repository's backend/script requirements and pytest in an isolated environment. The complete CI command, `python -m pytest -q backend/brain/tests scripts`, finished with **726 passed, 2 skipped, 4 warnings in 183 seconds**. The two D2-dependent tests could not run because D2 was unavailable. PDF export tests mock LibreOffice, so this does not establish rendered production quality. An initial run failed because this audit environment uses a SOCKS proxy and lacked `socksio`; installing that environment-only dependency resolved those failures without repository changes. The green suite coexists with the reproduced defects below.

All code references below are to the pinned commit. Paths beginning `backend/brain/` are shortened to their filenames in some explanations.

## What is good

- **Strong domain modeling:** discovery fields, proposal-type templates, vendor scope, RFP requirement IDs, eligibility gates and scope exclusions reflect real consulting work. This is much better than one large prompt that tries to produce a whole proposal.
- **Useful separation already exists:** `document_engine`, `diagram_engine`, `scope_filter`, `rfp_intake` and `supabase_client` are separate; generation receives retrieval/model helpers by injection. Build on that separation.
- **Retrieval accounts for corpus quality:** duplicate suppression, per-proposal diversity, topic/type reservation and a distinct reviewed product corpus are sensible mechanisms. Their production effectiveness still needs representative evaluation.
- **Structured diagrams:** typed specifications and validated transitions are preferable to blindly executing model-produced renderer source. Several render paths also use subprocess argument lists and timeouts.
- **Document care:** client-facing evidence stripping, explicit SME markers, image resolution safeguards and DOCX/PDF handling address problems that basic RAG demos miss.
- **Operational improvements are real:** current Compose binds brain/WebUI host ports to loopback, Caddy provides HTTPS, logs rotate, the WebUI volume is mounted read-only, CI exists, and migration 018 enables RLS on visual assets. Older documentation claiming otherwise should not be treated as present truth.
- **Many regression tests and recorded lessons:** there is meaningful engineering work here. The concern is the correctness of the assertions and missing system-level checks, not simply test count.

## Findings, in priority order

### 1. High — the exported compliance matrix makes unsupported commitments

**Evidence:** [document_engine.py:368–424](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/document_engine.py#L368), [test_document_engine.py:2328–2347](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/tests/test_document_engine.py#L2328).

`render_client_matrix` treats both `covered` and `partial` historical evidence as sufficient for “Compliant” if there is a section reference. With `missing` or `needs-human` evidence and a reference, it writes “Compliant - confirmed during Discovery.” The function receives no requirement-level discovery confirmation. References are selected by keyword overlap, which does not prove fulfillment and does not understand negation.

**Reproduced:** a partial synthetic requirement became “Compliant” and was omitted from the confirmation queue; a missing one became “confirmed during Discovery.” The existing test explicitly requires this conversion.

**Fix:** separate historical precedent, proposed solution coverage and explicit bid-team confirmation. Preserve uncertainty in the exported document. Require recorded confirmation for definitive statements; keyword matching should suggest references only. Replace the test that enshrines the unsafe conversion. Acceptance test: unsupported/partial requirements cannot become confirmed through rendering alone.

### 2. High — RFP intake can read another user's document

**Evidence:** [rfp_intake.py:77–126](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/rfp_intake.py#L77), [app.py:608–641](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/app.py#L608).

The picker scans a shared directory and selects the latest document from the preceding 15 minutes. It asks about ambiguity only when uploads are within 60 seconds of the newest. It has no user, chat or attachment ID. `run_rfp_extraction` ignores its supplied text/name when choosing the file, despite suggesting that the user tell it the filename.

**Reproduced:** with Alice's file 180 seconds old and Bob's 10 seconds old, the picker selected Bob's file without ambiguity. Alice's next extraction can therefore read Bob's tender, even without malicious intent.

**Fix:** forward the exact attachment ID through an OWUI integration; verify ownership/access and bind it to the intake session. If that binding is absent, refuse to guess. Test simultaneous uploads, repeated names, delayed reads and cross-user requests.

### 3. High — the deployment test gate ignores pytest failures

**Evidence:** [deploy/deploy.sh:22–26](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/deploy/deploy.sh#L22).

The container executes `python -m pytest ... | tail -1` under `sh`. The pipeline returns `tail`'s status. The outer script's `set -euo pipefail` does not apply inside this separate shell. A failing suite can therefore be followed by deployment, contradicting the script's central guarantee.

**Reproduced:** the equivalent inner-shell pipeline `sh -c 'false | tail -1'` exits 0.

**Fix:** run pytest directly, or capture its exit code before shortening its output. Test the deployment script with a deliberately failing suite and assert that the container swap never executes. Also verify the expected commit/config in the health check; `/v1/keepalive` establishes DB reachability, not all required dependencies.

### 4. High — approval is a chat convention, not a generation invariant

**Evidence:** [app.py:2660–2883](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/app.py#L2660), [chat_state.py:149–213](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/chat_state.py#L149).

`/v1/generate-proposal` accepts valid generation fields without an intake session, completed discovery or approved architecture. A provided proposal ID only selects diagrams to embed; it is not a prerequisite. Chat mode/session/proposal IDs travel in unsigned assistant-message markers and are trusted on return. Late discovery edits do not invalidate existing approvals.

**Reproduced:** a keyless TestClient POST with only client name, type and RFP text reached mocked generation and returned 200 without any approval lookup.

**Fix:** make the generation service load and validate authoritative workflow state. Bind approval to a specific intake/diagram revision. Persist skipped diagrams distinctly and report “approved or waived,” not “all approved.” Treat message markers as UI hints rather than authority. If a bypass is required for evaluation, make it an explicitly privileged mode.

### 5. High — approving one diagram approves every pending version

**Evidence:** [app.py:1660–1753](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/app.py#L1660), [app.py:1902–1930](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/app.py#L1902).

Regeneration inserts another `needs_review` row. Approval then loops over every `needs_review` diagram for the proposal. It is not bound to the displayed diagram ID/version. A superseded attempt can become approved alongside the current one. Partial write success is also reported as approval success.

**Reproduced:** one approval updated both an old and a current synthetic diagram.

**Fix:** approve an exact diagram ID and revision, atomically compare its prior status, supersede earlier attempts, and maintain explicit plan-item identity. Generation should embed only the approved active revision of each item.

### 6. High before wider rollout — application authorization is missing

**Evidence:** [app.py:2596 onward](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/app.py#L2596), [supabase_client.py:141–191](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/supabase_client.py#L141).

The brain does not authenticate its API callers. Reads/writes use the service-role key and select records by caller-supplied ID without organization/owner checks. The forwarded email is audit metadata, not verified authorization. RLS does not protect these service-role operations.

**Important boundary:** Compose keeps the brain's host port on loopback and Caddy does not publish these routes directly. This is not evidence that anonymous internet callers can currently access them. It is a missing application boundary behind OWUI and a blocker for claims of tenant/user isolation.

**Fix:** authenticate the trusted OWUI-to-brain channel, establish verified user identity, and enforce the intended organization/user-sharing rules for every object lookup and mutation. Single-organization use does not require building full multi-tenancy now, but it still needs a deliberate access policy. Direct intake/diagram routes also need consistent actor stamping.

### 7. High — input limits can silently remove tender obligations

**Evidence:** [rfp_intake.py:392–415](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/rfp_intake.py#L392), [app.py:2380–2450](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/app.py#L2380), [document_engine.py:3092–3100](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/document_engine.py#L3092).

Scanned PDFs are rasterized only through page 60 by default, with no comparison against the source page count. Compliance similarly slices requirements at 60. It sets `matrix.truncated`, but the normal client document path passes only `matrix.entries` to its renderer, losing the truncation warning.

**Impact:** a longer tender can produce an apparently complete proposal/matrix with obligations omitted. A safety cap is reasonable; hiding incomplete processing is not.

**Fix:** retain source totals, processed totals and omitted IDs/pages; propagate an incomplete status through API, chat and exported draft. Block a “complete” result until remaining pages/requirements are processed or explicitly waived. Prefer bounded batches over truncation.

### 8. High — client-logo retrieval permits server-side requests to arbitrary URLs

**Evidence:** [app.py:1580–1625](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/app.py#L1580).

A discovery answer can become an arbitrary HTTP(S) GET from the server; redirects are followed without checking destination networks. Bare existing local paths are also accepted. The 8 MB check occurs after the entire response has been read. This creates an SSRF primitive and avoidable memory risk. Actual access to cloud metadata/internal services depends on deployment/network controls and was not probed.

**Fix:** prefer authenticated uploaded image IDs. If remote fetching remains, constrain destinations, validate every redirect and resolved address, stream with byte limits, validate the decoded image and restrict local files to an owned asset directory. Clean up generated temporary logos.

### 9. Medium — concurrent answers can overwrite each other

**Evidence:** [supabase_client.py:159–191](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/supabase_client.py#L159).

Answer patches read the whole JSON object, merge in Python and overwrite it. Two requests can read the same revision and both report success while the last write discards the other answer.

**Reproduced:** two patches for different keys left only one new key.

**Fix:** use an atomic database merge for disjoint keys, and revision/optimistic concurrency checks for conflicting edits and workflow transitions. Test actual concurrent writes, not only sequential mocked saves.

### 10. Medium — long-running generation has no durable job identity

**Evidence:** [app.py:928–957](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/app.py#L928), [app.py:1960–2007](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/app.py#L1960), [document_engine.py:3038–3056](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/document_engine.py#L3038).

A potentially hour-long operation runs inside a request and makes an HTTP call back to the same service. Heartbeats improve the waiting experience but do not provide resumability, deduplication or cancellation. Retrying can start another paid generation. Semaphores are created per proposal, so multiple users multiply concurrency. PDF conversion and some render paths run blocking work directly in async handlers.

**Fix:** persist a job ID, input revision, status, outputs and checkpoints; deduplicate repeat submissions and enforce a shared concurrency/budget limit. A small database-backed worker is sufficient initially. Move blocking work off the event loop. Define cancellation and reconnect behavior explicitly.

### 11. Medium — backups do not establish full recoverability

**Evidence:** [scripts/backup_tables.py:1–35](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/scripts/backup_tables.py#L1), [backup_tables.py:120–131](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/scripts/backup_tables.py#L120).

The script explicitly excludes Storage objects. It also does not back up the OWUI volume holding chats/uploads/state markers. Its default table order places architecture diagrams before generated proposals, although diagrams reference proposals. A restore using that order into an empty schema can fail foreign-key checks. Separate paged HTTP reads are not a consistent database snapshot.

**Fix:** document the complete recovery set, back up source uploads and generated artifacts as well as database state, use dependency-aware restoration, and rehearse a restore into a fresh environment. S3 copying alone does not prove recovery. Verify whether separate host/volume backups already cover these gaps before duplicating them.

### 12. Medium — builds, tests and documentation can give false confidence

**Evidence:** [deploy/Dockerfile.webui:11](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/deploy/Dockerfile.webui#L11), [CI workflow](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/.github/workflows/ci.yml), [document_engine.py:60–70](https://github.com/imranshaikh-commits/iv-sarvam/blob/3a5083025d555501a49365e32153962ba537a5d4/backend/brain/document_engine.py#L60).

OWUI uses the moving `:main` image while source patches and filesystem attachment access depend on its internals. CI installs Python dependencies but not the production renderer stack; real D2 tests can skip and PDF export tests are mocked. Some tests inspect source text rather than resulting behavior. At least one important test requires incorrect compliance semantics (finding 1).

`CLAUDE.md` says no CI and describes a Sonnet/GLM chain; code has CI and defaults to Gemini/Qwen. `docs/PHASES.md` still describes public HTTP exposure, whereas current Compose/Caddy implement loopback/HTTPS. These contradictions are especially costly when a coding agent treats the documents as authoritative.

**Fix:** pin OWUI to an evaluated release/digest and lock the tested dependency set. Run a production-image smoke test covering upload binding, diagram rendering, approval, DOCX/PDF generation and download. Add deny-by-default external-network guards to unit tests. Keep one current operator guide and generate configuration descriptions from code where practical.

## The ugly part: recurring implementation patterns

`app.py` is 4,087 lines and `document_engine.py` 3,172 lines. Size alone is not a defect, but these files combine transport, state transitions, persistence, retrieval, approval, rendering and error recovery. Changes must be repeated across many paths, which explains the project's documented “built but never wired” failures.

“Fail-soft” has spread beyond optional features. An absent decorative image may be acceptable; uncertain authorization, failed approval writes, incomplete requirements and failed required-state persistence must not look successful. For example, `save_plan` ignores the `None` failure return, and the final interview path catches completion errors before proceeding to “Discovery is complete and saved.”

Chat is also being used as a workflow database and document selector. Invisible state markers and upload timestamps made an early integration possible, but are too fragile to remain the source of authority. The best refactor is a persisted proposal workflow behind the existing chat UI, not a new frontend for its own sake.

## Recommended implementation sequence

1. **Correct output truthfulness:** repair compliance semantics and truncation reporting; remove unsupported confirmation wording and update the tests.
2. **Make boundaries reliable:** fix attachment ownership, deployment exit status, exact-version approvals, generation preconditions and logo fetching.
3. **Protect shared state:** authenticate the internal API, implement access checks, atomic/versioned writes and authoritative workflow state.
4. **Make expensive work recoverable:** job IDs, idempotency, shared limits, checkpoints and complete backup/restore drills.
5. **Improve evidence before adding features:** run a production-image integration suite plus a held-out set spanning implementation, migration, MSS, multi-vendor, scanned and longer tenders. Score requirement recall, unsupported commitments, correct scope, reviewer edits, time and cost. Do not substitute page/word counts for quality.

For Claude Code, make each numbered fix a separate reviewed change with a failing behavioral regression test first. Preserve the domain templates and retrieval assets. Reject fixes that merely change a prompt while leaving the unsafe state transition or data path intact.

## Local reproduction summary

| Check | Result |
|---|---|
| Partial evidence rendered as compliant | Confirmed |
| Missing evidence rendered as confirmed during discovery | Confirmed |
| Latest shared upload selected without user identity | Confirmed |
| Generation with no authentication/session/approval, external calls mocked | HTTP 200 |
| One approval marks two pending versions approved | Confirmed |
| Concurrent answer patches lose a key | Confirmed |
| Failing command piped to tail under the deployment's inner shell | Exit 0 |

The reproductions are synthetic and local. They establish the code behavior, not an observed production incident.
