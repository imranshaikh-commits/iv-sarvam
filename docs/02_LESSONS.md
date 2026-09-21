# Shilpi — Lessons

Failure patterns observed repeatedly while building this system. Each one cost at
least one deploy cycle. They are recorded because they recur, and because
recognising the *shape* of a bug is faster than rediscovering it.

---

## 1. Built but never wired

The most expensive pattern, and it happened **four times**:

| Mechanism | What went wrong |
|---|---|
| Per-diagram-type guidance | Added as a parameter, appended to the end of `context_text`, then silently cut by an internal `[:4000]` slice. The deployment diagram was never told to show zones. |
| `DIAGRAM_LLM_MODELS` | Parsed from the environment, referenced nowhere. The model override was decorative for two days; every diagram ran on the default chain. |
| Client logo | The interview asks for it (area 10) and `assemble_docx` accepts a path — nothing connected them. Every document rendered a placeholder box. |
| Discovery answers | `generate_proposal` accepted only `rfp_text`. **21 of 22 discovery areas** were captured, written to Supabase, and discarded before drafting. |

**The rule:** when you add a parameter, write a test that asserts the *caller*
passes it. Testing the function in isolation proves nothing about whether it is
reachable.

---

## 2. Silent truncation

Three separate caps quietly destroyed input:

- `_answers_summary` limited to 2,500 chars — dropped ~30% of discovery
- `generate_diagram_spec` sliced context to 4,000 chars — ate the guidance
- `MAX_DRAFT_TOKENS` raised from 1,500 to 3,500 while a *tier* config still
  requested the old value, making the change a no-op

**The rule:** when raising a limit, grep for every other limit on the same path.
The one you changed is rarely the binding one.

---

## 3. Fixing the instance instead of the class

- Degeneration fix checked for the same **word** repeating. The model's actual
  failure was the same **phrase** repeating. The fix passed its own tests and
  caught nothing real.
- Meta-commentary fix blocklisted "the retrieved evidence" and "Note on Evidence
  Applicability". The model switched to "Evidence does not confirm…" and
  "Evidence cited originates from…".

**The rule:** ask what the general shape of the failure is. Blocklists lose to a
generative model. Match on structure (n-gram frequency, sentence subject), not on
the exact string that broke last time.

---

## 4. Asserting instead of measuring

Claude described D2 renders as "looking good" when they were in fact solid black
boxes — cairosvg cannot resolve D2's base64 `@font-face` fonts and rendered every
glyph as a filled rectangle. The claim was pattern-matched, not observed.

**What works:** a palette-independent measurement. Perimeter-to-area ratio of
dark pixels distinguishes real glyphs (~0.95) from solid blocks (~0.16). A
midtone-percentage check was tried first and gave a false negative on a
light-themed render — so the *metric* also needs validating, not just the output.

**The rule:** for anything visual, produce a number and compare it against a
known-good control.

---

## 5. Check the spec before blaming the renderer

A diagram that looked like a disconnected grid of boxes was blamed on D2/ELK. The
actual cause: the spec contained **12 nodes and 1 edge**, because the per-type
guidance described which nodes to include and how to group them and never once
said to connect them. Rendering the same nodes with a proper edge set produced
101,017 ink pixels versus 17,168 — same renderer, same styling.

**The rule:** when output looks wrong, inspect the intermediate representation
first. The renderer draws exactly what it is given.

---

## 6. Schema-valid is not the same as usable

`DiagramSpec.nodes` defaulted to `[]`, so `{"nodes": [], "edges": []}` was a
perfectly valid response. Instructor accepted it instantly, and a diagram
containing only its own title was rendered and shown to the user. The fallback
chain never triggered because nothing had raised.

Related: OpenRouter's structured-output support is **per endpoint, not per
model** — some providers enforce a JSON schema, others treat it as a strong hint.
An empty-but-well-formed array is a comfortable answer for a provider that treats
it as a hint.

**The rule:** validate content, not just shape. And on failure, correct once with
an explicit instruction, then fail honestly rather than presenting an empty
artefact as a result.

---

## 7. Unbounded external calls

Three separate paths had no timeout and inherited the OpenAI SDK's 600-second
default: answer extraction, drafting, and diagram-spec generation. The last one
hung a chat for ten minutes with no partial result and no error.

Adding a corrective retry later **doubled** the work inside a timeout that had
not been resized, so the retry died exactly when it was needed.

**The rule:** every external call gets a budget. When you add a retry inside a
timeout, resize the timeout.

---

## 8. Product judgement from the user has been reliably good

Two of the better design decisions in the system came from Imran, against
Claude's initial approach:

- **One diagram at a time with approval between each**, instead of generating the
  whole set then presenting it. This removed the timeout failure *structurally*
  rather than tuning around it.
- **"IV makes RFPs exhaustive."** Claude built an eval fixture that deliberately
  withheld sizing and phasing, reasoning that these were IV's recommendations
  rather than client inputs. That was wrong: the 22-area interview asks for them
  precisely because a consultant supplies them, and a drafting tool inventing
  server specifications would be a failure mode, not a capability.

**The rule:** when Imran pushes back on a design, the prior should be that he is
right about the product.

---

## 9. A limit you did not write is still a limit

Three separate bugs were "the number I raised was not the binding one":

- `hnsw.ef_search` defaults to **40**, so pgvector's HNSW index returns at most
  40 candidates however large the `LIMIT`. Two migrations widened the candidate
  pool to 60 and then 120; neither ever happened. In those 40 candidates for a
  migration query, 39 were implementation and one was migration — so a type
  reservation built on top had nothing to reserve, and measured *identical* with
  the feature on and off, twice.
- A **420-token** cap against a **220-word** instruction. Dense technical prose
  tokenizes above 1.35× per word, so the model wrote to its word target, began
  an `[SME REVIEW]` marker and was cut mid-word.
- **32 chunks (~70,000 characters)** of evidence for one compliance decision,
  against a 768-token response cap. The volume pushed the model to cite more
  sources than the response allowed, truncating the JSON and failing the whole
  requirement.

**The rule:** a hard cap should be a safety net against runaway output, never
the constraint that shapes normal output. When raising one limit, grep for every
other limit on the same path — and then measure, because a migration reporting
`success: true` proves the SQL parsed, not that the behaviour changed.

---

## 10. A gate must be legible at the size the reviewer sees it

Run 8 placed another client's Microsoft Project plan into a proposal — BTPN's
and STC's task names, durations and resource assignments, under *Case Studies*,
in a document addressed to Amlak.

Two failures compounded:

- The classifier filed Gantt charts as `corporate`, on the reasoning that they
  are "generic in shape, client-specific only in the dates". **A Gantt chart is
  a client's project plan.** That was a bad judgement, not a coding slip.
- The approval sheet rendered **260px thumbnails** on which the task names were
  illegible. A human approved all 341 assets in one pass and could not have seen
  the problem.

An approval gate that cannot be read through does not prevent anything; it
launders a decision nobody actually made. Thumbnails are now 720px with
click-to-zoom, and the sheet reflects current approval state rather than
pre-checking everything — which would have silently restored the rejects on the
next export.

---

## 11. A metric that does not measure what you care about will approve a regression in it

The retrieval scorecard approved topic-aware retrieval on relevance grounds
(type match 0.429 → 0.714). Run 7 then lost three sizing tables and rows from
every other table.

The scorecard measured source diversity, type match, fragment share and recency.
It did not measure **tabular evidence**, which is what IV proposals are actually
made of — 25 tables and 3,252 table words in the human original. `tabular_pct`
was added afterwards, and immediately showed the real problem: `sizing_prod`
scores 1.00, `sizing_dr` scores 0.12. The corpus is rich in production sizing
tables and nearly bare of DR-specific ones, and no retrieval tuning fixes a gap
in the source material.

---

## 12. Untracking a file does nothing if the ignore rule was never there

The asset review sheet — 341 base64 thumbnails of client proposal imagery, with
descriptions naming clients — was committed to the public repo by a `git add -A`.
The `.gitignore` rules for it had been drafted in conversation and never
actually added to the file.

It was caught only because `git status` happened to show the file as *modified*
rather than untracked. Purged with `git filter-repo` and force-pushed, but
GitHub may retain unreferenced blobs by SHA until garbage collection, and anyone
who cloned in the window still holds a copy.

**The rule:** after adding an ignore rule, run `git check-ignore -v <path>` and
read the output. Every artefact that touches client data now has that check:
`sarvam.env`, corpus manifests, retrieval scorecards, the asset sheet,
ingestion run output.
