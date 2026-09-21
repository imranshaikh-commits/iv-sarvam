# Shilpi — Session Kickoff

The first five minutes of any new session. The purpose is to replace *memory of
where things were* with *observation of where things are*.

---

## 1. Get the actual current state

```bash
git clone https://github.com/imranshaikh-commits/iv-sarvam.git /tmp/shilpi && cd /tmp/shilpi && git log --oneline -5
```

Do not work from a description of the repo, including one in project knowledge.
Files here go stale; the repo is the truth.

## 2. Read the two maintained documents

- `README.md` — architecture and component decisions
- `docs/PHASES.md` — the forward plan

Everything else in `docs/` (`SPRINT_*`, `SESSION_LOG_*`, `DAY_1_COMPLETE.md`,
`HANDOVER.md`, `PROJECT.md`) is a **historical record**, accurate for its date
and not current truth. Do not cite them as the present state.

## 3. Establish the test baseline

```bash
cd /tmp/shilpi/backend/brain && pip install -q httpx python-docx pydantic instructor fastapi --break-system-packages
```
```bash
for t in test_chat_state test_chat_interview_gating test_proposal_depth test_intake_template test_export_engine test_document_qa; do printf "  %-30s " "$t"; python3 tests/$t.py 2>&1 | tail -1; done
```
```bash
python3 -m pytest tests/test_diagram_engine.py tests/test_document_engine.py -q 2>&1 | tail -2
```

`d2` is needed for the diagram tests:

```bash
cd /tmp && curl -sL https://github.com/terrastruct/d2/releases/download/v0.6.9/d2-v0.6.9-linux-amd64.tar.gz -o d2.tgz && tar xzf d2.tgz && export PATH=/tmp/d2-v0.6.9/bin:$PATH
```

If tests fail *before* you have changed anything, that is the first thing to
report — it means the pushed state is broken.

## 4. Confirm the host matches GitHub

Claude cannot check this. Ask Imran to run:

```bash
cd ~/iv-sarvam && git log --oneline -1
```

If the host commit differs from `origin/main`, the deployed system is not running
the code you are about to modify, and any observed behaviour is misleading.

## 5. Check Supabase is awake

The free-tier project idle-pauses at 7 days. If a Supabase MCP connector is
available, `get_project` on ref `jthrjmiulefmyrqtwsnz` should report
`ACTIVE_HEALTHY`. Otherwise ask Imran.

---

## Before writing any code

- Confirm which of the open items in `03_CURRENT_STATE.md` you are addressing.
- If you are adding a parameter, plan the test that asserts the **caller**
  passes it — not just that the function accepts it.
- If you are adding a new `.py` module under `backend/brain/`, add it to the
  `Dockerfile` COPY allowlist in the same change.

## Before saying anything is done

- Run the full suite, not just the tests you wrote.
- If the change is visual, produce a measurement, not an impression.
- Stage files to `/mnt/user-data/outputs/` and present them — a file that is
  written but not presented is unreachable.
- Give deploy commands **one per block**, and say explicitly whether the host
  needs a rebuild (only if `backend/brain/` or `deploy/` changed).

## After Imran pushes

Verify by cloning fresh and grepping the pushed commit — not by trusting the
sandbox copy. This has caught a real miss before (files that never made it into
the copy folder).
