"""
Sprint 5 — document-production engine.

Turns chat drafts into a downloadable, formatted Word (.docx) proposal.

Design note (circular-import avoidance):
  This module MUST NOT import app.py. app.py imports `generate_proposal` from
  here, so the dependency flows one way only. The reusable brain helpers
  (embed_query, retrieve_chunks, build_grounded_system, run_compliance_matrix,
  render_matrix_markdown) are PASSED IN as callables by the endpoint. That keeps
  the module importable with no secrets present, which is what lets the smoke
  test exercise it offline by passing stubs.

  The single OpenRouter network call is isolated in ``draft_with_openrouter`` so
  tests can monkeypatch exactly that one function.
"""

from __future__ import annotations

import asyncio
import contextvars
import io
import itertools
import logging
import os
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import httpx
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

import branding
import document_qa
from proposal_templates import (
    REFERENCE_LIST,
    engagement_domains,
    COMPLIANCE_SECTION_ID,
    SUBSECTION_FACETS,
    DepthTier,
    SectionSpec,
    get_depth_tier,
    get_template,
    split_vendors,
    topic_for,
)
import asset_selection
import diagram_engine
import scope_filter

# Two per section: IV's proposals carry 37 images across 11 sections, and a
# third of those are per-deal architecture drawings we cannot reuse.
_ASSETS_PER_SECTION = int(os.environ.get("SHILPI_ASSETS_PER_SECTION", "2"))

log = logging.getLogger("shilpi-brain.doc-engine")

# --- config (env with safe defaults; NEVER required at import) --------------
# app.py hard-requires OPENROUTER_API_KEY at import; this module must not, so
# that it stays importable in a keyless environment (smoke test / CI).
OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
# LLM models. The single definition: app.py imports these, so the chat path and
# the drafting path can never run on different models. Defaults match what
# production runs (Gemini 3.8 Flash since 2026-09-24), so losing the env line on
# the host changes nothing; an override is still logged at startup.
PRIMARY_LLM_MODEL = os.environ.get("SHILPI_PRIMARY_MODEL", "").strip() or "google/gemini-3.8-flash"
FALLBACK_LLM_MODEL = os.environ.get("SHILPI_FALLBACK_MODEL", "").strip() or "qwen/qwen3-235b-a22b-2507"
TOP_K = int(os.environ.get("TOP_K", "8"))
# Evidence sent with every subsection of a section. ESNAD 09-24 prompts were
# 10-32k tokens, mostly these chunks, re-sent per subsection. Both are env
# knobs so a quality regression can be rolled back without a deploy.
SECTION_EVIDENCE_CAP = int(os.environ.get("SHILPI_SECTION_EVIDENCE_CAP", "16"))
PRODUCT_EVIDENCE_PER_VENDOR = int(os.environ.get("SHILPI_PRODUCT_EVIDENCE_PER_VENDOR", "6"))
# Concurrency is what trips OpenRouter's in-flight budget ceiling: the limit is
# on requests in flight at once, not on spend. Run 11 lost sixteen subsections
# to a 402 at concurrency 3 with a 61-subsection template. Retry with backoff is
# the real fix (see _draft_with_retry); this reduces how often it is needed.
DOC_CONCURRENCY = int(os.environ.get("DOC_CONCURRENCY", os.environ.get("COMPLIANCE_CONCURRENCY", "2")))

# Below this max-similarity the evidence is considered weak and the section is
# flagged for SME review.
WEAK_EVIDENCE_THRESHOLD = float(os.environ.get("DOC_WEAK_EVIDENCE", "0.55"))
# Characters of client-supplied discovery detail that count as "this section was
# briefed". Roughly two substantial answers. Below it, weak corpus evidence
# still triggers a review marker.
MIN_DISCOVERY_FOR_GROUNDING = int(os.environ.get("DOC_MIN_DISCOVERY", "200"))

SME_REVIEW_MARKER = "[SME REVIEW]"
# A section the model was never able to attempt is NOT the same as a section
# drafted from thin evidence, and the document must not present them alike.
# Run 11 lost sixteen subsections to an OpenRouter 402 and labelled every one
# "[SME REVIEW REQUIRED]" -- indistinguishable from "the corpus was quiet here".
# Hours went into debugging a content problem that was a billing problem.
DRAFT_FAILED_MARKER = "[DRAFTING FAILED - NOT ATTEMPTED]"
# Marks a grounded, generic placeholder emitted when a subsection LLM call
# returns null/empty even after one retry. Not a fabricated fact — an explicit
# assumption that an SME must confirm/replace before client use.
ASSUMPTION_MARKER = "[ASSUMPTION]"

# Type aliases for the passed-in brain helpers.
EmbedFn = Callable[[httpx.AsyncClient, str], Awaitable[list[float]]]
RetrieveFn = Callable[..., Awaitable[list[dict]]]
BuildSystemFn = Callable[[list[dict]], str]

_SECTION_SYSTEM_TEMPLATE = """You are Shilpi, InspiritVision's internal proposal assistant (an IAM consulting firm).
You are drafting the "{title}" section of a {proposal_type} proposal for {client_name}{vendor_clause}.{vendor_scope_clause}

SECTION PURPOSE: {purpose}

TWO SOURCES, DIFFERENT AUTHORITY:
- CLIENT-SUPPLIED FACTS are what this client told us in discovery. They are the
  MOST authoritative source in this prompt. A claim that rests on them is fully
  grounded and needs NO review marker, even if nothing in the EVIDENCE mentions
  it. The EVIDENCE is other clients' past proposals; it cannot corroborate a
  fact about THIS engagement, and its silence means nothing.
- EVIDENCE is IV's past proposals, for house style, technical patterns and
  wording. Cite it inline as [N].

HARD RULES (non-negotiable):
1. Ground every material technical claim in the CLIENT-SUPPLIED FACTS or the
   EVIDENCE. Cite evidence inline like [1], [3] referring to evidence numbers.
   Client-supplied facts are stated directly and are not cited.
2. NEVER invent product versions, compliance/regulatory claims, pricing, SLAs, or client commitments.
   Write a literal "{marker}" note ONLY when NEITHER source covers something the
   section genuinely needs — a figure only the consultant or the commercial
   owner can supply. Do NOT mark something as needing review because the
   evidence is silent on it while the client-supplied facts state it.
3. Draft ONLY this section as clean proposal prose (no markdown headings — the heading is added by the document builder). Be concise and specific; no filler.
4. Prefer specific technical content from the evidence (architectures, connectors, workflows, timelines, volumetrics) over generic methodology boilerplate.
5. This is a DRAFT for human review, never client-ready.

{evidence}

OUTPUT DISCIPLINE (mandatory):
- Write the section as it will appear in the client proposal. NEVER narrate your
  own research process. Do not write "the retrieved evidence", "the corpus",
  "Note on Evidence Applicability", or any commentary about what your sources do
  or do not contain. Sources are acknowledged ONLY through inline [N] markers.
- Where a fact is genuinely unavailable from BOTH sources, insert the review
  marker once and move on. Do not explain at length what is missing or why.
- Never write "not in evidence", "not specified in evidence" or "not addressed
  in the available evidence". The client does not know what our evidence corpus
  is, and the phrase is usually wrong anyway: the fact is normally sitting in
  the client-supplied facts above.
- Never emit reasoning, deliberation or <think> blocks. Output only the section.
- Stop when the section is complete. Do not pad to fill space, and never repeat a
  word or phrase to extend length.
"""


def _vendor_scope_clause(context: dict) -> str:
    """Explicit vendor-ownership framing for a multi-vendor engagement.

    Splitting headings and retrieval per vendor (render_subsections,
    _fanout_queries) is necessary but NOT sufficient: a section like
    "Proposed Deployment Architecture" is deliberately drafted ONCE, showing
    how the whole solution fits together — and without this clause, the model
    has no signal that it must attribute components correctly rather than
    blending two vendors' capabilities into one undifferentiated description.
    This is injected into every section's system prompt so the distinction
    holds even in sections that were never split.
    """
    vmap = (context.get("discovery_answers") or {}).get("vendor_scope_map") or {}
    if len(vmap) >= 2:
        lines = "; ".join(f"{v} owns {s}" for v, s in vmap.items())
    elif len(context.get("iam_vendors") or []) > 1:
        # ESNAD had no vendor_scope_map, so this clause never fired and "Why
        # Ping Identity" sold Saviynt's IGA features. The vendor answer itself
        # ("Ping Identity for Access Management and CIAM, Saviynt for IGA and
        # PAM") states the split; use it.
        lines = context.get("iam_vendor") or ""
    else:
        return ""
    return (f"\n\nMULTI-VENDOR ENGAGEMENT — {lines}. When describing the "
           f"architecture or capabilities, attribute each part to the "
           f"correct vendor explicitly. Do not blend the two vendors' "
           f"capabilities into one undifferentiated description, and do not "
           f"describe one vendor as covering scope that belongs to the other.")


def _subsection_vendor(sub_title: str, context: dict) -> Optional[str]:
    """The one vendor a subsection heading names, in a multi-vendor proposal."""
    vendors = [v for v in (context.get("iam_vendors") or []) if v]
    if len(vendors) < 2:
        return None
    named = [v for v in vendors if v.lower() in (sub_title or "").lower()]
    return named[0] if len(named) == 1 else None


def _engagement_facts_clause(context: dict) -> str:
    """Facts every section must agree on, stated once in every system prompt.

    Per-section routing let each section improvise them. ESNAD (envs "Dev,
    Test, and Prod", SaaS) came back with four different environment lists and
    a Tranche 1 milestone installing PingAM/PingDS for a vendor-hosted tenant.
    """
    answers = context.get("discovery_answers") or {}
    out = ""
    envs = " ".join(str(answers.get("envs") or "").split())
    if envs and envs.lower() not in ("skip", "none", "n/a", "-"):
        out += (f"\n\nENVIRONMENTS: exactly {envs}. Do not present any other "
                f"environment (DR, UAT, QA, Staging, Sandbox) as one that is "
                f"built or provisioned in this engagement.")
    if context.get("is_saas"):
        out += ("\n\nDEPLOYMENT: the platform is vendor-hosted SaaS. The vendor "
                "provisions and operates the tenants. Never describe installing, "
                "sizing or patching the platform's own servers, and never name "
                "self-managed server components as deployed. The client provides "
                "only connectivity and any integration agents or gateways.")
        for vendor in context.get("iam_vendors") or [context.get("iam_vendor") or ""]:
            for key, (saas, self_managed) in _SAAS_PRODUCTS.items():
                if key in (vendor or "").lower():
                    out += (f" {vendor}'s SaaS platform is {saas}; do not present "
                            f"{self_managed} as part of this solution.")
    pop = " ".join(str(answers.get("population_by_domain") or "").split())
    if pop and pop.lower() not in ("skip", "none", "n/a", "-"):
        # ESNAD 09-24: "10,000 users, including 5,000 workforce and 10,000
        # CIAM" -- a licensing total set against per-domain figures.
        out += (f"\n\nPOPULATION BY DOMAIN: {pop}. Quote these per-domain "
                f"figures; never state a single user total smaller than their sum.")
    if out:
        # ESNAD 09-25 repeated "5,000 WIAM users" 57 times and "Dev, Test, and
        # Prod" 63 times (IV's own proposal: 4 and 3), because these facts sit in
        # every section's prompt and every subsection restated them.
        out += ("\n\nUSING THESE FACTS: they keep sections consistent; they are "
                "not content to repeat. State a population, environment list or "
                "deployment model only where this subsection is about it, at most "
                "once (table cells included), and never as an opener or closer. "
                "Elsewhere refer to 'the "
                "workforce', 'each environment', 'the SaaS tenants'.")
    if answers:
        out += ("\n\nOTHER ORGANISATIONS: name no organisation other than the "
                "client, the proposed vendors and Inspirit Vision. Past clients "
                "appear only in the Similar Experience section, from its list.")
        out += ("\n\nSYSTEMS: name only systems, directories and products that the "
                "client-supplied facts or requirements name. Never add one they do "
                "not (for example a cloud directory the client never mentioned).")
    return out


# --- Delivered engagements (Similar Experience) ------------------------------
# ESNAD 09-27 wrote "Inspirit Vision partnered to deploy Saviynt EIC" for a
# client whose only corpus record was a proposal. The rule, from Imran
# (2026-09-27): claim delivery only for WON engagements, and NAME a client only
# when IV already cites that client as a reference in its own past proposals;
# describe every other client without its name.

_SHORT_ALIAS = 5  # aliases this short (ANB, SNB, STC) must match case-exactly
# Products whose delivery counts for the acquirer's platform (IV's own case
# studies present its ForgeRock projects as Ping Identity implementations).
_ACQUIRED_BY = {"forgerock": "Ping Identity"}


def _client_aliases(name: str) -> list[str]:
    """'Arab National Bank (ANB)' -> ['Arab National Bank (ANB)',
    'Arab National Bank', 'ANB']; 'Brunei Shell / DMOS' -> both halves."""
    name = " ".join((name or "").split())
    out = [name]
    m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", name)
    if m:
        out += [m.group(1), m.group(2)]
    for part in re.split(r"\s*/\s*", name):
        if part and part not in out:
            out.append(part)
    return [a for a in dict.fromkeys(out) if len(a) >= 3]


def _mentions_client(text: str, alias: str) -> bool:
    flags = 0 if len(alias) <= _SHORT_ALIAS else re.I
    return re.search(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", text, flags) is not None


def build_reference_book(proposals: list[dict], citation_chunks: list[dict]) -> str:
    """The WON ENGAGEMENTS block for Similar Experience, or "".

    proposals: corpus rows (id, client_name, iam_vendor, proposal_type, year,
    industry, country, outcome). citation_chunks: (proposal_id, text) from the
    similar-experience and company-profile sections of IV's proposals. A client
    is nameable when a proposal addressed to a DIFFERENT client mentions it."""
    by_id = {p.get("id"): p for p in proposals}
    won: dict[str, dict] = {}
    for p in proposals:
        client = (p.get("client_name") or "").strip()
        vendor = (p.get("iam_vendor") or "").strip()
        if not client or (p.get("outcome") or "").lower() != "won" \
                or vendor.lower() in ("", "other", "none"):
            continue
        e = won.setdefault(client, {"vendors": [], "types": set(), "years": set(),
                                    "industries": [], "countries": []})
        # Rows for one client disagree (NWC: 3 rows with no country), so the
        # most common non-empty value wins, not whichever row came first.
        if p.get("industry"):
            e["industries"].append(p["industry"])
        if p.get("country"):
            e["countries"].append(p["country"])
        for v in re.split(r"\s*\+\s*|\s*,\s*", vendor):
            if v and v not in e["vendors"]:
                e["vendors"].append(v)
        if p.get("proposal_type"):
            e["types"].add(p["proposal_type"])
        if p.get("year"):
            e["years"].add(int(p["year"]))
    if not won:
        return ""
    lines = []
    for client in sorted(won):
        e = won[client]
        aliases = _client_aliases(client)
        cited = any(
            (by_id.get(c.get("proposal_id")) or {}).get("client_name") != client
            and any(_mentions_client(c.get("text") or "", a) for a in aliases)
            for c in citation_chunks)
        def _mode(vals):
            return max(set(vals), key=vals.count) if vals else None
        ind = (_mode(e["industries"]) or "").strip().lower()
        country = _mode(e["countries"])
        ind = "" if ind in ("other", "unknown", "none", "n/a") else ind
        noun = f"{ind} organisation" if ind else "organisation"
        art = "an" if noun[0] in "aeiou" else "a"
        who = (f"{client} (named)" if cited else
               f"{art} {noun} in {country or 'the region'} (do not name)")
        years = f", {max(e['years'])}" if e["years"] else ""
        vendors = [f"{v} (now part of {_ACQUIRED_BY[v.lower()]})"
                   if v.lower() in _ACQUIRED_BY else v for v in e["vendors"]]
        lines.append(f"- {who}: {', '.join(vendors)}; "
                     f"{', '.join(sorted(e['types'])) or 'engagement'}{years}")
    return (f"{REFERENCE_LIST} -- proposals IV won, the ONLY "
            "engagements you may mention:\n" + "\n".join(lines) + "\n"
            "RULES: this list records that IV was SELECTED for the work, not that "
            "the work was completed. Write \"selected by\" or \"engaged by\"; never "
            "say delivered, completed, implemented, deployed or live. Name a client "
            "only where marked (named); for every other entry use exactly the "
            "description given, never a name. Mention each entry at most once. A "
            "product listed as 'now part of' a vendor counts as that vendor's "
            "engagement. State only the product and engagement type listed -- no "
            "scope, figures, outcomes or claims about referenceability the list "
            "does not give. Where a proposed vendor has no entry here, describe "
            "IV's partner status and certified resources for it, never a past "
            "engagement.")


# --- Client-facing compliance matrix -----------------------------------------
# The corpus grading (covered/partial/missing) says whether IV's PAST proposals
# show a capability. ESNAD 09-27 printed it as the client's answer: 30 of 56
# requirements "Partial" in the bid itself. The client document now states the
# response and WHERE this proposal addresses each requirement; the grading goes
# to the bid team in chat.
_REQ_STOP = set("""the and for with that this from into shall must will should
able support supports provide provides including include such other their
solution system systems platform based using within across all any each""".split())


def _req_tokens(text: str) -> set:
    """Words of 3+ letters, split on '/' and '-' ('joiner/mover/leaver' and
    'Joiner-Mover-Leaver' agree), with a light plural/-ing/-ed stem."""
    out = set()
    for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower()):
        if w in _REQ_STOP:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if len(w) > 5 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.add(w)
    return out


def requirement_references(entries, sections: list[dict]) -> dict:
    """requirement_id -> 'Section > Subsection' of THIS proposal that best
    addresses it, or None. Keyword overlap, subsection title counted twice."""
    targets = []
    for sec in sections:
        if sec.get("id") == COMPLIANCE_SECTION_ID:
            continue
        for sub in sec.get("subsections") or [{"title": "", "content": sec.get("content", "")}]:
            # Each capability heading the model wrote inside a subsection is its
            # own, more precise target ("Joiner-Mover-Leaver Lifecycle Automation").
            for chunk in re.split(r"(?m)^(?=#{1,6}\s)", sub.get("content") or ""):
                head = re.match(r"\s*#{1,6}\s+(.*)", chunk)
                inner = head.group(1).strip() if head else ""
                # A lead-in sentence set as a heading is not a reference.
                shown = inner if inner and len(inner.split()) <= 8 and not inner.endswith(":") else ""
                label = " > ".join(t for t in (sec.get("title"), sub.get("title"), shown) if t)
                targets.append((label, _req_tokens(f"{sub.get('title')} {inner}"),
                                _req_tokens(chunk)))
    out = {}
    for e in entries:
        req = _req_tokens(getattr(e, "requirement_text", "") or "")
        best, best_score = None, 0.0
        for label, title_t, body_t in targets:
            if not req:
                break
            score = (2 * len(req & title_t) + len(req & body_t)) / (2 * len(req))
            if score > best_score:
                best, best_score = label, score
        out[getattr(e, "requirement_id", "")] = best if best_score >= 0.3 else None
    return out


# What the client matrix may say (audit 2026-10-12, finding 1). The client never
# sees the corpus grading ("Partial", "Missing"), but the document must not
# assert more than is known either. Only a requirement with full IV precedent
# AND a section of this proposal behind it is stated as plainly compliant.
# "Compliant - confirmed during Discovery" claimed a confirmation nobody had
# recorded; a keyword-matched reference suggests where to look, it proves nothing.
RESPONSE_COMPLIANT = "Compliant"
RESPONSE_SUBJECT = "Compliant, subject to confirmation during Discovery"
RESPONSE_TBC = "To be confirmed"


def render_client_matrix(entries, refs: dict) -> tuple[str, list[str]]:
    """(markdown, requirement ids the bid team must confirm before submission).

    covered + a section reference            -> Compliant
    partial or missing + a section reference -> Compliant, subject to confirmation ...
    not judged (needs-human), whatever it references -> To be confirmed
    no section addresses it                  -> To be confirmed
    Every row that is not plainly Compliant is on the to-confirm list.

    needs-human means nobody assessed the requirement: the classifier declined,
    failed, or the row was past the cost cap. A keyword-matched section
    reference used to be enough to print "Compliant, subject to ..." on those
    (audit F08). `missing` with a reference keeps the conditional wording for
    now: the status grades IV's past-proposal precedent, not capability, and
    how many rows that is must be seen on a run first (matrix_breakdown)."""
    rows = ["Compliance Matrix", "",
            "| Req | Requirement | Response | Proposal Reference |", "|---|---|---|---|"]
    to_confirm = []
    for e in entries:
        rid = getattr(e, "requirement_id", "")
        ref = refs.get(rid)
        if not ref or getattr(e, "status", "") == "needs-human":
            response = RESPONSE_TBC
        elif getattr(e, "status", "") == "covered":
            response = RESPONSE_COMPLIANT
        else:
            response = RESPONSE_SUBJECT
        if response != RESPONSE_COMPLIANT:
            to_confirm.append(rid)
        text = (getattr(e, "requirement_text", "") or "").replace("|", "/").replace("\n", " ").strip()
        rows.append(f"| {rid} | {text} | {response} | {ref or '-'} |")
    return "\n".join(rows), to_confirm


def matrix_breakdown(entries, refs: dict) -> dict:
    """{status: {"rows": n, "with_reference": n}} for a compliance matrix: what
    the client-facing wording rests on. For the bid team, never the document."""
    out: dict[str, dict[str, int]] = {}
    for e in entries:
        row = out.setdefault(getattr(e, "status", "") or "unknown",
                             {"rows": 0, "with_reference": 0})
        row["rows"] += 1
        row["with_reference"] += 1 if refs.get(getattr(e, "requirement_id", "")) else 0
    return out


# Vendor -> (its SaaS platform, its self-managed products). ESNAD 09-24 named
# PingFederate, PingDirectory and PingAccess as components of a PingOne
# Advanced Identity Cloud tenant; IV's own proposal names AIC and PingGateway.
_SAAS_PRODUCTS = {
    "ping": ("PingOne Advanced Identity Cloud (with PingGateway where an edge "
             "gateway is needed)",
             "PingFederate, PingDirectory, PingAccess, PingAM, PingDS or PingIDM"),
    "sailpoint": ("SailPoint Identity Security Cloud", "IdentityIQ"),
}


def _vendor_clause(iam_vendor: Optional[str]) -> str:
    return f" using {iam_vendor}" if iam_vendor else ""


# Drives which language the four sizing-table subsections use, and which
# product names the solution stack shows. `deployment_model` is a free-text
# discovery answer ("SaaS (Software-as-a-Service) hosted within the Kingdom of
# Saudi Arabia"), not a dropdown value, so it is read loosely -- but a term
# only counts where it is AFFIRMED. "SaaS is not permitted; deploy in our
# private data centre" used to read as SaaS (audit F61: the word is there and
# no on-prem word is), and "SaaS, not on-prem" used to read as on-prem.
_SAAS_RE = r"(?:saas|software[- ]as[- ]a[- ]service)"
_SELF_HOSTED_RE = r"(?:on[- ]?prem\w*|hybrid)"
# Words that may sit between a term and the negation that governs it:
# "SaaS is strictly not permitted", "must not be SaaS", "cannot use SaaS".
_LINK = r"(?:\W+(?:is|are|was|be|been|being|will|would|should|must|can|could|may|a|an|" \
        r"any|the|use|using|used|allow\w*|permit\w*|accept\w*|consider\w*|deploy\w*|" \
        r"option|model|hosting|strictly|explicitly|currently)){0,3}"


# After the term only a negating PREDICATE counts: "SaaS is not permitted",
# "SaaS ruled out". "SaaS without on-premise components" and "SaaS with no
# on-prem footprint" describe the SaaS; reading their "without" and "no" as
# negating it turned a SaaS answer into on-prem (review, 2026-10-14). Before
# the term every negation counts: "no SaaS", "non-SaaS", "not SaaS".
_TRAILING_NEGATION = (r"\b(?:not|never|cannot|prohibit\w*|forbid\w*|forbade|disallow\w*|"
                      r"exclud\w*|reject\w*|ruled\s+out)\b|n['\u2019]t\b")


def _negated(term: str, clause: str) -> bool:
    neg = scope_filter.NEGATION_RE.pattern
    return bool(re.search(rf"(?:{neg}){_LINK}\W*{term}|{term}{_LINK}\W+(?:{_TRAILING_NEGATION})",
                          clause, re.I))


def _looks_like_saas(deployment_model: Optional[str]) -> bool:
    text = (deployment_model or "").lower()
    if not text:
        return False
    saas = False
    for clause in re.split(r"[.;,\n]|\bbut\b", text):
        # A hybrid or on-prem answer that mentions SaaS in passing is not a
        # SaaS deployment -- unless that is the thing being ruled out.
        if re.search(rf"\b{_SELF_HOSTED_RE}", clause) and not _negated(_SELF_HOSTED_RE, clause):
            return False
        if re.search(rf"\b{_SAAS_RE}", clause):
            if _negated(_SAAS_RE, clause):
                return False
            saas = True
    return saas



# Hard ceiling on any single draft call's token budget. Pass 3 depth tiers vary
# the budget DOWN for leaner tiers but must never raise a call above this — depth
# comes from more (fanned-out) calls, not from one runaway call.
# Lowered from 3500 after the Amlak run: the cap was raised to chase page count,
# but the generated proposal came out 38% LONGER than the human original while
# padding with degenerate synonym chains. Length was never the gap — fidelity
# was. 2500 leaves ample room for a substantive subsection.
MAX_DRAFT_TOKENS = 2500
DRAFT_REASONING_EFFORT = os.environ.get("SHILPI_DRAFT_REASONING_EFFORT", "low").strip()
# Prompt caching. Every subsection of a section re-sends the same system prompt
# (rules + ~24 evidence chunks + product docs + facts, 20-32k tokens on
# Sonnet). The OpenRouter log for ESNAD 09-24 showed 0 cached tokens and 73% of
# the run's $6.15 spent re-reading those identical blocks. Marked cacheable,
# repeats bill at ~10% (Anthropic) / ~25% (Gemini). Sent as a text part with
# cache_control; dropped with the other optional params on a 400.
# OpenRouter response caching: a request byte-identical to one made within the
# TTL is answered from cache at zero cost. Test runs repeat most of a run
# unchanged (every compliance call, every RFP page, every section whose prompt a
# fix did not touch), and each ESNAD run cost $2-6. Only successful responses
# are reused, and every deliberate retry changes the request body (length retry
# raises max_tokens, degenerate re-draft appends an instruction, blank retry
# appends a newline). SHILPI_RESPONSE_CACHE_TTL=0 disables.
RESPONSE_CACHE_TTL = int(os.environ.get("SHILPI_RESPONSE_CACHE_TTL", "86400"))


def openrouter_cache_headers() -> dict:
    if RESPONSE_CACHE_TTL <= 0:
        return {}
    return {"X-OpenRouter-Cache": "true",
            "X-OpenRouter-Cache-TTL": str(min(RESPONSE_CACHE_TTL, 86400))}


DRAFT_PROMPT_CACHE = os.environ.get("SHILPI_DRAFT_PROMPT_CACHE", "1").strip() not in ("0", "", "false")


def _cacheable(text: str) -> list[dict]:
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


# ---------------------------------------------------------------------------
# Discovery context routing
#
# The 22-area discovery interview captures sizing, timeline, integrations,
# commercials, NFRs and delivery model — and until this map existed NONE of it
# reached the drafting engine. ``generate_proposal`` accepted only ``rfp_text``,
# which app.py filled with ``business_objectives``, so 21 of 22 areas were
# collected, stored in Supabase and then discarded. The generated Amlak proposal
# complained that it lacked "Amlak-specific volumetrics/timeline/pricing" — it
# was telling the truth.
#
# Routing per section rather than passing the whole answers dict everywhere is
# deliberate: a single blob would bloat every call and invite every section to
# restate everything. Sizing belongs to architecture, milestones to commercials,
# pain points to the executive summary.
# Section id -> the discovery fields that section needs.
#
# THIS MAP MUST TRACK proposal_templates.py AND intake_template.py. It silently
# stopped doing both:
#
#   * Keys were the OLD section ids (client_context, solution_architecture,
#     technical_approach, implementation_methodology, integration_points). When
#     the template was rebuilt to IV's house structure every section was
#     renamed, and this map was not. Result: 11 of 12 implementation sections
#     received NO discovery answers at all -- only executive_summary matched.
#   * Field names were invented rather than taken from the intake schema
#     ("engagement_duration" for `duration`, "environments" for `envs`,
#     "sod_required" for `sod`, "hrms_authoritative_source" for
#     `integration_hrms`), so several that did match resolved to nothing.
#
# The visible symptom was 42 [SME REVIEW] markers in run 9, including on UAT
# sizing, connectors, hypercare and the support model -- all of which the
# consultant HAD supplied. The model was not hedging; it genuinely had not been
# told. A sizing table drafted without sizing inputs can only be generic, so
# this also explains part of the table thinness against IV's original.
#
# `test_document_engine.py` asserts every section id in every template appears
# here, and that every field name exists in the intake schema. Those two tests
# are the reason this cannot drift again.
_SECTION_DISCOVERY_FIELDS: dict[str, tuple[str, ...]] = {
    # --- implementation template ---
    "executive_summary": (
        "business_objectives", "pain_points", "differentiators", "decision_criteria",
        "duration", "app_count", "user_count", "audience", "population_by_domain",
        # IV's executive summary names the systems, protocols, regulations,
        # residency, delivery model and support model; it could not without these.
        "target_integrations", "idp_sso", "regulations", "data_residency",
        "deployment_model", "delivery_phases", "support_model", "identity_types",
        "in_scope",
    ),
    "company_profile": (
        "partner_positioning", "vendor_partner_positioning",
        "partner_tier_certifications",
    ),
    "similar_experience": (
        "case_studies_to_highlight", "case_studies_include", "case_studies_exclude",
        "similar_projects", "partner_positioning", "partner_tier_certifications",
    ),
    "scope_understanding": (
        "business_objectives", "in_scope", "out_of_scope", "current_state",
        "existing_iam_platform", "pain_points", "app_count", "user_count",
        "identity_types", "apps_to_onboard", "population_by_domain",
        # Domain-by-domain scope restates integrations, compliance, testing,
        # training and support in the client's own terms.
        "target_integrations", "idp_sso", "regulations", "data_residency",
        "delivery_phases", "envs", "support_model", "training", "kt",
        "is_migration", "existing_iam_platform",
    ),
    "solution_overview": (
        "sod", "access_review_cadence", "target_integrations", "audit",
        "monitoring", "identity_types", "differentiators", "regulations",
        "population_by_domain",
        # The Extension Modules subsection decides what is IN scope; without
        # the scope it called JIT an excluded add-on while the SOW required it.
        "in_scope",
    ),
    "proposed_solution": (
        "deployment_model", "hardware_sizing_inputs", "cluster_topology",
        "ha_dr_requirements", "rto_rpo", "security_architecture_needs",
        "regions", "network_zones", "envs", "availability", "scalability",
        "performance", "target_integrations", "integration_hrms", "ad_exchange",
        "idp_sso", "apps_to_onboard", "app_count", "user_count", "directories",
        "current_hrms", "current_idp", "source_of_truth", "population_by_domain",
    ),
    "implementation_approach": (
        "delivery_phases", "delivery_milestones", "governance", "raci",
        "client_responsibilities", "dependencies", "assumptions",
    ),
    "project_timeline": (
        "duration", "timeline_milestones", "go_live_date", "delivery_phases",
        "delivery_milestones", "app_count",
    ),
    "assumptions_responsibilities": (
        "assumptions", "dependencies", "client_responsibilities", "out_of_scope",
        "rto_rpo", "versions",
    ),
    "knowledge_transfer": ("training", "kt"),
    "post_production_support": (
        "hypercare", "support_model", "post_sla", "postgolive_reporting_cadence",
    ),
    "commercial": (
        "license_included", "pricing_model", "payment_milestones", "taxes",
        "travel", "support_terms", "validity_period", "currency",
        # ESNAD 09-24's BoQ had [SME REVIEW] quantities beside a known
        # population and listed modules the proposal excludes.
        "in_scope", "population_by_domain", "delivery_phases",
        "licence_line_items",
    ),
    "compliance_matrix": ("rfp_text", "regulations", "certifications"),

    # --- migration template ---
    "current_state": (
        "current_state", "existing_iam_platform", "versions", "directories",
        "current_hrms", "current_idp", "source_of_truth", "pain_points",
        "is_migration", "tenants", "apps_to_onboard", "app_count",
        "population_by_domain",
    ),
    "target_state": (
        "deployment_model", "hardware_sizing_inputs", "cluster_topology",
        "ha_dr_requirements", "envs", "regions", "network_zones",
        "security_architecture_needs", "availability", "scalability",
    ),
    "migration_strategy": (
        "is_migration", "existing_iam_platform", "apps_to_onboard", "app_count",
        "identity_types", "out_of_scope", "delivery_phases", "data_residency",
        "population_by_domain",
    ),
    "rollback_risk": (
        "ha_dr_requirements", "rto_rpo", "availability", "dependencies",
        "assumptions", "envs",
    ),
    "decommissioning": (
        "existing_iam_platform", "data_retention", "out_of_scope",
        "license_included", "support_terms",
    ),

    # --- mss template ---
    "operating_model": (
        "support_model", "postgolive_reporting_cadence", "governance", "raci",
        "training", "kt", "hypercare",
    ),
    "service_model": (
        "support_model", "post_sla", "hypercare", "training", "kt",
        "delivery_phases", "envs",
    ),
    "sla_coverage": ("post_sla", "support_model", "availability", "monitoring", "audit"),
    "escalation_incident": ("post_sla", "support_model", "monitoring", "governance"),
    "assumptions_open_questions": (
        "assumptions", "dependencies", "client_responsibilities", "out_of_scope",
        "rto_rpo", "versions",
    ),
}

# Fields every section benefits from knowing.
_UNIVERSAL_DISCOVERY_FIELDS: tuple[str, ...] = (
    "client_name", "iam_vendor", "proposal_type", "industry", "country",
)

# Never route these into drafting prompts: control fields, or content that must
# not be paraphrased into client-facing prose.
_EXCLUDED_DISCOVERY_FIELDS = frozenset({
    "proposal_depth", "diagram_count", "required_diagram_types", "rfp_text",
    "_diagram_plan", "case_studies_exclude", "logo_url", "client_logo",
})


def _humanise_field(key: str) -> str:
    return key.replace("_", " ").strip().title()


def discovery_context_for(section_id: str, answers: Optional[dict],
                          limit: int = 3500) -> str:
    """Render the discovery answers relevant to one section.

    Returns an empty string when nothing relevant was captured, so the prompt is
    unchanged for callers that have no intake data (the REST path).
    """
    if not answers:
        return ""
    wanted = _SECTION_DISCOVERY_FIELDS.get(section_id, ())
    keys = list(_UNIVERSAL_DISCOVERY_FIELDS) + [k for k in wanted
                                                if k not in _UNIVERSAL_DISCOVERY_FIELDS]
    lines: list[str] = []
    for key in keys:
        if key in _EXCLUDED_DISCOVERY_FIELDS:
            continue
        val = answers.get(key)
        if val is None:
            continue
        text = " ".join(str(val).split())
        if not text or text.lower() in ("skip", "none", "n/a", "na", "-"):
            continue
        # A template placeholder pasted back verbatim ("<IV's real tier/cert
        # counts, or skip>") reached Company Profile in three ESNAD sessions.
        if text.startswith("<") and text.endswith(">"):
            continue
        lines.append(f"- {_humanise_field(key)}: {text}")
    if not lines:
        return ""
    block = "\n".join(lines)
    if len(block) > limit:
        block = block[:limit].rsplit("\n", 1)[0]
    return block


def _draft_payload(model: str, system_prompt: str, user_prompt: str,
                   include_frequency_penalty: bool = True,
                   max_tokens: int = MAX_DRAFT_TOKENS,
                   include_extras: Optional[bool] = None) -> dict:
    # Prompt caching and the reasoning cap follow frequency_penalty unless the
    # caller says otherwise (the 400 path keeps them while dropping the penalty).
    extras = include_frequency_penalty if include_extras is None else include_extras
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _cacheable(system_prompt)
             if extras and DRAFT_PROMPT_CACHE else system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "temperature": 0.4,
        # Cap runaway generation (same repetition-spiral risk as the compliance
        # classifier). LOW frequency_penalty preserves grounded citation/vendor terms.
        # Clamp to the hard ceiling so a bad depth config can never inflate a call.
        "max_tokens": min(int(max_tokens), MAX_DRAFT_TOKENS),
    }
    if extras and DRAFT_REASONING_EFFORT:
        # Hidden reasoning counts against max_tokens: Gemini 3.8 Flash spent
        # ~860 of every 900 tokens on it in ESNAD 09-24, so 19 drafts needed
        # a retry. Dropped with the other optional params on a 400.
        payload["reasoning"] = {"effort": DRAFT_REASONING_EFFORT}
    if include_frequency_penalty:
        # Raised from 0.2: the low value was chosen to preserve repeated vendor
        # and product terms, but it also let an exhausted model pad with synonym
        # chains. 0.45 still tolerates legitimate repetition of "SailPoint" or
        # "IdentityIQ" while discouraging runaway filler.
        payload["frequency_penalty"] = 0.45
    return payload


# What one proposal costs (Sprint 3.4). A ledger opened for a request collects
# the usage of every OpenRouter call awaited inside it, including calls in
# gathered tasks (contextvars copy into them). OpenRouter reports the charged
# cost when a request carries usage={"include": true}.
# ponytail: covers the generation request only; the chat turns before it (RFP
# reading, diagram specs) are separate requests and are not summed per deal yet.
_LEDGER: contextvars.ContextVar = contextvars.ContextVar("llm_ledger", default=None)
USAGE_ACCOUNTING = {"usage": {"include": True}}


def start_ledger() -> dict:
    # cost_usd is the sum over calls that reported a cost: None until one does,
    # and a lower bound whenever unpriced_calls > 0. An unreported cost used to
    # be added as 0, so an unknown spend read as a free run.
    ledger = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
              "cost_usd": None, "unpriced_calls": 0}
    _LEDGER.set(ledger)
    return ledger


def cost_text(ledger: dict) -> str:
    """The ledger's cost for a log line or a message: never "$0" for unknown."""
    cost, unpriced = ledger.get("cost_usd"), ledger.get("unpriced_calls") or 0
    if cost is None:
        return "unknown" if ledger.get("calls") else "$0.0000"
    return f"${cost:.4f}" + (f" (+{unpriced} unpriced calls)" if unpriced else "")


def record_usage(usage) -> None:
    """Add one response's usage (a dict, or an OpenAI usage object) to the open ledger."""
    ledger = _LEDGER.get()
    if ledger is None or not usage:
        return
    if not isinstance(usage, dict):
        usage = usage.model_dump() if hasattr(usage, "model_dump") else {}
    ledger["calls"] += 1
    ledger["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
    ledger["completion_tokens"] += int(usage.get("completion_tokens") or 0)
    if usage.get("cost") is None:
        ledger["unpriced_calls"] += 1
    else:
        ledger["cost_usd"] = round((ledger["cost_usd"] or 0.0) + float(usage["cost"]), 6)


async def _post_once(client: httpx.AsyncClient, payload: dict) -> dict:
    resp = await client.post(
        f"{OPENROUTER_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            **openrouter_cache_headers(),
        },
        json={**payload, **USAGE_ACCOUNTING},
        timeout=180,
    )
    resp.raise_for_status()
    body = resp.json()
    choice = body["choices"][0]
    choice["_usage"] = body.get("usage") or {}
    record_usage(choice["_usage"])
    return choice


def _trim_incomplete_tail(text: str) -> str:
    """Drop the half-written last line of a response cut off at max_tokens.

    A table keeps only rows that close with '|'; prose ends at its last full
    sentence. A clean cut is a shorter section, a ragged one is a visible defect.
    """
    # ONE line at most: a cascade wiped whole bullet lists ("- Access
    # reviews" has no terminal punctuation). Colons are not sentence ends.
    lines = (text or "").rstrip().split("\n")
    last = lines[-1].rstrip() if lines else ""
    if last.lstrip().startswith("|"):
        if not last.endswith("|"):
            lines.pop()
    elif last:
        m = list(re.finditer(r"[.!?](?=\s|$)", last))
        if m:
            lines[-1] = last[: m[-1].end()]
        else:
            lines.pop()
    return "\n".join(lines).rstrip()


_LENGTH_RETRY_MIN_TOKENS = int(os.environ.get("SHILPI_LENGTH_RETRY_MIN_TOKENS", "6000"))


def _usage_note(choice: dict) -> str:
    """Visible vs reasoning tokens, so a cut-off log says which one ran out."""
    u = choice.get("_usage") or {}
    reasoning = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
    return f"completion={u.get('completion_tokens')} reasoning={reasoning}"


async def _post_draft(client: httpx.AsyncClient, payload: dict) -> str:
    """One draft call. A response cut off at max_tokens is retried once with
    double the budget, then trimmed to its last complete sentence or row.

    finish_reason used to be ignored, so ESNAD 09-23 shipped six subsections
    ending mid-word ("is available but N", RAID row "have not |") although the
    visible text was a fraction of the budget -- the primary model's reasoning
    tokens count against max_tokens.
    """
    choice = await _post_once(client, payload)
    if choice.get("finish_reason") != "length":
        return choice["message"]["content"]
    budget = int(payload.get("max_tokens") or MAX_DRAFT_TOKENS)
    # At least 6000: ESNAD 09-24 was still cut off at 2x (1800 for prose,
    # 5000 for Tranche 3, which then shipped 3 rows). Billing is on tokens
    # actually generated, so a higher ceiling costs nothing unless used.
    retry_budget = max(2 * budget, _LENGTH_RETRY_MIN_TOKENS)
    log.warning("draft cut off at max_tokens=%s (model=%s, %s); retrying with %s",
                budget, payload.get("model"), _usage_note(choice), retry_budget)
    # Deliberately above MAX_DRAFT_TOKENS for this one retry: the ceiling
    # guards against padding, and this call was cut by hidden reasoning.
    choice = await _post_once(client, {**payload, "max_tokens": retry_budget})
    content = choice["message"]["content"]
    if choice.get("finish_reason") == "length":
        log.warning("draft still cut off after retry (%s); trimming the incomplete tail "
                    "and marking it for review", _usage_note(choice))
        # The trim makes it read as finished, which is exactly why it must be
        # marked: whole obligations can be missing after a clean full stop.
        content = _mark_cut_off(_trim_incomplete_tail(content))
    return content


def _mark_cut_off(text: str) -> str:
    """End a cut-off draft with the review marker, once. The blank line keeps
    it out of a table whose last row was the thing cut."""
    text = (text or "").rstrip()
    return text if text.endswith(SME_REVIEW_MARKER) else f"{text}\n\n{SME_REVIEW_MARKER}"


async def draft_with_openrouter(
    client: httpx.AsyncClient,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = MAX_DRAFT_TOKENS,
) -> str:
    """Isolated OpenRouter chat call with primary->fallback. Monkeypatched by the smoke test.

    Tries PRIMARY_LLM_MODEL, then FALLBACK_LLM_MODEL on HTTP/network/timeout
    error. Defensive: a 400 caused by an unsupported param (frequency_penalty)
    triggers a same-model retry WITHOUT frequency_penalty before falling back.

    ``max_tokens`` is the per-call budget from the active depth tier; it is
    clamped to MAX_DRAFT_TOKENS inside ``_draft_payload``.
    """
    last_exc: Exception | None = None
    # 402 in-flight budget exhaustion is RETRYABLE and account-level, so falling
    # back to another model does not help -- both run on the same account.
    # Run 11 lost sixteen subsections to this: four whole sections (Timeline,
    # Key Assumptions, Knowledge Transfer, Commercial) drafted empty, ten tables
    # missing, and the document shipped anyway. OpenRouter returns
    # `Retry-After` and says explicitly to retry once in-flight requests settle.
    for attempt in range(_BUDGET_RETRIES + 1):
        try:
            return await _draft_across_models(client, system_prompt, user_prompt,
                                              max_tokens=max_tokens)
        except httpx.HTTPStatusError as e:
            last_exc = e
            if not _is_retryable_budget_error(e) or attempt == _BUDGET_RETRIES:
                raise
            wait = _retry_after_seconds(e, default=_BUDGET_BACKOFF_S * (attempt + 1))
            log.warning("draft hit an account budget limit (attempt %d/%d); "
                        "waiting %ss before retrying", attempt + 1,
                        _BUDGET_RETRIES + 1, wait)
            await asyncio.sleep(wait)
    raise last_exc  # pragma: no cover


# Retries and backoff for account-level budget errors. Deliberately generous:
# the alternative is a silently truncated proposal, and OpenRouter's own hint
# says the in-flight budget frees up as requests settle.
_BUDGET_RETRIES = int(os.environ.get("SHILPI_BUDGET_RETRIES", "3"))
_BUDGET_BACKOFF_S = float(os.environ.get("SHILPI_BUDGET_BACKOFF_S", "45"))


def _is_retryable_budget_error(e: httpx.HTTPStatusError) -> bool:
    """A 402 caused by IN-FLIGHT budget, not by an empty account.

    An exhausted balance is not retryable and must fail fast; an in-flight
    ceiling clears on its own. OpenRouter distinguishes them in the body.
    """
    if e.response is None:
        return False
    # 429 is a rate limit and always clears; ESNAD 09-24 (Gemini) lost
    # "Connectors and Integrations" to one after the fallback hit it too.
    if e.response.status_code == 429:
        return True
    if e.response.status_code != 402:
        return False
    try:
        body = e.response.text.lower()
    except Exception:  # noqa: BLE001
        return False
    return ("in_flight" in body or "in-flight" in body
            or "retry after" in body or "settle" in body)


def _retry_after_seconds(e: httpx.HTTPStatusError, default: float) -> float:
    header = (e.response.headers.get("Retry-After") if e.response is not None else None)
    try:
        return max(float(header), 1.0) if header else default
    except (TypeError, ValueError):
        return default


async def _draft_across_models(client, system_prompt: str, user_prompt: str, *,
                               max_tokens: int) -> str:
    """Primary then fallback model. Model-level failures only."""
    last_exc: Exception | None = None
    for model in (PRIMARY_LLM_MODEL, FALLBACK_LLM_MODEL):
        try:
            content = await _post_draft(
                client, _draft_payload(model, system_prompt, user_prompt, max_tokens=max_tokens))
            log.info("OpenRouter draft model=%s", model)
            return content
        except httpx.HTTPStatusError as e:
            last_exc = e
            # A 400 may be an unsupported-param error (e.g. frequency_penalty).
            # Retry the SAME model once without it before falling back.
            if e.response is not None and e.response.status_code == 400:
                # Drop ONLY frequency_penalty first: dropping everything lost
                # prompt caching and the reasoning cap silently on any model
                # that rejects that one parameter. Plain request as last resort.
                for extras, note in ((True, "no frequency_penalty"),
                                     (False, "plain: no penalty, reasoning or cache_control")):
                    try:
                        payload = _draft_payload(model, system_prompt, user_prompt,
                                                 include_frequency_penalty=False,
                                                 max_tokens=max_tokens,
                                                 include_extras=extras)
                        content = await _post_draft(client, payload)
                        log.warning("OpenRouter draft model=%s (%s)", model, note)
                        return content
                    except (httpx.HTTPStatusError, httpx.RequestError) as e2:
                        last_exc = e2
            if model == FALLBACK_LLM_MODEL:
                raise
            log.warning("draft failed on primary %s (%s); falling back to %s",
                        model, e, FALLBACK_LLM_MODEL)
        except httpx.RequestError as e:
            last_exc = e
            if model == FALLBACK_LLM_MODEL:
                raise
            log.warning("draft failed on primary %s (%s); falling back to %s",
                        model, e, FALLBACK_LLM_MODEL)
    raise last_exc  # pragma: no cover


def _fanout_queries(section_spec: SectionSpec, context: dict, fanout: int) -> list[str]:
    """Build up to ``fanout`` distinct retrieval queries for a section.

    Query 0 is the section's base query. Extra queries append a subsection facet
    keyword so retrieval surfaces evidence for different aspects of the section.

    MULTI-VENDOR: when the section's query_template contains {{ iam_vendor }}
    and context["iam_vendors"] holds more than one name, the BASE query is
    rendered once per vendor instead of once with a combined string. A query
    like "Ping Identity and Saviynt solution overview" dilutes the vector
    search against both vendors' corpus content at once; two separate queries
    each retrieve cleanly against their own vendor's evidence, then get merged
    and deduped by _retrieve_fanout same as any other fanned-out query.

    Single-vendor proposals are unaffected: iam_vendors defaults to a
    one-element list and this loop runs once, identical to before.
    """
    vendors = context.get("iam_vendors") or (
        [context["iam_vendor"]] if context.get("iam_vendor") else [None])

    is_multi_vendor_query = ("iam_vendor" in section_spec.query_template
                             and len(vendors) > 1)
    if is_multi_vendor_query:
        bases = [section_spec.render_query({**context, "iam_vendor": v})
                for v in vendors]
    else:
        bases = [section_spec.render_query(context)]

    # One base query per vendor is a FLOOR, not a target the fanout budget can
    # shrink below. A fanout of 1 with two vendors must still return both base
    # queries — retrieval for a vendor that ends up with ZERO queries means
    # that vendor's corpus evidence never reaches the drafted section at all,
    # which is a worse failure than one fewer facet-appended query. This was
    # caught by testing fanout=1 directly: it silently dropped the second
    # vendor's base query entirely.
    if not is_multi_vendor_query and fanout <= 1:
        return bases[:1]
    if is_multi_vendor_query and fanout <= len(bases):
        return bases

    queries = list(bases)
    remaining = max(0, fanout - len(bases))
    # Facet queries cycle through vendors round-robin (vendor 0's facet 0,
    # vendor 1's facet 0, vendor 0's facet 1, ...) rather than all landing on
    # vendor 0, so extra fanout budget also gives BOTH vendors deeper coverage
    # instead of only the first one.
    for idx, (_title, facet) in enumerate(SUBSECTION_FACETS[:remaining]):
        base = bases[idx % len(bases)]
        queries.append(f"{base} — {facet}")
    return queries[:fanout]


async def _retrieve_fanout(
    client: httpx.AsyncClient,
    section_spec: SectionSpec,
    context: dict,
    *,
    embed_fn: EmbedFn,
    retrieve_fn: RetrieveFn,
    top_k: int,
    fanout: int,
    retrieve_product_fn=None,
) -> tuple[list[dict], list[dict]]:
    """Run fanned-out retrieval, then merge + dedupe chunks by text.

    Deduped set is sorted by similarity (desc) and capped so wider fan-out gives
    richer evidence without an unbounded evidence block.

    Returns (proposal_chunks, product_chunks). product_chunks is always []
    unless retrieve_product_fn is given AND this section's query_template
    names {{ iam_vendor }} -- the same signal _fanout_queries already uses to
    decide whether a section is vendor-specific, reused here rather than a
    second, separately-maintained list of "which sections want product depth".
    """
    seen: set[str] = set()
    merged: list[dict] = []
    for query in _fanout_queries(section_spec, context, fanout):
        try:
            embedding = await embed_fn(client, query)
            # Pass the proposal type through so retrieval reserves slots for
            # the same kind of engagement. Without it a migration section is
            # drafted from greenfield implementation proposals: measured at 1 of
            # 8 chunks from a migration proposal, against a 35.5% base rate.
            chunks = await retrieve_fn(
                client, embedding, query, k=top_k,
                proposal_type=context.get("proposal_type") or None,
                # Retrieval runs ONCE per section, before subsections are
                # drafted, so there is no subsection heading here. The fan-out
                # QUERY text is used instead: it is derived from the section's
                # subsections and carries their content words ("sizing", "RACI",
                # "why SailPoint"), which is what topic_for matches on. Coarser
                # than a true per-subsection retrieval, and cheap.
                section_topic=topic_for(section_spec.id, query))
        except Exception as e:  # fail soft: one failed query must not sink the section
            log.error("draft_section retrieval failed for %s: %s", section_spec.id, e)
            chunks = []
        for c in chunks:
            key = (c.get("chunk_text") or c.get("heading") or "")[:160]
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(c)
    merged.sort(key=lambda c: float(c.get("similarity") or 0.0), reverse=True)
    # Cap evidence to keep prompts bounded: base top_k, plus headroom per extra query.
    # Was top_k x fanout = 24 at full depth, and every subsection re-sends it.
    # 16 keeps the best-ranked evidence (merged is sorted by similarity).
    cap = min(top_k * max(1, fanout), SECTION_EVIDENCE_CAP)
    proposal_chunks = merged[:cap]

    product_chunks: list[dict] = []
    is_vendor_specific = "iam_vendor" in section_spec.query_template
    if retrieve_product_fn and is_vendor_specific:
        vendors = context.get("iam_vendors") or (
            [context["iam_vendor"]] if context.get("iam_vendor") else [])
        # ONE query per vendor, not the full fanout depth: the product corpus
        # is small and curated (unlike the proposal vault, which benefits
        # from many query angles to surface diverse past engagements), so one
        # good base query per vendor is enough to pull its top relevant
        # chunks. Keeps this cheap and keeps the two retrieval passes
        # independent -- a slow or broken product query must never affect
        # proposal-history retrieval, or vice versa.
        seen_product: set[str] = set()
        for v in vendors:
            if not v:
                continue
            try:
                pq = section_spec.render_query({**context, "iam_vendor": v})
                p_embedding = await embed_fn(client, pq)
                p_chunks = await retrieve_product_fn(client, p_embedding, vendor=v, k=top_k)
            except Exception as e:  # fail soft: product corpus is a bonus, never a blocker
                log.warning("product retrieval failed for %s / %s: %s",
                           section_spec.id, v, e)
                p_chunks = []
            for c in p_chunks or []:
                key = (c.get("chunk_text") or c.get("heading") or "")[:160]
                if key and key in seen_product:
                    continue
                if key:
                    seen_product.add(key)
                product_chunks.append({**c, "_for_vendor": v})
        product_chunks.sort(key=lambda c: float(c.get("similarity") or 0.0), reverse=True)
        # top_k PER vendor: one shared cap let the better-embedding vendor's
        # docs crowd the other out of its own "Solution Overview".
        product_chunks = product_chunks[:PRODUCT_EVIDENCE_PER_VENDOR
                                        * max(1, len([v for v in vendors if v]))]

    return proposal_chunks, product_chunks


# Reasoning models (GLM 5.2 among them) emit internal deliberation that must
# never reach a client document. A live Amlak proposal contained a raw </think>
# tag plus seven verbatim repetitions of the model's own musing about evidence
# quality. Nothing anywhere stripped these.
_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.S | re.I)
_STRAY_THINK_TAG_RE = re.compile(r"</?think\s*[^>]*>", re.I)

# Whole paragraphs where the model narrates its own retrieval instead of writing
# the proposal. These are answer content, not reasoning tokens, so the tag strip
# above does not catch them.
_META_PARAGRAPH_RE = re.compile(
    r"(?m)^[^\n]*\b("
    r"note on evidence applicability"
    r"|the retrieved evidence"
    r"|retrieved evidence (originates|comes|is drawn)"
    r"|based on the (retrieved )?(corpus|evidence base)"
    r"|the (corpus|evidence) (does not|doesn't) contain"
    r")\b[^\n]*$",
    re.I,
)


def strip_model_artifacts(text: str) -> str:
    """Remove reasoning blocks and retrieval meta-commentary from drafted text.

    Deliberately conservative: it removes whole lines that narrate the drafting
    process, and never touches ``[N]`` citation markers or SME-review markers,
    which are intentional signals for the human reviewer.
    """
    if not text:
        return text
    out = _THINK_BLOCK_RE.sub("", text)
    out = _STRAY_THINK_TAG_RE.sub("", out)
    out = _META_PARAGRAPH_RE.sub("", out)
    # Collapse the blank lines and orphaned rules those removals leave behind.
    out = re.sub(r"(?m)^\s*-{3,}\s*$\n(?=\s*$)", "", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def strip_degenerate_tail(text: str, run_threshold: int = 5) -> str:
    """Cut a draft at the point it degenerates into repetition.

    With max_tokens raised to 3500 and frequency_penalty held low, an exhausted
    model pads to the cap with synonym chains — a live proposal ended a section
    with "shattering shattering shattering ... vow-breakingly". Sentence-level
    repair is not possible; the only safe action is to truncate at the onset and
    keep the good prose before it.
    """
    if not text:
        return text
    words = text.split()
    run_start, run_len = 0, 1
    for i in range(1, len(words)):
        prev = words[i - 1].strip(".,;:!?").lower()
        cur = words[i].strip(".,;:!?").lower()
        if cur and cur == prev:
            run_len += 1
            if run_len >= run_threshold:
                cut = " ".join(words[:run_start]).rstrip()
                # Fall back to a sentence boundary so we do not end mid-clause.
                m = list(re.finditer(r"[.!?](\s|$)", cut))
                if m:
                    cut = cut[: m[-1].end()].rstrip()
                log.warning("degenerate repetition detected (%r x%d); truncating draft",
                            words[i], run_len)
                return cut
        else:
            run_len, run_start = 1, i
    return text


def _is_blank(text: Optional[str]) -> bool:
    """A drafted string is unusable if it is None or whitespace-only."""
    return text is None or not str(text).strip()


# Review asides the QA pass removed from the section being drafted ("(needs SME
# confirmation)" and the like). The aside goes and the claim it qualified
# stays, and nothing used to record that it had happened (audit F10). Counted
# per section so a run can show how many there are before deciding whether to
# leave a marker in their place or list the sections for the consultant.
_ASIDES: contextvars.ContextVar = contextvars.ContextVar("stripped_asides", default=None)


async def _draft_with_retry(
    client: httpx.AsyncClient,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
) -> Optional[str]:
    """Draft one (sub)section; retry ONCE if the model returns null/empty.

    Some models occasionally return a null (None) or whitespace-only message
    content on a successful HTTP response. That is not an exception, so it slips
    past ``draft_with_openrouter``'s error handling and used to crash the caller
    on ``.strip()`` — a soft-fail that silently dropped the subsection. Here we
    treat a blank body as a soft failure and retry once (which re-runs the
    existing primary->fallback path). Returns the stripped text, or None if it is
    still blank after the retry. Network/HTTP errors propagate to the caller.
    """
    content = await draft_with_openrouter(client, system_prompt, user_prompt, max_tokens=max_tokens)
    if _is_blank(content):
        log.warning("draft returned null/empty content; retrying once")
        # The trailing newline changes the request, so a response cache cannot
        # hand back the same empty answer.
        content = await draft_with_openrouter(client, system_prompt, user_prompt + "\n",
                                              max_tokens=max_tokens)
    if _is_blank(content):
        return None

    # Single chokepoint for every drafted (sub)section.
    # `source` is the draft `cleaned` is derived from. If it came back marked as
    # cut off (_post_draft), the marker is put back after the trims below: they
    # drop a last line with no sentence ending, and a bare marker is one.
    source = content
    cleaned = strip_model_artifacts(content)
    cleaned, issues = document_qa.qa_text(cleaned)

    # A degenerate draft is padding, not content. Re-draft once — truncating
    # silently (the previous behaviour) hid the failure and lost real prose.
    if any(i.startswith("degenerate") for i in issues):
        log.warning("QA: degenerate draft (%s); re-drafting once",
                    "; ".join(i for i in issues if i.startswith("degenerate")))
        retry = await draft_with_openrouter(
            client, system_prompt,
            user_prompt + (
                "\n\nIMPORTANT: your previous attempt padded the section by repeating "
                "the same phrases. Write it again, shorter, with no repetition. Stop as "
                "soon as the substance is covered — length is not a goal."
            ),
            max_tokens=max_tokens)
        if not _is_blank(retry):
            retry_clean, retry_issues = document_qa.qa_text(
                strip_model_artifacts(retry))
            source = retry
            if not any(i.startswith("degenerate") for i in retry_issues):
                cleaned = retry_clean
            else:
                # Both attempts padded: keep the good prose, drop the padding.
                log.warning("QA: re-draft still degenerate; truncating at onset")
                cleaned = document_qa.truncate_at_degeneration(retry_clean)
        else:
            cleaned = document_qa.truncate_at_degeneration(cleaned)
    elif issues:
        log.info("QA: %s", "; ".join(issues))

    cleaned = _trim_mid_sentence_end(cleaned)
    tally = _ASIDES.get()
    if tally is not None:
        tally["n"] += document_qa.count_review_markers(strip_model_artifacts(source or ""))
    if not _is_blank(cleaned) and (source or "").rstrip().endswith(SME_REVIEW_MARKER):
        cleaned = _mark_cut_off(cleaned)
    return cleaned if not _is_blank(cleaned) else None


def _trim_mid_sentence_end(text: Optional[str]) -> Optional[str]:
    """A prose draft whose last line stops mid-sentence lost its tail somewhere
    finish_reason did not report ("...and preventative Seg", ESNAD 09-24).
    Lists, tables and headers legitimately end without punctuation."""
    if not text:
        return text
    last = text.rstrip().split("\n")[-1].strip()
    if (not last or re.match(r"^([-*•|#>]|\d+[.)]\s)", last)
            or last.endswith((".", "!", "?", ":", ")", '"', "]", "|"))):
        return text
    log.warning("draft ends mid-sentence (%r); trimming", last[-40:])
    return _trim_incomplete_tail(text)


def _strip_echoed_title(text: str, title: str) -> str:
    """Drop a first line that only restates the subsection heading.

    The builder adds the heading; the model, told not to write headings, echoes
    it as a plain line instead. ESNAD 09-23 had eight ("Who Had Access" under
    "Who Had Access", "ESNAD Resource Commitments" under "Saudi Mining Services
    Company (ESNAD) Resource Commitments").
    """
    title_words = set(re.findall(r"[a-z0-9]+", (title or "").lower()))
    first, _, rest = (text or "").lstrip().partition("\n")
    words = re.findall(r"[a-z0-9]+", first.lower())
    line = first.strip()
    # A markdown header is structure the nested-overview facets ask for
    # ("## Ping Identity Overview" under "Ping Identity Solution Overview");
    # strip one only when it IS the title.
    is_header = line.startswith("#")
    if (title_words and words and len(words) <= 12 and rest.strip()
            and not line.startswith("|") and not line.endswith((".", "!", "?", ":"))
            and (set(words) == title_words if is_header else set(words) <= title_words)):
        return rest.lstrip()
    return text


def _draft_failed_placeholder(section_title: str, sub_title: Optional[str],
                              error: str) -> str:
    """Text for a (sub)section the model never successfully attempted.

    Deliberately NOT the assumption placeholder: that one says "an SME must
    write this", which is a statement about the CONTENT. This says the drafting
    call failed, which is a statement about the SYSTEM. Conflating them sent a
    reader looking for missing evidence when the cause was an API error.
    """
    what = f"{sub_title} ({section_title})" if sub_title else section_title
    reason = (error or "").strip().splitlines()[0][:160] if error else "unknown error"
    return (f"{DRAFT_FAILED_MARKER} This subsection was not drafted: the "
            f"generation call for \"{what}\" did not complete ({reason}). "
            f"This is a system failure, not a gap in the evidence or the "
            f"discovery answers. Re-run generation before issuing this draft.")


def _assumption_placeholder(
    context: dict,
    section_title: str,
    sub_title: Optional[str] = None,
    facet: Optional[str] = None,
) -> str:
    """Grounded placeholder for a (sub)section the model could not draft.

    Derived ONLY from intake/context (client, proposal type, vendor, the
    section/subsection this call was meant to cover). Deliberately generic and
    marked ``[ASSUMPTION]`` + ``[SME REVIEW]`` — it invents no client facts,
    metrics, versions or commitments, only states that an SME must author it.
    """
    client_name = context.get("client_name") or "the client"
    ptype = context.get("proposal_type") or "implementation"
    focus = sub_title or section_title
    scope = f", which should cover {facet}," if facet else ""
    return (
        f"{ASSUMPTION_MARKER} The \"{focus}\" content{scope} could not be automatically "
        f"drafted from the available evidence for this {ptype} engagement for "
        f"{client_name}{_vendor_clause(context.get('iam_vendor'))}. This is a placeholder "
        f"assumption to be validated and replaced with grounded detail by a subject-matter "
        f"expert before any client use — no specifics have been inferred. {SME_REVIEW_MARKER}"
    )


# Prose subsections get a much tighter budget than table subsections.
#
# Measured: run 5 produced 16,051 prose words against the human proposal's 6,648
# across a near-identical subsection count (54 vs 53). That is ~300 words per
# subsection against IV's ~125. The cause is not the model choosing to ramble:
# a section was measured running dry at ~950 tokens and continuing to ~2,000, so
# it fills whatever budget it is given. The budget is therefore the lever.
#
# Table subsections keep the full budget - a 25-row RACI needs it.
# 420 tokens for a 220-word instruction left no headroom: dense technical prose
# tokenizes above 1.35x per word, so the model wrote to its word target, began
# an [SME REVIEW] marker and was guillotined mid-word. Run 8 shipped "[SME REV",
# "[SME RE" and "[SME REVIEW: specific SoD rule s".
#
# A hard cap should be a SAFETY NET against runaway generation, never the
# binding constraint on normal output. The word instruction does the shaping.
PROSE_SUBSECTION_TOKENS = int(os.environ.get("SHILPI_PROSE_SUBSECTION_TOKENS", "900"))
PROSE_SUBSECTION_WORDS = int(os.environ.get("SHILPI_PROSE_SUBSECTION_WORDS", "220"))
_STATED_LENGTH_RE = re.compile(r"roughly (\d+)\s*-\s*(\d+) words", re.I)
# IV's own proposals have a MEDIAN body paragraph of 29 words and a longest of
# 108. Run 8's median was 55 with twenty paragraphs over 100 and a longest of
# 180 -- because the instruction capped the SUBSECTION and said nothing about
# paragraphs, so the model wrote one 180-word block instead of four short ones.
PROSE_PARAGRAPH_WORDS = int(os.environ.get("SHILPI_PROSE_PARAGRAPH_WORDS", "60"))
_WANTS_TABLE_RE = re.compile(r"\bmarkdown\s+TABLE\b|\bas a (?:markdown )?TABLE\b", re.I)
# A subsection that asks for nested capability headers can't be written in 220
# words: the ESNAD rerun spread 7 Ping capability headings across ~180 words
# while IV's own Ping section ran ~5,000. Gets the table budget, keeps the
# paragraph discipline.
_WANTS_STRUCTURE_RE = re.compile(r"\bNESTED MARKDOWN\b")


async def draft_section(
    client: httpx.AsyncClient,
    section_spec: SectionSpec,
    context: dict,
    *,
    embed_fn: EmbedFn,
    retrieve_fn: RetrieveFn,
    build_grounded_system_fn: BuildSystemFn,
    top_k: int = TOP_K,
    fanout: int = 1,
    subsections: int = 1,
    max_tokens: int = MAX_DRAFT_TOKENS,
    retrieve_product_fn=None,
    build_product_evidence_fn=None,
) -> dict:
    """Draft one proposal section, grounded in retrieved corpus evidence.

    Depth controls (Pass 3):
      fanout      : number of retrieval queries merged for this section's evidence.
      subsections : number of INDEPENDENT drafting calls. >1 splits the section
                    into focused facets (Overview / Detailed Design / ...), each
                    its own LLM call with the per-call ``max_tokens`` budget — so
                    depth grows via more calls, never a bigger single call.

    Returns: {"id","title","content","subsections","citations","max_similarity",
              "needs_sme_review"}.
    """
    chunks, product_chunks = await _retrieve_fanout(
        client, section_spec, context,
        embed_fn=embed_fn, retrieve_fn=retrieve_fn, top_k=top_k, fanout=fanout,
        retrieve_product_fn=retrieve_product_fn,
    )

    max_similarity = max((float(c.get("similarity") or 0.0) for c in chunks), default=0.0)
    # Weak CORPUS evidence is only a review trigger when the discovery answers
    # do not cover the section either. The corpus is other clients' proposals:
    # it cannot corroborate a fact about THIS engagement, so its silence says
    # nothing when the consultant has already supplied the detail.
    #
    # Before this, "Proposed DR Hardware Sizing" was flagged as unreviewed while
    # the discovery answers said "DR sized identically to production" -- one of
    # the reasons run 10 still carried 39 markers after the discovery-routing
    # fix landed.
    #
    # Deliberately computed from PROPOSAL chunks only, not product_chunks:
    # strong similarity against a vendor's marketing datasheet says the
    # PRODUCT is well documented, not that THIS client's engagement facts
    # (real sizing, real environment counts) are grounded. Blending the two
    # into one signal risks a well-marketed product masking a genuine need
    # for client-specific review.
    asides = {"n": 0}
    _ASIDES.set(asides)
    weak_corpus = (not chunks) or (max_similarity < WEAK_EVIDENCE_THRESHOLD)
    discovery_ctx = discovery_context_for(section_spec.id,
                                          context.get("discovery_answers"))
    # A few lines of client facts is a passing mention; a substantial block
    # means the consultant has actually briefed this section.
    has_client_facts = len(discovery_ctx) >= MIN_DISCOVERY_FOR_GROUNDING
    needs_sme_review = weak_corpus and not has_client_facts

    # Reuse the brain's evidence/system-prompt builder, then layer section-specific
    # drafting instructions on top so the model drafts THIS section.
    section_title = section_spec.render_title(context)
    rfp_ctx = (context.get("rfp_text") or "")[:4000]
    # The discovery answers relevant to THIS section. Without this the drafting
    # engine saw only rfp_text and invented or omitted every captured specific.
    client_facts = (f"CLIENT-SUPPLIED FACTS FOR THIS SECTION — these are "
                    f"authoritative and MUST be used verbatim where relevant. Do not "
                    f"replace them with generic statements, and do not claim they are "
                    f"unavailable:\n{discovery_ctx}\n\n") if discovery_ctx else ""
    # Facts and RFP context are the same for every subsection of this section,
    # so they live in the cached system prompt, not the per-call user message
    # (where they re-billed at full price on every facet).
    section_context = f"\n\n{client_facts}RFP / requirement context:\n{rfp_ctx}"
    if section_spec.id == "similar_experience" and context.get("delivered_references"):
        section_context += "\n\n" + context["delivered_references"]

    def _system_for(pchunks: list[dict]) -> str:
        evidence_block = build_grounded_system_fn(chunks)
        if pchunks and build_product_evidence_fn:
            evidence_block += build_product_evidence_fn(pchunks)
        return _SECTION_SYSTEM_TEMPLATE.format(
            title=section_title,
            proposal_type=context.get("proposal_type", "implementation"),
            client_name=context.get("client_name", "the client"),
            vendor_clause=_vendor_clause(context.get("iam_vendor")),
            vendor_scope_clause=(_vendor_scope_clause(context)
                                 + _engagement_facts_clause(context)),
            purpose=section_spec.purpose,
            marker=SME_REVIEW_MARKER,
            evidence=evidence_block,
        ) + section_context
    system_prompt = _system_for(product_chunks)

    # Plan the subsection facets.
    #
    # A section that defines its OWN subsections uses all of them, ignoring the
    # depth tier's subsection count: those headings are the section's structure,
    # not a knob. "Proposed Production Hardware Sizing" is not an optional
    # elaboration of "Proposed Solution", it is part of what that section IS.
    #
    # Only sections without their own subsections fall back to the generic
    # Overview / Detailed Design / Considerations triple, which is what every
    # section used to get and why the table of contents repeated three headings
    # seven times.
    own = section_spec.render_subsections(context)
    if own:
        # Drop subsections whose discovery inputs are all empty. A TABLE
        # subsection with no inputs renders a header row and "To be confirmed"
        # in every cell, which reads as work done and figures withheld. BTPN
        # produced a 6x6 Licence BOQ and a 6x3 Payment Milestones entirely of
        # placeholders, against an IV proposal that had neither table.
        _ans = context.get("discovery_answers")
        # Three filters, narrowest last:
        #   1. evidence   — drop subsections whose inputs are all empty
        #   2. families   — one sizing/RACI/tranche variant, not four
        #   3. scale      — cap per section for a compact engagement
        facets = scope_filter.filter_subsections(
            type("_S", (), {"subsections": own})(), _ans)
        facets = scope_filter.collapse_families(facets, _ans)
        facets = scope_filter.cap_for_scale(facets, _ans)
    else:
        n_sub = max(1, min(int(subsections), len(SUBSECTION_FACETS)))
        facets = SUBSECTION_FACETS[:n_sub] if n_sub > 1 else []

    async def _draft_once(user_prompt: str) -> Optional[str]:
        return await _draft_with_retry(client, system_prompt, user_prompt, max_tokens)

    subsection_results: list[dict] = []
    drafting_failed = False
    if not facets:
        user_prompt = (
            f"Draft the \"{section_title}\" section now, grounded in the "
            f"CLIENT-SUPPLIED FACTS above or the EVIDENCE, citing evidence "
            f"inline as [N]."
        )
        try:
            content = await _draft_once(user_prompt)
        except Exception as e:
            log.error("draft_section drafting failed for %s: %s", section_spec.id, e)
            content = _draft_failed_placeholder(section_title, None, str(e))
            drafting_failed = True
        # Null/empty draft (even after the retry inside _draft_once) must not
        # drop the section: emit a grounded placeholder instead.
        if _is_blank(content):
            log.warning("draft_section produced empty content for %s; using grounded placeholder",
                        section_spec.id)
            content = _assumption_placeholder(context, section_title)
            needs_sme_review = True
    else:
        # Independent drafting call per facet (the structured fan-out).
        parts: list[str] = []
        for sub_title, facet in facets:
            wants_table = _WANTS_TABLE_RE.search(facet) is not None
            wants_structure = _WANTS_STRUCTURE_RE.search(facet) is not None
            budget = (max_tokens if wants_table or wants_structure
                      else min(max_tokens, PROSE_SUBSECTION_TOKENS))
            # A facet that states its own length ("roughly 500-800 words") gets
            # it: the flat 220-word prose cap held ESNAD's executive summary to
            # 185 words against IV's 1,034.
            _len = _STATED_LENGTH_RE.search(facet)
            word_cap = int(_len.group(2)) if _len else PROSE_SUBSECTION_WORDS
            if _len:
                budget = min(max_tokens, max(budget, word_cap * 2))
            length_rule = (
                "Output the table and one short lead-in line, nothing else."
                if wants_table else
                f"Give each capability area enough grounded detail to stand on "
                f"its own. Keep every paragraph under {PROSE_PARAGRAPH_WORDS} "
                f"words. If you run out of grounded material, stop - do not pad."
                if wants_structure else
                f"Write NO MORE than {word_cap} words in total, and "
                f"keep every paragraph under {PROSE_PARAGRAPH_WORDS} words - break "
                f"longer thoughts into separate short paragraphs. IV's proposals have "
                f"a median paragraph of 29 words; short paragraphs are the house style, "
                f"not a constraint. Density beats length. If you run out of grounded "
                f"material, stop - do not pad."
            )
            user_prompt = (
                # The system prompt establishes that CLIENT-SUPPLIED FACTS
                # outrank EVIDENCE and that their absence from the corpus means
                # nothing. This line used to say "Ground every claim in the
                # EVIDENCE", contradicting it in the more immediate user
                # message -- which is why run 10 still carried 24 inline markers
                # reading "not in evidence" and "not specified in evidence",
                # phrases the system prompt explicitly forbids. Two of them
                # flagged DR sizing and load-balancer scope that the consultant
                # had supplied outright.
                f"Draft the \"{sub_title}\" subsection of the \"{section_title}\" section, "
                f"focusing specifically on {facet}. Ground every claim in the "
                f"CLIENT-SUPPLIED FACTS above or the EVIDENCE, citing evidence "
                f"inline as [N]; client-supplied facts are stated directly and "
                f"not cited. Do not repeat content that belongs in other "
                f"subsections.\n"
                f"{length_rule}"
            )
            # "Why Ping Identity" drafted from a pool that also held Saviynt's
            # docs sold Saviynt's features as Ping's. A subsection named for one
            # vendor sees only that vendor's product evidence.
            sub_vendor = _subsection_vendor(sub_title, context)
            sub_system = (_system_for([c for c in product_chunks
                                       if c.get("_for_vendor") == sub_vendor])
                          if sub_vendor else system_prompt)
            try:
                sub_content = await _draft_with_retry(
                    client, sub_system, user_prompt, budget)
            except Exception as e:
                log.error("draft_section subsection %s failed for %s: %s", sub_title, section_spec.id, e)
                # NOT the SME marker: this is a system failure, not a content
                # gap, and labelling it "[SME REVIEW]" sent a reader hunting for
                # missing evidence when run 11's real cause was a 402.
                sub_content = _draft_failed_placeholder(section_title, sub_title, str(e))
                drafting_failed = True
            # Null/empty subsection (even after the retry inside _draft_once) is
            # the soft-fail that used to lose ~2/3 of full-depth content: emit a
            # grounded placeholder rather than an empty string.
            if _is_blank(sub_content):
                log.warning("draft_section subsection %s empty for %s; using grounded placeholder",
                            sub_title, section_spec.id)
                sub_content = _assumption_placeholder(context, section_title, sub_title, facet)
                needs_sme_review = True
            sub_content = _strip_echoed_title(sub_content, sub_title)
            subsection_results.append({"title": sub_title, "content": sub_content})
            # An untitled subsection is continuous prose under the section
            # heading (IV's executive summary), so no "### " marker.
            parts.append(f"### {sub_title}\n\n{sub_content}"
                         if sub_title.strip() else sub_content)
        content = "\n\n".join(parts).strip()

    content = (content or "").strip()

    # A drafting FAILURE must not be reported as weak evidence. The banner below
    # asserts "Retrieval found no supporting evidence (max similarity 0.00)",
    # which is a claim about the corpus. Run 11's sixteen lost subsections
    # carried exactly that line while the real cause was an OpenRouter 402, and
    # the whole investigation went looking in the wrong place.
    if drafting_failed:
        if DRAFT_FAILED_MARKER not in content:
            content = (f"{DRAFT_FAILED_MARKER} One or more parts of this section "
                       f"could not be generated. Re-run before issuing.\n\n" + content)
    elif needs_sme_review and not content.startswith("[SME REVIEW"):
        content = (
            f"[SME REVIEW: weak evidence] Retrieval found "
            f"{'no' if not chunks else 'only low-similarity'} supporting evidence "
            f"(max similarity {max_similarity:.2f}). Verify and expand with an SME.\n\n"
            + content
        )

    return {
        "id": section_spec.id,
        "title": section_title,
        "content": content,
        "subsections": subsection_results,
        "citations": chunks,
        "product_citations": product_chunks,
        "max_similarity": max_similarity,
        "needs_sme_review": needs_sme_review,
        "stripped_asides": asides["n"],
    }


# ---------------------------------------------------------------------------
# DOCX assembly
# ---------------------------------------------------------------------------

_DRAFT_COLOR = RGBColor(0xB0, 0x00, 0x00)  # warning red (SME-review flag)
_CITATION_RE = re.compile(r"\[(\d+)\]")



# --- Contents-page navigation -------------------------------------------------
#
# The contents table listed section names as plain text: no page numbers and
# nothing to click. A reader had to scroll and hunt. IV's own proposals carry a
# numbered, navigable contents page.
#
# A bare Word TOC field is still not used -- it displays "Right-click here and
# choose 'Update Field'" until someone presses F9, which is worse than no page
# numbers. Instead each heading gets a BOOKMARK, each contents entry is an
# internal HYPERLINK to it, and the page number is a PAGEREF field. Word and
# LibreOffice resolve PAGEREF on open when `updateFields` is set in settings,
# so the reader never sees placeholder text.

_bookmark_counter = itertools.count(1)


def _slug_bookmark(text: str) -> str:
    """A Word-legal bookmark name: letters, digits and underscores, <= 40 chars."""
    base = re.sub(r"[^0-9A-Za-z]+", "_", (text or "").strip()).strip("_")
    return f"S_{base[:34]}_{next(_bookmark_counter)}"


def _bookmark_paragraph(paragraph, name: str) -> None:
    """Wrap a paragraph in bookmarkStart/bookmarkEnd so links can target it."""
    bid = str(next(_bookmark_counter))
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), bid)
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), bid)
    paragraph._p.insert(0, start)
    paragraph._p.append(end)


def _add_internal_link(paragraph, text: str, bookmark: str, *,
                       bold: bool = False, size: Optional[Pt] = None,
                       color=None):
    """A clickable run that jumps to a bookmark in this document."""
    link = OxmlElement("w:hyperlink")
    link.set(qn("w:anchor"), bookmark)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    if bold:
        props.append(OxmlElement("w:b"))
    if size is not None:
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), str(int(size.pt * 2)))
        props.append(sz)
    if color is not None:
        col = OxmlElement("w:color")
        col.set(qn("w:val"), str(color))
        props.append(col)
    run.append(props)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def _add_pageref(paragraph, bookmark: str, *, size: Optional[Pt] = None) -> None:
    """A PAGEREF field: the page a bookmark falls on, resolved by the reader."""
    run = paragraph.add_run()
    if size is not None:
        run.font.size = size
    r = run._r
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" PAGEREF {bookmark} \\h "
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "-"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for el in (begin, instr, sep, placeholder, end):
        r.append(el)


def _enable_field_update_on_open(document: Document) -> None:
    """Ask Word/LibreOffice to resolve PAGEREF fields when the file is opened.

    Without this the page-number column shows "-" until the reader presses F9 --
    the same defect that made a bare TOC field unacceptable.
    """
    try:
        settings = document.settings.element
        if settings.find(qn("w:updateFields")) is None:
            el = OxmlElement("w:updateFields")
            el.set(qn("w:val"), "true")
            settings.append(el)
    except Exception as e:  # noqa: BLE001 - a missing page number must not fail the doc
        log.warning("could not enable field auto-update: %s", e)


def _add_static_toc(document: Document, sections: list[dict],
                    extra_titles: Optional[list[str]] = None) -> dict[str, str]:
    """Write a formatted contents table from the headings we already know.

    Renders as a real table (section | subsections) rather than a flat run of
    Normal paragraphs, which looked unformatted next to IV's own documents. A
    bare Word TOC field is not used because it shows "Right-click here and
    choose 'Update Field'..." until someone presses F9.
    """
    rows = []
    for sec in sections:
        title = (sec.get("title") or "").strip()
        if not title:
            continue
        subs = [(sub.get("title") or "").strip()
                for sub in (sec.get("subsections") or [])]
        rows.append((title, [x for x in subs if x]))
    for title in extra_titles or []:
        rows.append((title, []))
    if not rows:
        return

    # Bookmark names are assigned HERE and handed back to the section renderer,
    # so the contents entry and the heading it targets cannot drift apart.
    bookmarks: dict[str, str] = {}

    table = document.add_table(rows=0, cols=3)
    table.style = "Table Grid"
    table.autofit = False
    for idx, (title, subs) in enumerate(rows, start=1):
        cells = table.add_row().cells
        cells[0].width = Inches(0.45)
        cells[1].width = Inches(4.95)
        cells[2].width = Inches(0.60)

        num = cells[0].paragraphs[0]
        num.paragraph_format.space_after = Pt(2)
        nrun = num.add_run(str(idx))
        nrun.bold = True
        nrun.font.color.rgb = branding.ORANGE

        mark = bookmarks.setdefault(title, _slug_bookmark(title))
        body = cells[1].paragraphs[0]
        body.paragraph_format.space_after = Pt(2)
        _add_internal_link(body, title, mark, bold=True, color=branding._NAVY_HEX)

        page = cells[2].paragraphs[0]
        page.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        page.paragraph_format.space_after = Pt(2)
        _add_pageref(page, mark)

        for sub in subs:
            sub_mark = bookmarks.setdefault(sub, _slug_bookmark(sub))
            sp = cells[1].add_paragraph()
            sp.paragraph_format.left_indent = Inches(0.18)
            sp.paragraph_format.space_after = Pt(0)
            _add_internal_link(sp, sub, sub_mark, size=Pt(9),
                               color="6B6B72")
    return bookmarks


def _add_toc_field(document: Document) -> None:
    """Insert a real, F9-refreshable Word Table of Contents field.

    Until the user updates the field in Word (right-click -> Update Field, or
    F9), Word shows the instructional placeholder text below.
    """
    para = document.add_paragraph()
    run = para.add_run()
    r = run._r
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = r'TOC \o "1-3" \h \z \u'
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = (
        "Right-click here and choose 'Update Field' (or press F9) to build the "
        "table of contents from the section headings below."
    )
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    r.append(fld_begin)
    r.append(instr)
    r.append(fld_sep)
    r.append(placeholder)
    r.append(fld_end)


# Inline emphasis the drafting model emits. ONLY asterisks are treated as
# markup: underscores are far too common in this domain's technical identifiers
# (data_retention, session_data, iam_vendor) and an underscore rule silently
# italicises across them.
_INLINE_MD_RE = re.compile(r"(\*\*.+?\*\*|\*(?!\s)[^*\n]+?(?<!\s)\*)", re.S)

# A bullet marker is "-", "•", or a SINGLE "*" followed by space. "**" is bold.
_BULLET_MARKER_RE = re.compile(r"^\s*(?:[-\u2022]|\*(?!\*))\s+")


def _add_inline_runs(para, text: str) -> None:
    """Add ``text`` to ``para`` converting **bold** / *italic* into real runs.

    Without this the model's markdown lands in the DOCX as literal asterisks —
    a generated proposal contained 147 stray ``**`` and 82 ``*`` sequences,
    which is immediately visible to anyone opening the document.
    """
    for token in _INLINE_MD_RE.split(text):
        if not token:
            continue
        if len(token) > 4 and token.startswith("**") and token.endswith("**"):
            para.add_run(token[2:-2]).bold = True
        elif len(token) > 2 and token.startswith("*") and token.endswith("*"):
            para.add_run(token[1:-1]).italic = True
        else:
            para.add_run(token)


def _is_gfm_separator(line: str) -> bool:
    """True for a GFM header/body separator such as ``|---|---|`` or ``---|---``.

    Deliberately stricter than a bare subset check: an EMPTY line is a subset of
    the allowed character set, which previously let a single stray ``|`` line be
    parsed as a one-row table.
    """
    s = line.strip()
    return bool(s) and "-" in s and set(s) <= set("|-: ")


def _parse_gfm_table(lines: list[str], i: int) -> Optional[tuple[list[str], list[list[str]], int]]:
    """If a GFM table starts at ``lines[i]``, return (headers, rows, next_index).

    Returns None when there is no table here, so callers fall through to their
    normal prose handling.
    """
    if i >= len(lines) or not lines[i].strip().startswith("|"):
        return None
    if i + 1 >= len(lines) or not _is_gfm_separator(lines[i + 1]):
        return None
    headers = [c.strip() for c in lines[i].strip().strip("|").split("|")]
    j = i + 2
    rows: list[list[str]] = []
    while j < len(lines) and lines[j].strip().startswith("|"):
        rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
        j += 1
    return headers, rows, j


def _add_gfm_table(document: Document, headers: list[str], rows: list[list[str]]) -> None:
    """Render parsed GFM table data as a native Word table.

    Cell text goes through ``_add_inline_runs`` so ``**Total**`` inside a BOQ or
    milestone row becomes a bold run rather than literal asterisks — the same
    failure that put 147 stray ``**`` into a generated proposal.
    """
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    for j, h in enumerate(headers):
        cell_para = table.rows[0].cells[j].paragraphs[0]
        _add_inline_runs(cell_para, h)
        for run in cell_para.runs:
            run.bold = True
    for row in rows:
        cells = table.add_row().cells
        for j in range(len(headers)):
            _add_inline_runs(cells[j].paragraphs[0], row[j] if j < len(row) else "")


def _add_body_paragraphs(document: Document, text: str) -> None:
    """Add body text, preserving [N] citation markers, splitting on blank lines.

    Markdown tables emitted inside a drafted section are rendered as native Word
    tables. Before this, only the compliance-matrix path could do that, so a
    sizing or BOQ table anywhere else in the document landed as a single
    paragraph of literal pipe characters.
    """
    if "|" in text:
        _add_body_with_tables(document, text)
        return
    _add_prose_paragraphs(document, text)


def _add_body_with_tables(document: Document, text: str) -> None:
    """Split body text into alternating prose runs and GFM tables, in order."""
    lines = text.splitlines()
    i = 0
    prose: list[str] = []

    def _flush() -> None:
        if prose:
            _add_prose_paragraphs(document, "\n".join(prose))
            prose.clear()

    while i < len(lines):
        parsed = _parse_gfm_table(lines, i)
        if parsed is None:
            prose.append(lines[i])
            i += 1
            continue
        headers, rows, i = parsed
        _flush()
        _add_gfm_table(document, headers, rows)
        # A plan table with week columns gets its Gantt chart right under it.
        chart = diagram_engine.render_gantt(diagram_engine.gantt_rows(headers, rows))
        if chart:
            _add_picture_fitted(document, io.BytesIO(chart),
                                max_w=_DIAGRAM_MAX_W, max_h=_DIAGRAM_MAX_H)
    _flush()


def _add_prose_paragraphs(document: Document, text: str) -> None:
    """Render body text into real Word paragraph styles.

    This used to decide per BLOCK: if a block began with "- " the whole block
    became bullets, otherwise the whole block became one paragraph. Drafted text
    is not shaped that way. A block like

        Assumptions & Open Questions:
        - The deployment model is not specified
        - Volumetrics are not provided

    begins with a label, so the label AND both dashes landed in a single
    paragraph as literal text. Measured on Amlak run 4: 42 paragraphs carried
    embedded newlines and 52 dash-lines rendered as body text, with List Bullet
    used zero times in the entire document.

    The decision is now per LINE, so each line gets the style it deserves.
    """
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        block = block.strip()
        if not block:
            continue
        pending: list[str] = []

        def _flush() -> None:
            if not pending:
                return
            para = document.add_paragraph()
            _add_inline_runs(para, " ".join(pending))
            pending.clear()

        for raw in block.splitlines():
            line = raw.strip()
            if not line:
                _flush()
                continue

            heading = re.match(r"^(#{1,6})\s+(.*)$", line)
            if heading:
                _flush()
                # The SUBSECTION containing this drafted text is already
                # rendered at H2 by the template mechanism (see
                # _draft_section / assemble_docx). The model's OWN markdown
                # headers inside its drafted prose therefore belong ONE level
                # BELOW that -- starting at H3, not H2 -- or the model's
                # first "#" stacks a second, visually indistinguishable H2
                # directly inside a subsection that is already H2.
                #
                # Capped at H5, not silently flattened to H3 regardless of
                # depth: IV's own proposals genuinely nest to H5 (e.g.
                # "Saviynt EIC Logical Architecture" H3 -> a named capability
                # H4 -> a specific workflow step H5). The template has no
                # subsection instruction that asks a model to write that
                # deep YET (that is the next piece of this sprint), but the
                # renderer must not be the reason a model that DOES produce
                # nested structure gets flattened.
                _add_subheading(document, heading.group(2).strip(),
                                level=min(5, len(heading.group(1)) + 2))
                continue

            if _BULLET_MARKER_RE.match(line):
                _flush()
                # Strip ONLY the bullet marker. A blunt lstrip("-*• ") also ate
                # the opening ** of "- **Label:** text", leaving an unmatched
                # closing marker and losing the bold entirely.
                body = _BULLET_MARKER_RE.sub("", line).strip()
                if body:
                    _add_inline_runs(document.add_paragraph(style="List Bullet"), body)
                continue

            numbered = re.match(r"^\d+[.)]\s+(.*)$", line)
            if numbered and len(line.split()) <= _MAX_LIST_LINE_WORDS:
                _flush()
                _add_inline_runs(document.add_paragraph(style="List Number"),
                                 numbered.group(1).strip())
                continue

            if _is_pseudo_heading(line):
                _flush()
                _add_subheading(document, line.rstrip(":").strip(), level=3)
                continue

            pending.append(line)
        _flush()


# A numbered line longer than this is prose that happens to open with "1.", not
# a list item. Amlak run 3 contained a 783-word "list item".
_MAX_LIST_LINE_WORDS = 40

# A short line ending in a colon that introduces what follows. The model writes
# these as plain text ("Risks to Manage:", "Assumptions & Open Questions:") and
# they must become headings, not body prose. Bounded by length so a real
# sentence containing a colon is not promoted.
_PSEUDO_HEADING_RE = re.compile(r"^[A-Z][^.!?]{2,70}:$")


def _is_pseudo_heading(line: str) -> bool:
    return bool(_PSEUDO_HEADING_RE.match(line)) and len(line.split()) <= 10


def _add_subheading(document: Document, text: str, *, level: int = 3) -> None:
    """A bold sub-heading that is a real Word heading, so it reaches the TOC."""
    para = document.add_heading("", level=level)
    _add_inline_runs(para, text)
    for run in para.runs:
        run.bold = True


# No blurry image in a proposal, ever (Imran, ESNAD 09-27: a 392-px Frost radar
# stretched to 6 inches). An image is never printed below this many pixels per
# inch; a supporting image that would then be too small to read is dropped.
MIN_PRINT_DPI = int(os.environ.get("SHILPI_MIN_PRINT_DPI", "150"))
_MIN_ASSET_W = Inches(2.5)
_EMU_PER_INCH = 914400


def _add_picture_fitted(document: Document, stream, *, max_w, max_h,
                        drop_if_small: bool = False) -> bool:
    """Insert an image scaled to fit inside (max_w, max_h), keeping aspect ratio,
    and never enlarged past MIN_PRINT_DPI. Returns False when the image was
    dropped because it could not be shown sharp at a readable size.

    CENTRED. `document.add_picture` creates its own paragraph and leaves it
    left-aligned, so every diagram and reusable image sat hard against the left
    margin with a ragged gap on the right -- visible against IV's own documents,
    where images are centred.
    """
    try:
        from PIL import Image as _PILImage
        pos = stream.tell() if hasattr(stream, "tell") else None
        with _PILImage.open(stream) as im:
            w_px, h_px = im.size
        if pos is not None:
            stream.seek(pos)
        if w_px and h_px:
            scale = min(max_w / w_px, max_h / h_px, _EMU_PER_INCH / MIN_PRINT_DPI)
            if drop_if_small and w_px * scale < _MIN_ASSET_W:
                log.warning("image dropped: %dx%d px cannot print sharp at a readable "
                            "size (%d dpi minimum)", w_px, h_px, MIN_PRINT_DPI)
                return False
            document.add_picture(stream, width=int(w_px * scale), height=int(h_px * scale))
            _centre_last_paragraph(document)
            return True
    except Exception as e:  # noqa: BLE001 — never lose a diagram over sizing
        log.warning("could not measure diagram for fitting (%s); using width only", e)
        if hasattr(stream, "seek"):
            stream.seek(0)
        if drop_if_small:
            return False  # unmeasurable: cannot promise it is sharp
    document.add_picture(stream, width=max_w)
    _centre_last_paragraph(document)
    return True


def _centre_last_paragraph(document: Document) -> None:
    """Centre the paragraph add_picture just created."""
    if document.paragraphs:
        document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER


# Reusable assets are supporting material, not the point of the page: sized
# smaller than a generated architecture diagram so they illustrate rather than
# dominate. The text column is 5.9in wide with IV's margins.
# Diagrams are the point of the page; reusable assets illustrate. Different caps.
_DIAGRAM_MAX_W = Inches(6.0)
_DIAGRAM_MAX_H = Inches(7.5)

_IMAGE_MAX_W = Inches(4.5)
_IMAGE_MAX_H = Inches(3.2)


# A generated diagram whose topic a vendor kit picture already covers is left
# out: ESNAD 09-28 printed three JML flows and three PAM flows, IV's kit
# versions beside Shilpi's thinner redraws. (diagram topic, kit file marker)
_KIT_COVERS: tuple[tuple[str, str], ...] = (
    (r"privileged|\bpam\b", "_pam_"),
    (r"governance|certification|\biga\b", "_eic_logical"),
    (r"joiner|lifecycle|\bjml\b|hrms", "_jml_"),
)
_OVERLAP_TYPES = frozenset({"architecture", "component"})


def _label_tokens(labels) -> set:
    return {w for lab in labels or () for w in re.findall(r"[a-z0-9]{3,}", str(lab).lower())}


def _is_duplicate(d: dict, kit_ids: set, kept: list[dict]) -> bool:
    if d.get("diagram_type") == "stack":
        return False  # the one-page stack is IV's lead picture; always kept
    key = f"{d.get('diagram_type') or ''} {d.get('title') or ''}"
    if any(re.search(pat, key, re.I) and any(mark in k for k in kit_ids)
           for pat, mark in _KIT_COVERS):
        return True
    # Two structural views of the same boxes (ESNAD 09-28: "Integration" shared
    # 85% of its labels with "Solution Architecture") print as one picture twice.
    if d.get("diagram_type") in _OVERLAP_TYPES and d.get("nodes"):
        mine = _label_tokens(d["nodes"])
        for k in kept:
            other = _label_tokens(k.get("nodes"))
            if (k.get("diagram_type") in _OVERLAP_TYPES and mine and other
                    and len(mine & other) / min(len(mine), len(other)) >= 0.7):
                return True
    return False


def _embeddable_diagrams(diagrams: Optional[list[dict]],
                         sections: Optional[list[dict]] = None,
                         dropped: Optional[list] = None) -> list[dict]:
    """Diagrams that will actually be embedded: approved AND carrying an image,
    minus duplicates of a kit picture or of another diagram.

    Single source of truth. The contents list previously re-implemented a looser
    version of this check and advertised a diagram section the assembler then
    declined to create.
    """
    kit_ids = {str(a.get("id") or "") for s in sections or () for a in s.get("assets") or ()
               if str(a.get("id") or "").startswith("kit:")}
    out: list[dict] = []
    kept: list[dict] = []
    for d in diagrams or []:
        if (d.get("status") or "").strip() != "approved":
            continue
        if _is_duplicate(d, kit_ids, kept):
            log.info("diagram '%s' left out: duplicates a kit picture or another diagram",
                     d.get("title"))
            # An APPROVED diagram is being dropped: the caller reports it (audit
            # F57). The log line was the only trace of it.
            if dropped is not None:
                dropped.append(d.get("title") or "Architecture Diagram")
            continue
        kept.append(d)
        title = d.get("title") or "Architecture Diagram"
        image = d.get("image_bytes")
        path = d.get("image_path")
        extra = {"diagram_type": d.get("diagram_type"), "legend": d.get("legend")}
        if image:
            out.append({"title": title, "stream": io.BytesIO(image), **extra})
        elif path and os.path.exists(path):
            out.append({"title": title, "stream": path, **extra})
    return out


def _unplaced_diagrams(diagrams: Optional[list[dict]],
                       sections: list[dict], owners: Optional[dict] = None) -> list[dict]:
    """Diagrams no subsection heading will claim.

    The contents page is written BEFORE the sections are rendered, so it cannot
    read `_placed_diagrams`. It replays the same matching instead: advertising a
    "Solution Architecture Diagrams" section that then contains nothing is the
    same class of bug as the contents entry that once advertised a diagram
    section the assembler declined to create.
    """
    embeddable = _embeddable_diagrams(diagrams, sections)
    if not embeddable:
        return []
    headings = [(sub.get("title") or "")
                for sec in sections for sub in (sec.get("subsections") or [])]
    claimed: set = set()
    for heading in headings:
        _claim_diagram(embeddable, heading, claimed, owners)
    return [d for d in embeddable if id(d) not in claimed]


def _add_approved_diagrams(document: Document, embeddable: list[dict],
                           already_placed: Optional[set] = None,
                           toc_bookmarks: Optional[dict] = None) -> None:
    """Embed ONLY approved, rendered architecture diagrams as images.

    A diagram is embedded iff status == 'approved' and it carries usable image
    bytes (``image_bytes``) or a readable ``image_path``. Everything else
    (draft/needs_review/rejected, or no render) is skipped — this is the DOCX-side
    enforcement of the approval gate. If nothing qualifies, no section is added,
    so proposals without approved diagrams are unchanged.
    """
    # `embeddable` is the SAME list the section renderer worked from, not a
    # fresh call to _embeddable_diagrams. That function builds new dicts each
    # time, so id() is not stable across calls and an identity filter here
    # silently matched nothing: run 10 placed every diagram inline and then
    # emitted this heading again with no image under it, because the BytesIO
    # stream had already been consumed.
    if already_placed:
        embeddable = [d for d in embeddable if id(d) not in already_placed]
    if not embeddable:
        return

    document.add_page_break()
    _dg = branding.add_section_heading(document, "Solution Architecture Diagrams")
    if toc_bookmarks and "Solution Architecture Diagrams" in toc_bookmarks:
        _bookmark_paragraph(_dg, toc_bookmarks["Solution Architecture Diagrams"])
    intro = document.add_paragraph()
    irun = intro.add_run(
        "The following architecture diagrams have been reviewed and approved for inclusion. "
        "They are draft artefacts for human review and not client-ready commitments."
    )
    irun.italic = True
    irun.font.size = Pt(10)
    for item in embeddable:
        document.add_heading(item["title"], level=2)
        try:
            # Width alone lets a tall D2 render scale to any height — a live
            # proposal had architecture diagrams spanning 2+ pages. Constrain by
            # BOTH dimensions, preserving aspect ratio, so a diagram always fits
            # on one page.
            _add_picture_fitted(document, item["stream"],
                                max_w=Inches(6.0), max_h=Inches(7.5))
        except Exception as e:  # noqa: BLE001 — a bad image must not break the doc
            log.error("failed to embed diagram '%s' (skipping): %s", item["title"], e)


async def _attach_assets(client, sections: list[dict], context: dict,
                         asset_fns: dict) -> None:
    """Put approved, section-appropriate images on each drafted section.

    `asset_fns` supplies {"library": async () -> list[dict],
                          "download": async (storage_path, bucket) -> stream|None}.
    `bucket` is None for IV's own library and set for partner product images.

    Every image that reaches a document has been approved by a human, matched
    to the section by its vision description, and checked against the vendor
    being proposed. `architecture` assets are excluded upstream because they
    depict a single client's estate.
    """
    library = await asset_fns["library"](client) or []

    vendor = context.get("iam_vendor")
    vendors = context.get("iam_vendors")
    used: set[str] = set()
    placed = 0
    def key(a):
        return f"{a.get('bucket') or ''}/{a['storage_path']}"

    for sec in sections:
        sid = sec.get("id") or ""
        # The fixed kit first: IV's own slides and the proposed vendors'.
        kit = asset_selection.kit_for(sid, context)
        # One image appears once per document, however many sections it suits
        # -- the same picture twice reads as a mistake. Filter BEFORE selecting,
        # so a later section gets the next-best images instead of re-picking
        # the ones already placed and ending up with none. A section the kit
        # covers takes no keyword-matched IV-library images on top: that is how
        # two near-identical "Engagement Approach" slides landed in one section.
        pool = [a for a in library if key(a) not in used
                and not (kit and not a.get("vendor"))]
        chosen = kit + asset_selection.select_assets(
            pool, sid, vendor, iam_vendors=vendors, limit=_ASSETS_PER_SECTION)
        attached = []
        for a in chosen:
            if key(a) in used:
                continue
            stream = await asset_fns["download"](client, a["storage_path"], a.get("bucket"))
            if stream is None:
                continue
            used.add(key(a))
            attached.append({"id": a["id"], "stream": stream, "heading": a.get("heading"),
                             "near": a.get("near")})
        if attached:
            sec["assets"] = attached
            placed += len(attached)
    log.info("assets: %d image(s) placed across %d section(s)",
             placed, sum(1 for s in sections if s.get("assets")))



# Which drafted subsection each diagram belongs beside.
#
# IV embeds every diagram INSIDE the subsection that explains it: the deployment
# architecture diagram sits under "Proposed Deployment Architecture", right after
# the prose describing it. Shilpi collected all diagrams into a trailing
# "Solution Architecture Diagrams" section, so in run 9 a reader had to hold the
# text in their head and go looking forty pages later.
#
# Matched on the diagram's TYPE and title against the subsection heading, since
# the diagram plan and the template are written independently.
_DIAGRAM_PLACEMENT: tuple[tuple[str, str], ...] = (
    # Specific before generic: ESNAD 09-24 sent Integration, PAM and IGA to the
    # trailing section because only the joiner and solution patterns existed,
    # and "integration" was swallowed by the joiner rule.
    # {pam}/{iga}/{ciam}/{wiam} become the vendor that owns that domain in
    # THIS engagement (proposal_templates.engagement_domains), so a PAM diagram
    # lands under whichever vendor's overview covers PAM, for any vendor mix.
    # The stack opens the proposed solution, as IV19 does (ESNAD 09-28 put it
    # under "Why Ping Identity"). MSS has neither heading: trailing gallery.
    (r"\bstack\b", r"Proposed Future IAM State|Proposed Target Architecture"),
    # Not the scope section's "Privileged Access Management (PAM)" heading.
    (r"privileged|\bpam\b", r"^{pam} Solution Overview|Privileged(?! Access Management \(PAM\))"),
    (r"governance|certification|\biga\b", r"Access Certification|^{iga} Solution Overview"),
    (r"joiner|hrms|lifecycle|jml", r"HRMS Integration|Joiner"),
    (r"\bciam\b|customer|authentication|\bsso\b|federation",
     r"Integration with Identity Provider|^{ciam} Solution Overview|^{wiam} Solution Overview"),
    (r"integration", r"Connectors and Integrations|Application Onboarding"),
    (r"deployment", r"Proposed Deployment Architecture"),
    (r"security|network", r"Proposed Production Architecture"),
    (r"solution|reference|architecture", r"Proposed Future IAM State"),
    (r"migration|cutover", r"Migration Pattern|Proposed Target Architecture"),
)

def _placement_for(diagram: dict, owners: Optional[dict] = None) -> Optional[re.Pattern]:
    """The subsection-heading pattern this diagram should sit under, if any."""
    key = f"{diagram.get('diagram_type') or ''} {diagram.get('title') or ''}"
    for dpat, hpat in _DIAGRAM_PLACEMENT:
        if re.search(dpat, key, re.I):
            # An unknown owner never matches, rather than matching any overview.
            hpat = re.sub(r"\{(\w+)\}", lambda m: re.escape((owners or {}).get(m.group(1)) or "")
                          or "(?!)", hpat)
            return re.compile(hpat, re.I)
    return None


def _claim_diagram(embeddable: list[dict], heading: str, used: set,
                   owners: Optional[dict] = None) -> list[dict]:
    """Every diagram belonging under this subsection heading, each once. One
    vendor overview can explain two diagrams (IGA and PAM on one platform)."""
    out = []
    for item in embeddable:
        if id(item) in used:
            continue
        pattern = _placement_for(item, owners)
        if pattern and pattern.search(heading or ""):
            used.add(id(item))
            out.append(item)
    return out


def _embed_diagram(document: Document, item: dict) -> None:
    try:
        _add_picture_fitted(document, item["stream"],
                            max_w=_DIAGRAM_MAX_W, max_h=_DIAGRAM_MAX_H)
    except Exception as e:  # noqa: BLE001 - a bad render must not sink the section
        log.warning("could not embed diagram %s: %s", item.get("title"), e)
        return
    # The colour key, under the picture: IV's diagrams carry a legend; ours
    # colour by vendor and say so here rather than inside the image.
    if item.get("legend"):
        cap = document.add_paragraph()
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = cap.add_run(item["legend"])
        run.italic = True
        run.font.size = Pt(9)


def _render_section_assets(document: Document, sec: dict) -> None:
    """Embed the approved images chosen for this section.

    Only assets a human has approved reach this point, and only `corporate` and
    `product` kinds -- `architecture` assets depict a specific client's estate
    and are excluded in asset_selection.py.

    No captions: they were generated from the vision description and leaked
    another client's project name into run 8.
    """
    for asset in sec.get("assets") or []:
        if asset.get("heading") is not None:
            continue  # placed under its subsection heading instead
        _embed_asset(document, asset)


def _add_sub_content(document: Document, content: str, near_assets: list,
                     placed: set) -> None:
    """Subsection body, with each kit image under the first of the model's own
    capability headings that it matches ("near"), instead of all of them piled
    up after the last heading."""
    if not near_assets:
        _add_body_paragraphs(document, content)
        return
    for chunk in re.split(r"(?m)^(?=#{1,6}\s)", content or ""):
        if not chunk.strip():
            continue
        _add_body_paragraphs(document, chunk)
        head = re.match(r"\s*#{1,6}\s+(.*)", chunk)
        if not head:
            continue
        for asset in near_assets:
            if id(asset) not in placed and asset["near"].search(head.group(1)):
                placed.add(id(asset))
                _embed_asset(document, asset)


def _embed_asset(document: Document, asset: dict) -> None:
    stream = asset.get("stream")
    if not stream:
        return
    # Kit images are full slides and architecture diagrams: page width, like
    # IV's. Library images are supporting pictures and stay smaller.
    kit = str(asset.get("id") or "").startswith("kit:")
    try:
        _add_picture_fitted(document, stream,
                            max_w=_DIAGRAM_MAX_W if kit else _IMAGE_MAX_W,
                            max_h=_DIAGRAM_MAX_H if kit else _IMAGE_MAX_H,
                            drop_if_small=True)
    except Exception as e:  # noqa: BLE001 - a bad image must not sink a section
        log.warning("could not embed asset %s: %s", asset.get("id"), e)


def assemble_docx(
    metadata: dict,
    sections: list[dict],
    compliance_markdown: Optional[str] = None,
    client_logo_path: Optional[str] = None,
    include_appendices: bool = False,
    diagrams: Optional[list[dict]] = None,
) -> bytes:
    """Build a professional, IV-branded Word document and return its bytes.

    metadata keys: client_name, proposal_type, iam_vendor (optional),
    generated_at (optional), version (optional).

    client_logo_path: optional path to a client logo image embedded in the
    title-page box. When None (default) a bordered "Client Logo" placeholder is
    drawn instead. Logos are never sourced online in this pass.

    include_appendices: when True (full depth) appends the appendix pack —
    RACI, timeline, sizing, integration inventory and risks — as real DOCX
    tables. Where intake/retrieval data is absent, conservative
    assumption-marked placeholder rows are used (never fabricated specifics).

    diagrams (Pass 4): optional list of architecture-diagram dicts. ONLY diagrams
    whose status == 'approved' AND that carry a rendered image (``image_bytes``
    or a readable ``image_path``) are embedded — draft/rejected/needs_review
    diagrams are silently skipped. When no diagram qualifies, the document is
    byte-for-byte the same as before (Pass 1-3 unchanged).
    """
    # Which vendor owns each capability domain: diagram placement follows it.
    _owners = engagement_domains(metadata.get("iam_vendor") or "",
                                 metadata.get("discovery_answers") or {})["domain_vendors"]
    metadata = {
        **metadata,
        "generated_at": metadata.get("generated_at")
        or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

    document = Document()
    branding.configure_base_styles(document)
    branding.apply_header_footer(document, metadata.get("client_name") or "Client",
                                 client_logo_path=client_logo_path)

    # --- Title page (IV branding) ------------------------------------------
    branding.add_title_page(document, metadata, client_logo_path=client_logo_path)
    document.add_page_break()

    # --- Table of contents (real, refreshable Word TOC field) --------------
    # NOT a section heading: a contents page that lists itself as section 1 is
    # a Shilpi artefact, not something IV's proposals do. Styled to match, but
    # outside the heading hierarchy so it stays out of the contents it renders.
    _toc_title = document.add_paragraph()
    _toc_run = _toc_title.add_run("Table of Contents")
    _toc_run.bold = True
    _toc_run.font.size = Pt(16)
    _toc_run.font.color.rgb = branding.NAVY
    _toc_extra = [t for t in (
        # Only advertise the gallery if a diagram will actually land in it.
        # _placed_diagrams is populated during section rendering, which happens
        # after the contents page is written, so this is computed the same way.
        "Solution Architecture Diagrams" if _unplaced_diagrams(diagrams, sections, _owners) else None,
        "Compliance Matrix" if any(
            s.get("id") == COMPLIANCE_SECTION_ID for s in sections) else None,
    ) if t]
    # Bookmark names come BACK from the contents builder, so a heading and its
    # contents entry cannot drift apart.
    _toc_bookmarks = _add_static_toc(document, sections, _toc_extra) or {}

    # Sections the scope filter removed, named on the page. Run 15 dropped
    # Decommissioning, Knowledge Transfer and Project Timeline and said nothing,
    # so a deliberately scoped proposal looked like a truncated one. A reviewer
    # has to be able to tell the difference, and to push back if a section
    # should have been kept.
    _dropped = metadata.get("dropped_sections") or []
    if _dropped:
        note = document.add_paragraph()
        run = note.add_run("Sections omitted for this engagement")
        run.bold = True
        run.font.size = Pt(10)
        run.font.color.rgb = branding.NEUTRAL_MUTED
        for sid, reason in _dropped:
            line = document.add_paragraph(style="List Bullet")
            r = line.add_run(f"{sid.replace('_', ' ').title()} — {reason}")
            r.font.size = Pt(9)
            r.font.color.rgb = branding.NEUTRAL_MUTED

    document.add_page_break()

    # --- Sections -----------------------------------------------------------
    # Diagrams are placed INSIDE the subsection that explains them, the way IV
    # does it. Whatever no subsection claims still gets the trailing gallery, so
    # an unmatched diagram is never silently dropped.
    _inline_diagrams = _embeddable_diagrams(diagrams, sections)
    _placed_diagrams: set = set()
    _placed_assets: set = set()

    aggregated_assumptions: list[str] = []
    for sec in sections:
        _sec_title = sec.get("title", "Untitled")
        heading = branding.add_section_heading(document, _sec_title)
        if _sec_title in _toc_bookmarks:
            _bookmark_paragraph(heading, _toc_bookmarks[_sec_title])
        if sec.get("needs_sme_review"):
            flag = heading.add_run("   [SME REVIEW REQUIRED]")
            flag.font.size = Pt(10)
            flag.font.color.rgb = _DRAFT_COLOR
        # Reusable images sit directly under the section heading they were
        # chosen for. They used to render AFTER the whole section body, so an
        # image appeared beneath whatever the last subsection heading happened
        # to be -- run 9 put a CIAM governance pyramid under "Assumptions &
        # Open Questions" and a change-control graphic under "Training and
        # Post-Production Support". The selection was right; the position made
        # it read as wrong.
        _render_section_assets(document, sec)

        subs = sec.get("subsections") or []
        if subs:
            # Multi-subsection (full depth): render each facet under an H2 heading.
            for sub in subs:
                # An EMPTY title means "continuous prose under the section
                # heading", which is how IV writes its executive summary. Adding
                # an empty H2 would put a blank line in the table of contents.
                title = (sub.get("title") or "").strip()
                if title:
                    _h2 = document.add_heading(title, level=2)
                    if title in _toc_bookmarks:
                        _bookmark_paragraph(_h2, _toc_bookmarks[title])
                mine = [x for x in (sec.get("assets") or [])
                        if x.get("heading") is not None and id(x) not in _placed_assets
                        and x["heading"].search(title)]
                _add_sub_content(document, sub.get("content", ""),
                                 [x for x in mine if x.get("near")], _placed_assets)
                # The diagram that explains THIS subsection, immediately after
                # the prose describing it -- the way IV places them.
                for claimed in _claim_diagram(_inline_diagrams, title,
                                              _placed_diagrams, _owners):
                    _embed_diagram(document, claimed)
                # Kit images for this subsection not already placed under one
                # of its capability headings, after its diagram.
                for asset in mine:
                    if id(asset) not in _placed_assets:
                        _placed_assets.add(id(asset))
                        _embed_asset(document, asset)
        else:
            _add_body_paragraphs(document, sec.get("content", ""))

        # A kit image whose subsection this engagement does not have (a
        # different template, a dropped subsection) still lands in its section.
        for asset in sec.get("assets") or []:
            if asset.get("heading") is not None and id(asset) not in _placed_assets:
                _placed_assets.add(id(asset))
                _embed_asset(document, asset)

        # Opportunistically collect assumption-ish lines for the aggregate section.
        if "assumption" not in (sec.get("id") or "").lower():
            for line in (sec.get("content") or "").splitlines():
                s = line.strip().lstrip("-*• ").strip()
                if s and re.match(r"(?i)^(assumption|assume|open question|dependency)", s):
                    aggregated_assumptions.append(s)

    # --- Assumptions & Open Questions (ensure one always exists) ------------
    has_dedicated_assumptions = any("assumption" in (s.get("id") or "").lower() for s in sections)
    if not has_dedicated_assumptions:
        branding.add_section_heading(document, "Assumptions & Open Questions")
        if aggregated_assumptions:
            for a in aggregated_assumptions:
                document.add_paragraph(a, style="List Bullet")
        else:
            document.add_paragraph(
                "No explicit assumptions were captured during drafting. "
                "An SME should confirm scope, dependencies, and open questions before client use.",
                style="List Bullet",
            )

    # --- Architecture Diagrams (Pass 4 — approved only) --------------------
    _add_approved_diagrams(document, _inline_diagrams, already_placed=_placed_diagrams,
                           toc_bookmarks=_toc_bookmarks)

    # --- Compliance Matrix (optional) --------------------------------------
    if compliance_markdown:
        document.add_page_break()
        # H2: a requirement-coverage matrix is a supporting artefact, not one
        # of the proposal's top-level sections.
        document.add_heading("Compliance Matrix", level=2)
        _add_markdown_ish(document, compliance_markdown)

    # --- Citation Appendix: REMOVED -----------------------------------------
    # This listed every retrieved corpus chunk by CLIENT NAME with a similarity
    # score. In the Amlak run 3 document it named Al Qadsiah Club 51 times, plus
    # Ministry of Energy and National Water Company: three other IV clients, in
    # a document addressed to a fourth. 78 paragraphs of it.
    #
    # The directive was already "citations are removed completely" — the inline
    # [N] markers were stripped but the appendix they pointed at was left
    # behind, which is the same built-but-never-finished shape as the rest.
    # Retrieval provenance belongs in logs, not in a client deliverable.

    # --- Appendices (full depth only) --------------------------------------
    # The appendix pack predates the template rebuild, when the body was seven
    # generic sections with no RACI, timeline, sizing or commercial content.
    # The body now carries all four as real tables, so run 5 printed each twice
    # with DIFFERENT numbers: a 25-row RACI built from discovery answers in the
    # body, and a 9-row generic placeholder one in Appendix A. Two versions of
    # the same table in one proposal is worse than neither, because a reader
    # cannot tell which is authoritative.
    if include_appendices:
        _add_appendices(document, metadata,
                        skip=_superseded_appendices({s.get("id") for s in sections}))

    # PAGEREF fields in the contents table resolve when the reader opens the
    # file, so the page-number column is never a placeholder.
    _enable_field_update_on_open(document)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _add_markdown_ish(document: Document, text: str) -> None:
    """Render the compliance matrix markdown into DOCX in a robust, simple way.

    We don't build a full markdown parser: headings (#) become paragraphs, GFM
    tables become native Word tables, everything else becomes plain paragraphs.
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # GFM table block — same parser/renderer the section body path uses, so
        # a fix to one is a fix to both.
        parsed = _parse_gfm_table(lines, i)
        if parsed is not None:
            headers, rows, i = parsed
            _add_gfm_table(document, headers, rows)
            continue
        if stripped.startswith("#"):
            para = document.add_paragraph()
            run = para.add_run(stripped.lstrip("# ").strip())
            run.bold = True
            i += 1
            continue
        if stripped.startswith(">"):
            note = document.add_paragraph()
            nr = note.add_run(stripped.lstrip("> ").strip())
            nr.italic = True
            i += 1
            continue
        document.add_paragraph(stripped)
        i += 1


# ---------------------------------------------------------------------------
# Appendices (Pass 3 — full depth). Deterministic, no LLM calls, no fabrication.
# Missing intake/retrieval data yields conservative assumption-marked rows.
# ---------------------------------------------------------------------------

_APPENDIX_ASSUMPTION = "[ASSUMPTION]"


def _appendix_note(document: Document, text: str) -> None:
    note = document.add_paragraph()
    run = note.add_run(text)
    run.italic = True
    run.font.size = Pt(10)


def _appendix_table(document: Document, headers: list[str], rows: list[list[str]]) -> None:
    """Render a native Word table with a bold header row."""
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    for j, h in enumerate(headers):
        run = table.rows[0].cells[j].paragraphs[0].add_run(h)
        run.bold = True
    for row in rows:
        cells = table.add_row().cells
        for j in range(len(headers)):
            cells[j].text = row[j] if j < len(row) else ""


# Appendix letter -> the body section that now carries the same content.
_APPENDIX_SUPERSEDED_BY = {
    "A": "implementation_approach",   # RACI matrix
    "B": "project_timeline",          # indicative timeline
    "C": "proposed_solution",         # sizing & volumetrics
    "E": "implementation_approach",   # risk register -- "Initial Project RAID
                                       # Log" (Sprint 3) covers Risk/Assumption/
                                       # Issue/Dependency, discovery-grounded,
                                       # not the generic boilerplate below
    "F": "commercial",                # commercial structure
}


def _superseded_appendices(body_section_ids: set) -> frozenset:
    """Appendices the body template already covers, so they must not print twice.

    D (integration inventory) has no body counterpart and is always kept.
    """
    return frozenset(letter for letter, sec_id in _APPENDIX_SUPERSEDED_BY.items()
                     if sec_id in body_section_ids)


def _add_appendices(document: Document, metadata: dict,
                    skip: frozenset = frozenset()) -> None:
    """Append the full-depth appendix pack as real DOCX sections/tables.

    Every row is either grounded in supplied metadata or an explicit
    ``[ASSUMPTION]`` placeholder — SMEs must confirm before client use. No
    specific figures, dates or SLAs are fabricated.
    """
    vendor = (metadata.get("iam_vendor") or "the selected IAM platform").strip() or "the selected IAM platform"
    ptype = (metadata.get("proposal_type") or "implementation").strip().lower()

    if len(skip) >= 6:
        return

    # Rather than reindenting six large literal blocks behind guards, the emit
    # helpers below no-op while the current appendix is in `skip`. Each block
    # sets `_cur` first. Fewer moving parts than restructuring, and the skip
    # decision stays in one place.
    _cur = [""]

    def _heading(text: str) -> None:
        # Level 2, not a section heading. "Appendices" is the H1; A through F sit
        # UNDER it. Emitting each as an H1 gave run 9 eighteen top-level
        # sections against IV's eleven, and a table of contents listing
        # "Appendices", "Appendix D", "Appendix E" and "Appendix F" as peers.
        if _cur[0] not in skip:
            document.add_heading(text, level=2)

    def _table(_doc, cols, rows) -> None:
        # Signature mirrors _appendix_table so the call sites are unchanged.
        if _cur[0] not in skip:
            _appendix_table(_doc, cols, rows)

    def _note(text: str) -> None:
        if _cur[0] not in skip:
            _appendix_note(document, text)

    document.add_page_break()
    branding.add_section_heading(document, "Appendices")
    _appendix_note(
        document,
        "The following appendices are structured planning artefacts. Rows marked "
        f"{_APPENDIX_ASSUMPTION} are conservative placeholders to be confirmed with the "
        "client and an SME during discovery — they are not commitments.",
    )

    # A. RACI matrix
    _cur[0] = "A"
    _heading("Appendix A — RACI Matrix")
    _table(
        document,
        ["Activity / Workstream", "InspiritVision", "Client", "Vendor"],
        [
            ["Solution design & architecture", "R/A", "C", "C"],
            ["Environment provisioning", "C", "R/A", "C"],
            ["Configuration & build", "R/A", "C", "I"],
            ["Integration & connector setup", "R/A", "C", "C"],
            ["Testing & UAT", "R", "A", "I"],
            ["Go-live & cutover", "R/A", "C", "I"],
            ["Knowledge transfer & handover", "R/A", "C", "I"],
            [f"{_APPENDIX_ASSUMPTION} Additional workstreams", "TBC", "TBC", "TBC"],
        ],
    )
    _note("R = Responsible, A = Accountable, C = Consulted, I = Informed.")

    # B. Timeline / phasing
    _cur[0] = "B"
    _heading("Appendix B — Indicative Timeline")
    _dur = " ".join(str((metadata.get("discovery_answers") or {}).get("duration") or "").split())
    if _dur and _dur.lower() not in ("skip", "none", "n/a", "-"):
        _note(f"Client-supplied engagement duration: {_dur}.")
    _table(
        document,
        ["Phase", "Key Activities", "Indicative Duration"],
        [
            ["Discovery & Design", "Requirements, current-state review, target design", f"{_APPENDIX_ASSUMPTION} TBC"],
            ["Build & Configure", "Platform config, connectors, workflows", f"{_APPENDIX_ASSUMPTION} TBC"],
            ["Test & Validate", "SIT, UAT, remediation", f"{_APPENDIX_ASSUMPTION} TBC"],
            ["Deploy & Stabilise", "Cutover, hypercare, handover", f"{_APPENDIX_ASSUMPTION} TBC"],
        ],
    )
    _note("Durations are confirmed once scope and volumetrics are baselined in discovery.")

    # C. Sizing
    _cur[0] = "C"
    _heading("Appendix C — Sizing & Volumetrics")
    # Use what discovery actually captured. These rows used to be hardcoded
    # "TBC / Client to confirm" even when the consultant had supplied exact
    # figures at intake — the same discard bug as the drafting path.
    _ans = metadata.get("discovery_answers") or {}

    def _row(label: str, *keys: str) -> list[str]:
        for k in keys:
            v = " ".join(str(_ans.get(k) or "").split())
            if v and v.lower() not in ("skip", "none", "n/a", "na", "-"):
                return [label, v, "Client-supplied at discovery"]
        return [label, f"{_APPENDIX_ASSUMPTION} TBC", "Client to confirm"]

    _rows = [
        _row("Identities / users", "user_count"),
        # Intake ids, not invented names: "environments" and
        # "applications_to_onboard" never existed, so ESNAD printed TBC over
        # "Dev, Test, and Prod". test_appendix_reads_real_intake_ids pins this.
        _row("Target applications", "app_count", "apps_to_onboard"),
        _row("Environments", "envs"),
        _row("Deployment model", "deployment_model"),
        _row("Hardware sizing", "hardware_sizing_inputs"),
        _row("Cluster topology", "cluster_topology"),
        _row("HA / DR", "ha_dr_requirements"),
        _row("Availability target", "availability"),
        _row("Peak transaction volume", "performance"),
    ]
    _appendix_table(document, ["Dimension", "Value", "Source"], _rows)

    # D. Integration inventory
    _cur[0] = "D"
    _heading("Appendix D — Integration Inventory")
    _int_rows = _integration_rows(_ans.get("target_integrations"))
    _table(
        document,
        ["System / Application", "Integration Type", f"{vendor} Connector", "Notes"],
        _int_rows or [
            ["Directory / HR source", "Authoritative source", f"{_APPENDIX_ASSUMPTION} TBC", "System of record for identities"],
            ["Core business applications", "Provisioning target", f"{_APPENDIX_ASSUMPTION} TBC", "Confirm inventory in discovery"],
            ["Downstream / custom apps", f"{_APPENDIX_ASSUMPTION} TBC", f"{_APPENDIX_ASSUMPTION} TBC", "May require custom connector"],
        ],
    )

    # E. Risks
    _cur[0] = "E"
    _heading("Appendix E — Risk Register")
    _table(
        document,
        ["Risk", "Likelihood", "Impact", "Mitigation"],
        [
            ["Scope / requirements change", "Medium", "High", "Change control, phased delivery"],
            ["Application onboarding delays", "Medium", "Medium", "Early inventory, prioritised backlog"],
            ["Data quality in source systems", "Medium", "High", "Data-quality assessment in discovery"],
            ["Resource / SME availability", "Medium", "Medium", "Agreed RACI and governance cadence"],
            [f"{_APPENDIX_ASSUMPTION} {ptype}-specific risks", "TBC", "TBC", "To be assessed with client"],
        ],
    )

    # F. Commercial structure. The human proposals always carry one, and the
    _cur[0] = "F"
    # milestone basis IS captured at discovery. Figures stay with the human.
    _c = metadata.get("discovery_answers") or {}
    _crows = []
    for label, key in (("Licence included", "license_included"),
                       ("Pricing model", "pricing_model"),
                       ("Payment milestones", "payment_milestones"),
                       ("Taxes", "taxes"),
                       ("Travel", "travel"),
                       ("Support terms", "support_terms")):
        val = " ".join(str(_c.get(key) or "").split())
        if val and val.lower() not in ("skip", "none", "n/a", "na", "-"):
            _crows.append([label, val])
    if _crows:
        # Through the wrappers, not around them. Calling
        # branding.add_section_heading / _appendix_table directly here bypassed
        # BOTH the H2 demotion and the skip logic, so run 10 shipped
        # "Appendix F" as a top-level heading beside "Appendices" while A-E sat
        # correctly beneath it.
        _heading("Appendix F — Commercial Structure")
        _note("Structure as captured at discovery. Pricing figures are "
              "deliberately omitted and must be completed by the commercial "
              "owner before issue.")
        _table(document, ["Item", "Basis"], _crows)


def _integration_rows(value) -> list[list[str]]:
    """Appendix D rows from the `target_integrations` answer.

    "Nafath (Citizen authentication, SSO); GIS (Esri Geoportal, Maps) (SSO)"
    -> one row per system, the trailing parenthetical as its integration type.
    ESNAD named nine systems and the appendix printed three TBC rows.
    """
    rows = []
    for item in re.split(r";|\n", str(value or "")):
        item = item.strip(" .-")
        if not item or item.lower() in ("skip", "none", "n/a"):
            continue
        m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", item)
        name, kind = (m.group(1), m.group(2)) if m and m.group(1) else (item, "To be confirmed in discovery")
        rows.append([name.strip(), kind.strip(), "To be confirmed in discovery",
                     "Client-supplied at discovery"])
    return rows


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def generate_proposal(
    client: httpx.AsyncClient,
    rfp_text: str,
    client_name: str,
    proposal_type: str,
    iam_vendor: Optional[str] = None,
    *,
    embed_fn: EmbedFn,
    retrieve_fn: RetrieveFn,
    build_grounded_system_fn: BuildSystemFn,
    run_compliance_matrix_fn: Optional[Callable[..., Awaitable[object]]] = None,
    render_matrix_markdown_fn: Optional[Callable[[object], str]] = None,
    sections: Optional[list[str]] = None,
    include_compliance_matrix: bool = False,
    top_k: int = TOP_K,
    proposal_depth: Optional[str] = None,
    diagrams: Optional[list[dict]] = None,
    discovery_answers: Optional[dict] = None,
    client_logo_path: Optional[str] = None,
    asset_fns: Optional[dict] = None,
    scale_fn: Optional[Callable] = None,
    retrieve_product_fn=None,
    build_product_evidence_fn=None,
    references_fn: Optional[Callable] = None,
) -> dict:
    """Orchestrate: pick template, draft sections concurrently, assemble DOCX.

    The brain helpers are injected so this module never imports app.py.

    ``proposal_depth`` (brief|standard|full) controls long-form depth via
    STRUCTURED fan-out — retrieval queries + independent drafting calls per
    section, plus (full) an appendix pack. Unknown/absent values fall back to the
    safe ``standard`` tier, preserving existing behaviour.
    Returns {"docx_bytes", "sections_meta", "filename", ...}.
    """
    template = get_template(proposal_type)  # raises ValueError on bad type
    tier: DepthTier = get_depth_tier(proposal_depth)

    # Choose which sections to draft. Compliance is opt-in and handled separately.
    if sections:
        wanted = {str(s).strip().lower() for s in sections}
        # A name that matches nothing is refused, not ignored: it used to fall
        # back to the WHOLE template, so a typo bought a full paid draft.
        unknown = wanted - {s.id for s in template} - {s.title.lower() for s in template}
        if unknown:
            raise ValueError(
                f"unknown section(s): {', '.join(sorted(unknown))}. Valid ids for "
                f"{proposal_type}: {', '.join(s.id for s in template)}")
        chosen = [s for s in template if s.id in wanted or s.title.lower() in wanted]
    else:
        chosen = [s for s in template if not s.optional]

    # Drop sections the client's own answers rule out. Measured on the first
    # migration proposal (BTPN, a scoped ForgeRock upgrade): Shilpi produced
    # 6,141 prose words against IV's 2,642 for the same deal, including a
    # Decommissioning section for an in-place version upgrade and a Knowledge
    # Transfer section the consultant had marked out of scope. A section the
    # answers exclude is worse than a missing one: it tells a reviewer the
    # document was not read.
    # Judge engagement scale ONCE, before any filtering, and record it on the
    # answers so every downstream filter reads the same decision. The keyword
    # heuristic in scope_filter stays as the offline fallback; it misread BTPN
    # in run 17 by treating the word "production" as inclusion when the
    # sentence excluded it.
    if scale_fn and discovery_answers is not None:
        try:
            _scale, _why = await scale_fn(discovery_answers)
            if _scale:
                discovery_answers = dict(discovery_answers)
                discovery_answers[scope_filter.SCALE_ANSWER_KEY] = _scale
                log.info("engagement scale: %s (%s)", _scale, _why[:100])
        except Exception as e:  # noqa: BLE001 - heuristic still applies
            log.warning("scale judgement failed: %s", e)

    chosen, _dropped_sections = scope_filter.select_sections(
        chosen, discovery_answers)

    draft_specs = [s for s in chosen if s.id != COMPLIANCE_SECTION_ID]

    # A multi-vendor answer ("Ping Identity ... and Saviynt ...") is split into
    # a list so per-vendor headings and retrieval queries can render/query
    # cleanly for EACH vendor instead of once with a diluted combined string.
    # Single-vendor proposals get a one-element list — everything downstream
    # that reads iam_vendors falls back to iterating once, unchanged.
    _vendors = split_vendors(iam_vendor)
    _is_saas = _looks_like_saas((discovery_answers or {}).get("deployment_model"))
    context = {
        "client_name": client_name,
        "iam_vendor": iam_vendor or "",
        "iam_vendors": _vendors,
        "proposal_type": proposal_type,
        "rfp_text": rfp_text or "",
        "discovery_answers": discovery_answers or {},
        # Drives the sizing-table instructions in proposal_templates.py.
        # Previously each of Production/DR/UAT/Development guessed its OWN
        # relationship to the others with no shared signal: ESNAD's proposal
        # shipped with Production correctly saying N/A (vendor-managed SaaS),
        # DR garbling "mirrors production" against cells that had nothing to
        # mirror, and Development inventing on-prem-style 4 vCPU / 16 GB
        # specs for a product that is never deployed on IV/client hardware.
        # One shared flag, one shared decision, across all four.
        "is_saas": _is_saas,
        # Which capability domains are in scope and which vendor owns each:
        # scope and solution structure follow them for any vendor mix.
        **engagement_domains(iam_vendor or "", discovery_answers or {}),
    }
    if references_fn:
        try:
            context["delivered_references"] = await references_fn(client)
        except Exception as e:  # noqa: BLE001 - Similar Experience falls back to partner status
            log.warning("delivered references unavailable: %s", e)
    if len(_vendors) > 1:
        log.info("multi-vendor proposal: %s", _vendors)

    sem = asyncio.Semaphore(DOC_CONCURRENCY)

    async def _draft(spec: SectionSpec) -> dict:
        async with sem:
            return await draft_section(
                client, spec, context,
                embed_fn=embed_fn,
                retrieve_fn=retrieve_fn,
                build_grounded_system_fn=build_grounded_system_fn,
                top_k=top_k,
                fanout=tier.retrieval_fanout,
                subsections=tier.subsections_per_section,
                max_tokens=tier.per_call_max_tokens,
                retrieve_product_fn=retrieve_product_fn,
                build_product_evidence_fn=build_product_evidence_fn,
            )

    drafted = await asyncio.gather(*[_draft(s) for s in draft_specs])

    # Optional compliance matrix.
    compliance_markdown: Optional[str] = None
    compliance_to_confirm: list[str] = []
    compliance_unassessed = 0
    compliance_text_cut = False
    compliance_breakdown: dict = {}
    want_compliance = include_compliance_matrix or (
        sections is not None and COMPLIANCE_SECTION_ID in {str(s).strip().lower() for s in sections}
    )
    if want_compliance and run_compliance_matrix_fn and render_matrix_markdown_fn:
        # Prefer requirements already extracted from the source RFP (vision-
        # extracted per-page, with original ref ids and page numbers intact)
        # over re-deriving them from rfp_text via a second LLM call. This
        # matters most for a SCANNED RFP with no text layer: rfp_text is
        # legitimately empty for those, so the re-derivation path would find
        # nothing to extract from, and the first live run against one (ESNAD)
        # shipped a proposal with zero requirement citations as a direct
        # result. See app.py's RFP-upload handler, which persists this list.
        _extracted_reqs = None
        _raw_reqs = (discovery_answers or {}).get("extracted_requirements")
        if _raw_reqs:
            try:
                import json as _json
                _parsed = _json.loads(_raw_reqs) if isinstance(_raw_reqs, str) else _raw_reqs
                # Plain dicts, not a Requirement instance: document_engine.py
                # cannot import app.Requirement without an import cycle (app.py
                # already imports generate_proposal FROM this module). The
                # dict is turned into a real Requirement on the other side, in
                # run_compliance_matrix, the one place both the class and this
                # data can meet without the cycle.
                _extracted_reqs = [
                    {"id": r.get("id") or r.get("ref") or "",
                     "text": r.get("text") or "", "category": r.get("category")}
                    for r in _parsed if (r.get("text") or "").strip()
                ] or None
            except Exception as e:  # noqa: BLE001 - fall back to rfp_text re-derivation
                log.warning("could not parse extracted_requirements: %s", e)
        try:
            matrix = await run_compliance_matrix_fn(
                client, rfp_text, _extracted_reqs, top_k)
            compliance_unassessed = int(getattr(matrix, "unassessed", 0) or 0)
            compliance_text_cut = bool(getattr(matrix, "text_cut", False))
            if getattr(matrix, "entries", None):
                _refs = requirement_references(matrix.entries, list(drafted))
                compliance_markdown, compliance_to_confirm = render_client_matrix(
                    matrix.entries, _refs)
                compliance_breakdown = matrix_breakdown(matrix.entries, _refs)
                log.info("compliance matrix by status (rows, with a section "
                         "reference): %s", compliance_breakdown)
            else:
                compliance_markdown = render_matrix_markdown_fn(matrix)
        except Exception as e:
            log.error("Compliance matrix generation failed: %s", e)
            # The exception text stays in the log: it can carry model output
            # fragments, and this string ships inside the client document.
            compliance_markdown = (
                f"{SME_REVIEW_MARKER}: compliance matrix generation failed. "
                "Run the /v1/compliance-matrix endpoint separately."
            )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    metadata = {
        "client_name": client_name,
        "proposal_type": proposal_type,
        "iam_vendor": iam_vendor,
        "generated_at": generated_at,
        # Appendices read captured figures from here instead of printing "TBC"
        # over data the consultant already supplied.
        "discovery_answers": discovery_answers or {},
    }
    # Attach reusable images to each section before assembly. Selection is
    # pure (asset_selection.py); fetching bytes is the caller's job, passed in
    # as `asset_fns` so document_engine keeps no Supabase dependency -- the same
    # arrangement as retrieve_fn and embed_fn.
    if asset_fns:
        try:
            await _attach_assets(client, list(drafted), context, asset_fns)
        except Exception as e:  # noqa: BLE001
            # A proposal without images is a worse proposal, not a failed one.
            log.warning("asset attachment failed, drafting without images: %s", e)

    # Sections omitted by the scope filter are recorded IN the document. Run 15
    # dropped three and said nothing, which makes a deliberately scoped-down
    # proposal indistinguishable from a broken one -- the exact failure the
    # filter was supposed to avoid.
    metadata = dict(metadata)
    metadata["dropped_sections"] = _dropped_sections
    # Approved diagrams the assembler will leave out as duplicates, by title.
    diagrams_left_out: list[str] = []
    _embeddable_diagrams(diagrams, list(drafted), diagrams_left_out)

    docx_bytes = assemble_docx(
        metadata, list(drafted), compliance_markdown,
        client_logo_path=client_logo_path,
        include_appendices=tier.include_appendices,
        diagrams=diagrams,
    )

    safe_client = re.sub(r"[^A-Za-z0-9]+", "_", client_name).strip("_") or "Client"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"Shilpi_Proposal_{safe_client}_{stamp}.docx"

    sections_meta = [
        {
            "id": d["id"],
            "title": d["title"],
            "max_similarity": d["max_similarity"],
            "needs_sme_review": d["needs_sme_review"],
            "citation_count": len(d.get("citations") or []),
            "stripped_asides": d.get("stripped_asides", 0),
        }
        for d in drafted
    ]
    stripped_asides = sum(m["stripped_asides"] for m in sections_meta)
    if stripped_asides:
        log.info("review asides stripped: %d in total (%s)", stripped_asides,
                 ", ".join(f"{m['id']} {m['stripped_asides']}" for m in sections_meta
                           if m["stripped_asides"]))
    draft_markdown = "\n\n".join(
        f"## {d['title']}\n\n{d.get('content', '')}".rstrip() for d in drafted
    )

    return {
        "docx_bytes": docx_bytes,
        "sections_meta": sections_meta,
        "dropped_sections": _dropped_sections,
        "draft_markdown": draft_markdown,
        "filename": filename,
        "included_compliance_matrix": compliance_markdown is not None,
        "compliance_to_confirm": compliance_to_confirm,
        "compliance_unassessed": compliance_unassessed,
        "compliance_text_cut": compliance_text_cut,
        "compliance_breakdown": compliance_breakdown,
        "diagrams_left_out": diagrams_left_out,
        "stripped_asides": stripped_asides,
        "proposal_depth": tier.name,
        "included_appendices": tier.include_appendices,
    }
