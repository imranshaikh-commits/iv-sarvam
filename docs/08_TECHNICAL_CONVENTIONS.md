> **Split from `06_NUANCES_AND_GROUND_RULES.md`** as part of the 2026-09-21
> Claude Code migration. See `06_PRODUCT_DECISIONS.md` for why this was
> split into three files.

# Shilpi — Technical Conventions

Engineering conventions and tooling choices, with the reasoning behind
each — read before swapping a library or reversing a prior technical
decision.

---

## Technical conventions

**Deterministic parsing over LLM calls wherever possible.** The 22-area interview
parses `label: value` and bare positional answers with zero LLM calls; the model
is only a fallback for prose. Same principle drives `document_qa.py` — counting
and pattern problems with objectively correct answers belong in code, not in a
non-deterministic model that would also be grading its own family's output.

**An LLM reviewer is deferred until an eval harness exists** to prove it helps.
Judgement calls (technical accuracy, IV voice) are a legitimate use; adding one
now would be an unmeasured component in front of measurable bugs.

**Underscores are identifiers, not markdown.** This domain is full of
`data_retention`, `session_data`, `iam_vendor`. Only asterisks are treated as
emphasis markup; an underscore-italic rule silently mangles technical terms.

**Every external call gets a timeout budget.** The OpenAI SDK's default is 600
seconds. When you add a retry inside a timeout, resize the timeout.

**Validate content, not just schema.** A `DiagramSpec` with `nodes: []` is
schema-valid and useless. OpenRouter structured-output support is *per endpoint*,
not per model — some providers enforce the JSON schema, others treat it as a
strong hint, and an empty well-formed array is a comfortable answer for the
latter.

**A diagram is a graph.** Guidance that describes which nodes to include without
demanding edges produces a grouped list. Every diagram type's guidance carries an
explicit edge mandate, and `spec_shortfall()` validates edge count and orphan
ratio, not just node count.

---

## Tooling decisions and their reasons

| Decision | Reason |
|---|---|
| **D2 over Graphviz** | D2 draws `group` as a real nested container — what DMZ / secure zone / data zone diagrams need. Graphviz clusters lay out poorly. Graphviz retained as automatic fallback |
| **ELK over dagre** | More mature, better maintained, clean orthogonal routing, better nested containers. Free (MPL-2.0), bundled in the D2 binary |
| **TALA rejected** | Purpose-built for architecture diagrams, but a paid engine — commercial/server use needs a licence and unlicensed renders carry a watermark |
| **librsvg over cairosvg** | cairosvg cannot resolve D2's base64 WOFF `@font-face` fonts and renders every glyph as a solid black box. Measured: ~28% anti-aliased midtone pixels vs ~2% |
| **No headless browser** | D2's own PNG export shells out to one. SVG + librsvg keeps the image path dependency-light and offline |
| **No image-generation model for diagrams** | Mangles precise labels, breaks schematic consistency, and editing labels onto a raster is unreliable |
| **Per-object styling, not a D2 theme** | Stock themes paint containers as saturated slabs that bury labels. IV's decks are near-white with restrained accent |
| **Separate model chain for diagram specs** | The hardest structured output in the system; GLM proved marginal. `SHILPI_DIAGRAM_MODELS` routes only that call |

**Auto-layout will never reproduce IV's hand-composed decks.** "Pretty good" is
the honest target. Real parity needs either editable export (D2 supports PPTX) so
a human finishes it, or a designer-built SVG template library for the ~4
recurring diagram types. That is a design investment, not an engineering one, and
is deferred until pilot feedback says whether it matters.
