"""
Sprint 5 — proposal section templates.

Ordered section definitions for the two proposal types Shilpi produces:
  - "implementation" : a delivery/implementation proposal
  - "mss"            : a managed support services proposal

Each section carries a Jinja2 ``query_template`` that renders into a retrieval
query (used to embed + fetch grounded evidence from the proposal corpus). The
render context provides: client_name, iam_vendor, proposal_type, rfp_text.

This module has NO dependency on app.py or the network — it is pure data +
Jinja2 rendering, safe to import from the smoke test without any secrets.
"""

import re
from dataclasses import dataclass
from typing import Optional

import re

from jinja2 import Template

# Multi-vendor engagements are real: ESNAD asked for Ping Identity (Access
# Management, CIAM) AND Saviynt (IGA, PAM) as two separate platforms in one
# proposal, and the system had no way to represent that — iam_vendor was a
# single string, templated verbatim into 47 places across this file, producing
# headings like "Why Ping Identity (Access Management, CIAM) and Saviynt (IGA,
# PAM)" and retrieval queries that diluted vector search against BOTH vendors'
# corpus content at once instead of hitting either cleanly.
#
# split_vendors() is DELIBERATELY CONSERVATIVE. A single vendor name is never
# more valuable to split incorrectly than a genuine multi-vendor answer is to
# leave combined — a wrongly-split single vendor ("Micro Focus" ->
# ["Micro", "Focus"]) corrupts every retrieval query in the proposal, while a
# wrongly-combined multi-vendor answer just falls back to today's (known,
# imperfect) single-string behaviour. So this only splits on an explicit " and "
# or a comma, and only when what results actually looks like a set of distinct
# product names rather than a single vendor's own multi-word name.
_VENDOR_SPLIT_RE = re.compile(r"\s*(?:,\s*(?:and\s+)?|\s+and\s+)\s*", re.I)

# A handful of known product/vendor names where "and" is part of the name
# itself, not a separator between two vendors. Matched against the WHOLE
# string, not a substring search -- "Ping Identity and Saviynt" contains the
# substring "Identity and" (from "Ping IdentITY AND Saviynt") without being
# one of these names, and a substring match on that phrase silently blocked
# a genuine two-vendor split. Anchored to ^...$ so only an exact name match
# suppresses splitting.
_KNOWN_AND_NAMES = (
    r"identity\s+and\s+access\s+management(\s+inc)?",
    r"research\s+and\s+markets",
)
_VENDOR_AND_IS_PART_OF_NAME = re.compile(
    r"^(?:" + "|".join(_KNOWN_AND_NAMES) + r")$", re.I)

# "Ping Identity FOR Access Management and CIAM, Saviynt FOR IGA and PAM" --
# the actual live ESNAD answer, and the one split_vendors got wrong. No
# parentheses, so the old code had nothing to protect the capability list
# from being treated as more vendors: "and"/"," split the whole string flat,
# producing FOUR "vendors" (Ping Identity for Access Management, CIAM,
# Saviynt for IGA, PAM) -- headings like "Why CIAM" and "PAM Extension
# Modules and Add-ons" in the shipped proposal are a direct result.
#
# EXTENSIBILITY: this is not a lookup against a known vendor or capability
# list (there is no such list -- a future partner brings whatever name and
# whatever capabilities). The connector word is the only reliable signal
# regardless of what is on either side of it, which is what makes this work
# for a partner that does not exist yet without any code change.
_CAPABILITY_CONNECTOR_RE = re.compile(
    r"\b(?:for|covering|delivering|providing|handling)\b", re.I)


def _strip_capability_clauses(text: str) -> str:
    """"Ping Identity for Access Management and CIAM, Saviynt for IGA and PAM"
    -> "Ping Identity, Saviynt" -- vendor names only, capability language
    removed, ready for the EXISTING and/comma vendor splitter below.

    Returns `text` unchanged when no connector word is present, so the
    parenthesized form ("Ping Identity (Access Management, CIAM)") and the
    bare list form ("Ping Identity, Saviynt") are completely untouched by
    this function and keep their existing, separately-tested behaviour.

    ALGORITHM: split on the connector word. The first chunk is the first
    vendor's name. Each middle chunk holds [capability list for the
    PREVIOUS vendor] followed by [the NEXT vendor's name] -- the last
    and/comma-separated item in that chunk is taken as the next vendor name,
    everything before it discarded as capability text. The final chunk (after
    the last connector) is pure trailing capability text for the last
    vendor -- its name was already captured as the last piece of the
    chunk before it, so the final chunk contributes nothing further.
    """
    chunks = _CAPABILITY_CONNECTOR_RE.split(text)
    if len(chunks) < 2:
        return text  # no connector word: nothing to strip

    vendor_names = [chunks[0].strip()]
    for middle in chunks[1:-1]:
        pieces = [p.strip() for p in _VENDOR_SPLIT_RE.split(middle) if p.strip()]
        if pieces:
            vendor_names.append(pieces[-1])
    return ", ".join(v for v in vendor_names if v)


def split_vendors(iam_vendor: Optional[str]) -> list[str]:
    """A single iam_vendor answer -> a list of one or more vendor names.

    "Ping Identity (Access Management, CIAM) and Saviynt (IGA, PAM)" ->
    ["Ping Identity (Access Management, CIAM)", "Saviynt (IGA, PAM)"]

    "SailPoint" -> ["SailPoint"]  (single vendor, list of one, unchanged
    behaviour throughout the rest of this file)

    Splits on commas inside parentheses are protected: "Ping Identity (Access
    Management, CIAM)" is ONE vendor with a two-item capability list, not two
    vendors, because the comma is nested inside "(...)".
    """
    text = (iam_vendor or "").strip()
    if not text:
        return []
    if _VENDOR_AND_IS_PART_OF_NAME.search(text):
        return [text]

    # "Vendor for Capability A and Capability B" has no parentheses, so
    # without this step "Access Management" and "CIAM" would be split out as
    # if they were separate vendor names -- exactly what happened live.
    # Strips to bare vendor names ONLY when a connector word is present;
    # otherwise `text` is returned unchanged and every line below behaves
    # exactly as it did before this existed.
    text = _strip_capability_clauses(text)
    if _VENDOR_AND_IS_PART_OF_NAME.search(text):
        return [text]

    # Split on top-level commas/and only — never inside parentheses, which is
    # where a single vendor's capability list lives ("Ping Identity (Access
    # Management, CIAM)").
    parts, depth, buf = [], 0, ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if depth == 0:
            m = _VENDOR_SPLIT_RE.match(text, i)
            if m and buf.strip():
                parts.append(buf.strip())
                buf = ""
                i = m.end()
                continue
        buf += ch
        i += 1
    if buf.strip():
        parts.append(buf.strip())

    parts = [p for p in parts if p]
    return parts if len(parts) > 1 else [text]

# --- Capability domains ------------------------------------------------------
# Which IAM domains an engagement covers, and which vendor owns each. Drives
# the scope and solution structure for ANY vendor mix: IV structures a
# multi-domain proposal (ESNAD: WIAM+CIAM on one platform, IGA+PAM on another)
# by domain and capability, and a single-domain one (Amlak: SailPoint IGA) by
# the governance facets. Order is display order.
DOMAIN_PATTERNS: tuple[tuple[str, str], ...] = (
    # "Privileged Access Management" contains "access management"; it is PAM.
    ("wiam", r"(?<!privileged )access management|\bam\b|wiam|workforce|\bsso\b|single sign"),
    ("ciam", r"ciam|customer|consumer|citizen"),
    ("iga", r"\biga\b|governance|lifecycle"),
    ("pam", r"\bpam\b|privileged"),
)

# Capability areas a domain section should walk through, where the evidence
# supports them. Generic IAM domain knowledge, not any one vendor's feature list.
DOMAIN_CAPABILITIES: dict[str, str] = {
    "wiam": ("single sign-on and federation protocols (SAML, OIDC, OAuth 2.0), "
             "adaptive and risk-based MFA, passwordless and FIDO2, authentication "
             "journeys and orchestration, protection of legacy applications through "
             "a gateway or agent, directory integration and synchronisation, and "
             "user self-service"),
    "ciam": ("registration and progressive profiling, social or national identity "
             "federation, consent and privacy management, profile management and "
             "account recovery, fraud and bot protection, and web and mobile "
             "channel support"),
    "iga": ("authoritative identity sources, aggregation and correlation, "
            "joiner-mover-leaver lifecycle automation, access request and approval "
            "workflows, role-based access control, access certification campaigns, "
            "segregation of duties, reconciliation and orphan accounts, and audit "
            "reporting"),
    "pam": ("privileged account discovery and onboarding, credential vaulting and "
            "rotation, just-in-time and time-bound access, privileged session "
            "management and recording (including keystroke logging), "
            "credential-less access, third-party and remote administrator access, "
            "approval workflows, and privileged audit and analytics"),
}

DOMAIN_NAMES: dict[str, str] = {
    "wiam": "Workforce Identity and Access Management (WIAM)",
    "ciam": "Customer Identity and Access Management (CIAM)",
    "iga": "Identity Governance and Administration (IGA)",
    "pam": "Privileged Access Management (PAM)",
}


def vendor_scopes(iam_vendor: str, vendor_scope_map: Optional[dict] = None) -> list[tuple[str, str]]:
    """[(vendor, scope text)] from the scope map, else from the vendor answer:
    "X for A, Y for B" or "X (A, B) and Y (C)". A bare "SailPoint" gives
    [("SailPoint", "SailPoint")] -- no stated scope."""
    if vendor_scope_map and len(vendor_scope_map) > 1:
        return [(str(v), str(s)) for v, s in vendor_scope_map.items()]
    out = []
    for part in split_vendors(iam_vendor):
        m = re.match(r"\s*(.+?)\s*\((.*)\)\s*$", part)
        if m:
            out.append((m.group(1), m.group(2)))
    if out:
        return out
    for part in re.split(r",|;|\band\b(?=\s+[A-Z][a-z]+\s+for\b)", iam_vendor or ""):
        m = re.match(r"\s*(.+?)\s+for\s+(.+?)\s*$", part)
        if m:
            out.append((m.group(1), m.group(2)))
    return out or ([(iam_vendor.strip(), iam_vendor)] if (iam_vendor or "").strip() else [])


def _domains_in(text: str) -> list[str]:
    return [d for d, pat in DOMAIN_PATTERNS if re.search(pat, text or "", re.I)]


def engagement_domains(iam_vendor: str, answers: Optional[dict] = None) -> dict:
    """{"domains": [...], "domain_vendors": {domain: vendor},
    "vendor_domains": {iam_vendors entry: [domains]}, "multi_domain": bool}.

    Read from what each vendor is stated to own. When the vendor answer names
    no capability (a bare "SailPoint"), the per-domain population answer
    ("WIAM users: 5000; PAM privileged accounts: 50") is the fallback for a
    single vendor; otherwise no domains are known and callers keep the
    single-domain structure."""
    answers = answers or {}
    vendors = split_vendors(iam_vendor)
    domain_vendors: dict[str, str] = {}
    vendor_domains: dict[str, list[str]] = {}
    for name, scope in vendor_scopes(iam_vendor, answers.get("vendor_scope_map")):
        owned = _domains_in(scope)
        key = next((v for v in vendors
                    if v.lower().split()[0] == name.lower().split()[0]), name)
        vendor_domains[key] = owned
        for d in owned:
            domain_vendors.setdefault(d, name)
    if not domain_vendors and len(vendors) == 1:
        pop = str(answers.get("population_by_domain") or "")
        keys = " ".join(re.findall(r"([^:;,\n]+):", pop))
        owned = _domains_in(keys)
        if owned:
            vendor_domains[vendors[0]] = owned
            domain_vendors = {d: vendors[0] for d in owned}
    domains = [d for d, _ in DOMAIN_PATTERNS if d in domain_vendors]
    return {"domains": domains, "domain_vendors": domain_vendors,
            "vendor_domains": vendor_domains, "multi_domain": len(domains) > 1,
            "domain_capabilities": DOMAIN_CAPABILITIES, "domain_names": DOMAIN_NAMES}


# Sentinel section id: this section is produced by the compliance-matrix
# pipeline (run_compliance_matrix) rather than by free-form LLM drafting.
COMPLIANCE_SECTION_ID = "compliance_matrix"


# Section id -> the corpus `section_topic` that section should draw on.
#
# Retrieval reserves half the evidence slots for chunks carrying this topic
# (sarvam_010), with a topic-scoped second query for topics too small to appear
# in a general candidate pool (sarvam_011). Measured: why_vendor went from 1 of
# 8 on-topic results to 4 of 8 when seeded from an unrelated query.
#
# The topics that matter most here are the BANK-SOURCED sections -- Company
# Profile, Why-Vendor, Similar Experience -- which are 1-3% of the corpus and
# have been thin in every generated proposal precisely because a general vector
# search rarely surfaces them.
#
# A section with no entry passes None and retrieval behaves as before, so the
# mapping can stay partial without breaking anything.
SECTION_TOPICS: dict[str, str] = {
    "executive_summary": "executive_summary",
    "company_profile": "company_profile",
    "similar_experience": "similar_experience",
    "scope_understanding": "scope",
    "solution_overview": "why_vendor",
    "proposed_solution": "architecture",
    "implementation_approach": "raci",
    "project_timeline": "timeline",
    "assumptions_responsibilities": "assumptions",
    "knowledge_transfer": "knowledge_transfer",
    "commercial": "pricing",
    # migration template
    "current_state": "scope",
    "target_state": "architecture",
    "migration_strategy": "migration",
    "rollback_risk": "migration",
    "decommissioning": "migration",
}

# Subsection heading -> topic, where the SECTION topic is too coarse. A sizing
# table and a joiner-flow description sit in the same section but want
# completely different evidence.
SUBSECTION_TOPICS: tuple[tuple[str, str], ...] = (
    (r"sizing|hardware", "sizing"),
    (r"raci|responsibilit", "raci"),
    (r"bill of quantit|boq|payment milestone|commercial assumption", "pricing"),
    (r"why ", "why_vendor"),
    (r"certification|segregation|who (has|should have|had) access", "governance"),
    (r"integration|connector|onboard|provisioning|joiner|hrms|active directory|sso",
     "integration"),
    (r"knowledge transfer|training|hypercare|support", "knowledge_transfer"),
    (r"timeline|tranche|milestone|plan|cutover window", "timeline"),
    (r"case stud|relevant engagement|lessons applied", "similar_experience"),
    (r"rollback|decommission|migration|coexistence|credential", "migration"),
    (r"test|validation|reconciliation", "testing"),
    (r"risk", "project_management"),
    (r"out of scope|assumption|dependenc|prerequisite", "assumptions"),
)

_SUBSECTION_TOPIC_RE = tuple(
    (re.compile(pat, re.I), topic) for pat, topic in SUBSECTION_TOPICS)


def topic_for(section_id: str, subsection_heading: str | None = None) -> Optional[str]:
    """The corpus topic to bias retrieval toward. Subsection wins when it matches.

    The subsection is checked FIRST because it is the more specific signal:
    "Proposed Production Hardware Sizing" wants sizing tables, not the
    architecture prose its parent section maps to.
    """
    if subsection_heading:
        for pattern, topic in _SUBSECTION_TOPIC_RE:
            if pattern.search(subsection_heading):
                return topic
    return SECTION_TOPICS.get(section_id)


@dataclass(frozen=True)
class SectionSpec:
    id: str
    title: str
    purpose: str
    query_template: str
    # Optional sections are only drafted when explicitly requested
    # (e.g. the compliance matrix).
    optional: bool = False
    # Content-specific subsections: (heading, drafting instruction).
    #
    # These used to come from a single module-level SUBSECTION_FACETS list
    # applied identically to every section, so all seven sections carried
    # "Overview / Detailed Design / Considerations & Dependencies" and the table
    # of contents showed the same three headings seven times. IV's own proposals
    # have 53 subsections and all 53 are different: "Proposed Production
    # Hardware Sizing", "Tranche 2 - Life Cycle Management + 5 Application",
    # "Payment Milestone - Resident Engineer". The heading IS the content.
    #
    # Headings support Jinja so a vendor name can appear where IV puts one.
    subsections: tuple[tuple[str, str], ...] = ()
    # Sections drawn near-verbatim from the corpus rather than reasoned from
    # discovery answers: company profile, case studies, vendor positioning.
    # These are about IV and the vendor, not about the client's estate.
    static_from_corpus: bool = False

    def render_query(self, context: dict) -> str:
        """Render this section's retrieval query from the given context."""
        rendered = Template(self.query_template).render(**context)
        # Collapse whitespace so the embedding input is clean.
        return " ".join(rendered.split())

    def render_title(self, context: dict) -> str:
        """Title with context substituted (e.g. 'Proposed Solution - SailPoint')."""
        return " ".join(Template(self.title).render(**context).split())

    def render_subsections(self, context: dict) -> list[tuple[str, str]]:
        """Subsection (heading, instruction) pairs with context substituted.

        MULTI-VENDOR EXPANSION: a heading that literally contains
        ``{{ iam_vendor }}`` is rendered ONCE PER VENDOR when
        ``context["iam_vendors"]`` holds more than one name, instead of once
        with a combined string like "Why Ping Identity and Saviynt".

        Why this matters: every ``{{ iam_vendor }}`` heading also drives a
        retrieval query for that subsection (see ``render_query`` and
        ``document_engine.draft_section``). A combined vendor string in a
        query dilutes the vector search against BOTH vendors' corpus content
        at once, and "Why Ping Identity and Saviynt" reads as one confused
        pitch rather than two clean, evidence-backed cases.

        A heading with NO ``{{ iam_vendor }}`` token (e.g. "Access
        Certification", "Who Has Access Today") is vendor-agnostic — it
        describes a capability the combined solution has, not one vendor's
        pitch — and is rendered exactly once regardless of vendor count.

        Single-vendor proposals get ``iam_vendors = [iam_vendor]``: one
        iteration, byte-identical output to before this existed.
        """
        vendors = context.get("iam_vendors") or (
            [context["iam_vendor"]] if context.get("iam_vendor") else [None])

        out: list[tuple[str, str]] = []
        # A subsection whose instruction renders empty does not apply to this
        # engagement (the hardware sizing tables under SaaS) and is skipped.
        for heading, instruction in self.subsections:
            if not Template(instruction).render(**context).strip():
                continue
            if "iam_vendor" in heading and len(vendors) > 1:
                for vendor in vendors:
                    vctx = {**context, "iam_vendor": vendor}
                    out.append((
                        " ".join(Template(heading).render(**vctx).split()),
                        " ".join(Template(instruction).render(**vctx).split()),
                    ))
            else:
                out.append((
                    " ".join(Template(heading).render(**context).split()),
                    " ".join(Template(instruction).render(**context).split()),
                ))
        return out


def render_section_query(section: SectionSpec, context: dict) -> str:
    """Module-level convenience wrapper around ``SectionSpec.render_query``."""
    return section.render_query(context)


# --- shared query fragments -------------------------------------------------
# Every query is grounded in the client, vendor and RFP text so retrieval
# surfaces the most relevant past-proposal evidence for THAT section.
_CTX = (
    "for {{ client_name }}"
    "{% if iam_vendor %} using {{ iam_vendor }}{% endif %}"
    " IAM {{ 'managed support' if proposal_type == 'mss' else 'implementation' }}"
)


# IV's real proposal skeleton, taken from the Amlak SailPoint proposal: 11
# top-level sections, 53 subsections, every subsection heading different. The
# previous template was a 7-section generic scaffold whose subsections were the
# same three words repeated, which is why a reviewer did not recognise the
# output as IV's.
#
# Section order matters. IV establishes credibility (Company Profile, Similar
# Experience) BEFORE describing scope, and puts Commercial last.
IMPLEMENTATION_SECTIONS: list[SectionSpec] = [
    SectionSpec(
        id="executive_summary",
        title="Executive Summary",
        purpose="High-level overview of the proposed engagement, value, and outcomes.",
        query_template=f"executive summary, engagement value, business outcomes and objectives {_CTX}. {{{{ rfp_text[:400] }}}}",
        # IV's executive summary is continuous prose on a single page, so this
        # section wants ONE drafting call, not a facet split.
        #
        # Leaving `subsections` empty does not achieve that: a section with none
        # falls back to SUBSECTION_FACETS, which is the generic
        # "Overview / Detailed Design / Considerations & Dependencies" triple
        # that Sprint B removed everywhere else. Run 9 shipped it at the top of
        # the document, in the one section a reader always reads.
        #
        # A single unnamed subsection renders as continuous prose under the
        # section heading, which is what IV actually does.
        subsections=(
            ("", "the engagement on one to two pages (roughly 500-800 words), "
                 "continuous prose with no sub-headings. It is the section every "
                 "evaluator reads, so it carries the substance, not slogans. "
                 "Cover, from the client-supplied facts: what {{ client_name }} is "
                 "buying and why now; each capability domain in scope with its "
                 "population and the platform that delivers it; the named systems "
                 "and applications to be integrated (name them); the deployment "
                 "model and any data-residency requirement; the standards and "
                 "authentication methods the solution supports; the regulations "
                 "and national identity services it aligns with; the delivery "
                 "model, duration and phase structure; and the support model after "
                 "go-live. A short list of the platforms and the domains each owns "
                 "is allowed; otherwise no bullet lists."),
        ),
    ),
    SectionSpec(
        id="company_profile",
        title="Company Profile",
        purpose="Introduce Inspirit Vision: who we are, where we operate, what we can field.",
        query_template="Inspirit Vision company profile, branch locations, service offices, workforce capabilities and certifications",
        static_from_corpus=True,
        subsections=(
            ("Inspirit Vision",
             "who Inspirit Vision is: founding, focus on identity and access management, "
             "and the position held in the market. Drawn from IV's own past proposals, not invented."),
            ("Branch Locations and Service Offices",
             "where IV operates from and which regions are served from each office."),
            ("Workforce and Capabilities",
             "team size, certified consultant counts by vendor, and delivery capability."),
        ),
    ),
    SectionSpec(
        id="similar_experience",
        title="Similar Experience and Customer References",
        purpose="Evidence of comparable delivery: sectors, vendors, outcomes.",
        query_template=f"similar experience, customer references, case studies, comparable {{{{ iam_vendor }}}} deployments in banking government and enterprise",
        static_from_corpus=True,
        subsections=(
            ("Relevant Engagements",
             "engagements comparable to this one by vendor, sector and scope. Naming "
             "past clients is permitted and expected - IV's own proposals do it. State "
             "the client, sector, vendor and the nature of the work. Never state an "
             "outcome metric the evidence does not support. Cite only work IV "
             "DELIVERED: a past proposal is not a reference, and a client is not "
             "a reference for a product the evidence does not show IV delivering "
             "there."),
            # IV's own reference section is a sector/client table, not case
            # studies: the corpus holds client lists but no outcomes, so a
            # "problem, delivered, outcome" facet could only be invented.
            ("Client References by Sector",
             "a markdown TABLE with columns Client Sector, Client List. Use ONLY "
             "client names and sectors that appear in the EVIDENCE; never add a "
             "client, sector or outcome the evidence does not name."),
        ),
    ),
    SectionSpec(
        id="scope_understanding",
        title="Our Understanding of Scope",
        purpose="Restate the client's situation and fix the scope boundary precisely.",
        query_template=f"understanding of scope, business drivers, current state, in scope and out of scope {_CTX}. {{{{ rfp_text[:400] }}}}",
        subsections=(
            ("{{ client_name }} Current State and Drivers",
             "the client's current identity estate, the incumbent platform being replaced, "
             "and the business drivers stated in discovery. Name actual systems and counts."),
            ("Identity and Access Management - {{ iam_vendor }}",
             "{% if not domains %}what is IN scope, as a structured list: the capability "
             "areas, application counts, identity types and environments supplied at "
             "discovery.{% endif %}"),
            # Multi-domain engagements: IV restates scope domain by domain, the way
            # the client's SOW is written, so an evaluator can tick each item off.
            ("Discovery and IAM Assessment",
             "{% if domains %}the assessment-first discovery phase: what is validated "
             "before design (identity populations and licensing baseline, "
             "authoritative sources, applications and how each authenticates today, "
             "privileged accounts, integration dependencies, governance processes) "
             "and how the findings finalise the target design. Use the discovery "
             "duration where supplied.{% endif %}"),
            ("Workforce Identity and Access Management (WIAM)",
             "{% if 'wiam' in domains %}the in-scope workforce capability delivered "
             "by {{ domain_vendors.wiam }}, as a bulleted list: the workforce "
             "population, the applications it covers, and the capabilities among "
             "{{ domain_capabilities.wiam }} that the client-supplied facts or "
             "requirements call for.{% endif %}"),
            ("Customer Identity and Access Management (CIAM)",
             "{% if 'ciam' in domains %}the in-scope customer identity capability "
             "delivered by {{ domain_vendors.ciam }}, as a bulleted list: the "
             "customer population and the named customer-facing applications, and "
             "the capabilities among {{ domain_capabilities.ciam }} that the "
             "client-supplied facts or requirements call for.{% endif %}"),
            ("Identity Governance and Administration (IGA)",
             "{% if 'iga' in domains %}the in-scope governance capability delivered "
             "by {{ domain_vendors.iga }}, as a bulleted list: the governed "
             "population and systems, and the capabilities among "
             "{{ domain_capabilities.iga }} that the client-supplied facts or "
             "requirements call for.{% endif %}"),
            ("Privileged Access Management (PAM)",
             "{% if 'pam' in domains %}the in-scope privileged access capability "
             "delivered by {{ domain_vendors.pam }}, as a bulleted list: the "
             "privileged account count and target platforms, and the capabilities "
             "among {{ domain_capabilities.pam }} that the client-supplied facts or "
             "requirements call for.{% endif %}"),
            ("Application and Platform Integration",
             "{% if domains %}EVERY system and application named in the "
             "client-supplied facts (target integrations, applications, directories, "
             "identity sources), as a bulleted list, each with the integration it "
             "needs: federation/SSO, provisioning, governance or privileged access. "
             "Do not drop a named system and do not add one the facts do not name."
             "{% endif %}"),
            ("Identity Data Migration and Lifecycle Transition",
             "{% if domains %}how existing identities, accounts and entitlements move "
             "into the new platforms: source extraction, cleansing scope, "
             "reconciliation, and cutover from current processes.{% endif %}"),
            ("Testing and Production Deployment",
             "{% if domains %}the test stages (functional, integration, UAT) and how "
             "configuration is promoted through the environments named at discovery "
             "into production.{% endif %}"),
            ("Training, Knowledge Transfer and Documentation",
             "{% if domains %}one short paragraph on what the client's teams receive: "
             "training audiences, knowledge transfer and the documentation set.{% endif %}"),
            ("Post-Implementation Support and Optimization",
             "{% if domains %}one short paragraph on the support model after go-live, "
             "as supplied at discovery.{% endif %}"),
            ("Compliance and Regulatory Alignment",
             "{% if domains %}each regulation, national identity service and "
             "data-residency requirement named in the client-supplied facts, and how "
             "the solution addresses it. Name only what the facts name.{% endif %}"),
            ("Out of Scope",
             "what is explicitly EXCLUDED. Render as a bulleted list, one exclusion per "
             "line, using the supplied out-of-scope items verbatim in substance. This "
             "section protects IV commercially, so completeness matters more than prose."),
        ),
    ),
    SectionSpec(
        id="solution_overview",
        title="Solution Overview - Identity and Access Management",
        purpose="Vendor positioning and the governance capabilities being brought.",
        query_template=f"{{{{ iam_vendor }}}} solution overview, identity governance platform capabilities, access certification, provisioning, segregation of duties, reporting and connectors",
        static_from_corpus=True,
        subsections=(
            ("Why {{ iam_vendor }}",
             "the case for this vendor over alternatives: analyst position, governance "
             "depth, connector coverage, and fit to the client's stated drivers."),
            ("Who Has Access Today",
             "{% if not multi_domain %}how the platform answers the visibility question: aggregation, correlation "
             "and the single view of entitlement across connected systems.{% endif %}"),
            ("Who Should Have Access",
             "{% if not multi_domain %}role modelling, birthright access, policy and segregation-of-duties controls.{% endif %}"),
            ("Who Had Access",
             "{% if not multi_domain %}historical audit: what was granted, by whom, when, and revocation evidence.{% endif %}"),
            ("Access Certification",
             "{% if not multi_domain %}certification campaign types scoped to the reviewers named at discovery.{% endif %}"),
            ("Provisioning and Lifecycle Management",
             "{% if not multi_domain %}automated joiner, mover and leaver flows and the connectors that fulfil them.{% endif %}"),
            ("Segregation of Duties",
             "{% if not multi_domain %}the SoD policy model, and detective versus preventive control points.{% endif %}"),
            ("Reporting and Analytics",
             "{% if not multi_domain %}standard and custom reporting, dashboards and audit evidence production.{% endif %}"),
            ("Connectors and Integrations",
             "{% if not multi_domain %}connector coverage for the systems named at discovery, and the approach "
             "where no out-of-box connector exists.{% endif %}"),
            # IV's Solution Overview has THIRTEEN subsections; run 9 produced
            # nine. The four below are the gap, and they are precisely where IV
            # places its product screenshots -- the `product` asset kind we hold
            # 97 approved examples of and barely used.
            ("{{ iam_vendor }} Solution Overview",
             "the platform itself: what the product is, its core components, and "
             "how they fit together. Vendor-level, not client-specific. "
             "STRUCTURE THIS AS NESTED MARKDOWN, not flat prose: one '## ' "
             "header per major capability area the evidence supports (for "
             "example: authentication methods, session and orchestration, "
             "channel/mobile support, integration patterns, administrative "
             "tooling -- named per what this specific product actually "
             "offers, not this generic list). Under a capability area, use "
             "'### ' for a named sub-feature only where the evidence gives "
             "genuine, specific detail to write about -- do not invent a "
             "sub-heading with nothing under it. A reviewer scanning the "
             "headers alone, without reading the prose, should be able to "
             "tell what the product does."
             "{% set owned = vendor_domains.get(iam_vendor, []) if vendor_domains else [] %}"
             "{% if multi_domain and owned %} In this engagement {{ iam_vendor }} "
             "owns{% for d in owned %} {{ domain_names[d] }}{{ ',' if not loop.last }}"
             "{% endfor %}. Use one '## ' header per owned domain and, under each, "
             "'### ' headers for its capability areas that the evidence and the "
             "client's requirements support, drawn from:{% for d in owned %} "
             "{{ domain_names[d] }}: {{ domain_capabilities[d] }}.{% endfor %} For "
             "each capability, say what it does and how it applies to this client's "
             "named systems and users. Do not describe domains another vendor owns."
             "{% endif %}"),
            ("Comprehensive Identity Governance Platform",
             "{% if not multi_domain %}the breadth of the governance platform across identity lifecycle, "
             "access request, certification, policy and analytics - what a single "
             "platform covers that point solutions do not.{% endif %}"),
            ("Intuitive Administrative and End User Dashboards",
             "{% if not multi_domain %}what an administrator and an end user each see day to day: request "
             "flows, approvals, self-service, and the administrative console.{% endif %}"),
            ("{{ iam_vendor }} Extension Modules and Add-ons",
             "optional modules beyond the core platform and when each is worth "
             "adding. Note explicitly which are IN scope for this engagement and "
             "which are not. A capability the client-supplied scope names is IN "
             "scope; never describe it as an excluded or future add-on. A separately "
             "licensed module is IN scope only when the client-supplied facts or "
             "requirements name it or it is the vendor's stated scope; otherwise "
             "list it as optional."),
        ),
    ),
    SectionSpec(
        id="proposed_solution",
        title="Proposed Solution - {{ iam_vendor }}",
        purpose="The actual proposed design: architecture, sizing per environment, integrations.",
        query_template=f"proposed deployment architecture, production hardware sizing, DR and UAT sizing, cluster topology, HRMS and Active Directory integration {_CTX}",
        subsections=(
            ("Proposed Future IAM State for {{ client_name }}",
             "the target state, contrasted with the current state named at discovery."),
            ("Proposed Deployment Architecture",
             "environments, zones, and how components are distributed across them."),
            ("Proposed Production Architecture",
             "the production tier in detail: cluster topology, node roles and data tier."),
            # IV's own sizing tables are ELEVEN columns wide and identical across
            # all four environments. Run 9 produced 6, 4, 3 and 3 columns from an
            # invented column list, which is why they read thinner than the human
            # original even where the numbers were right. These are IV's actual
            # headers, taken from the Amlak proposal.
            ("Proposed Production Hardware Sizing",
             "{% if not is_saas %}"
             "production sizing as a markdown TABLE with EXACTLY these columns: "
             "#, Server Category, Quantity, CPU per node, Memory per node (GB), "
             "Storage per node (GB), DB Storage (GB), Operating System, "
             "Application Server, Database, Remarks. "
             "One row per server category. Use the discovery sizing figures "
             "exactly; write N/A where a column does not apply, never leave a "
             "cell blank. Remarks names the node split (e.g. '2 x UI, 2 x Task') "
             "and any RAID or clustering requirement."
             "{% endif %}"),
            ("Proposed DR Hardware Sizing",
             "{% if not is_saas %}"
             "disaster recovery sizing as a markdown TABLE with EXACTLY these "
             "columns: #, Server Category, Quantity, CPU per node, "
             "Memory per node (GB), Storage per node (GB), DB Storage (GB), "
             "Operating System, Application Server, Database, Remarks. "
             "DR mirrors production unless discovery says otherwise. Follow the "
             "table with one short paragraph on the replication approach."
             "{% endif %}"),
            ("Proposed UAT Hardware Sizing",
             "{% if not is_saas %}"
             "UAT sizing as a markdown TABLE with EXACTLY these columns: "
             "#, Server Category, Quantity, CPU per node, Memory per node (GB), "
             "Storage per node (GB), DB Storage (GB), Operating System, "
             "Application Server, Database, Remarks. UAT is normally reduced "
             "from production; use the discovery figures. If no discovery "
             "figures are available for this environment, write N/A rather "
             "than estimating -- an invented specification is worse than an "
             "acknowledged gap."
             "{% endif %}"),
            ("Proposed Development Hardware Sizing",
             "{% if not is_saas %}"
             "development sizing as a markdown TABLE with EXACTLY these columns: "
             "#, Server Category, Quantity, CPU per node, Memory per node (GB), "
             "Storage per node (GB), DB Storage (GB), Operating System, "
             "Application Server, Database, Remarks. Development is the smallest "
             "environment, typically a single node. If no discovery figures are "
             "available for this environment, write N/A rather than estimating "
             "-- an invented specification is worse than an acknowledged gap."
             "{% endif %}"),
            # SaaS: IV's ESNAD proposal sizes no hardware. It gives each vendor an
            # environment table plus what the client must still provide, then
            # one consolidated table. The four hardware tables above render
            # empty (and are skipped) when is_saas, instead of four N/A rows.
            ("{{ iam_vendor }} Deployment and Environments",
             "{% if is_saas %}"
             "how {{ iam_vendor }} is consumed as a vendor-hosted SaaS service "
             "for this client. First a markdown TABLE with columns NO, "
             "Environment, Purpose, Proposed: one row per environment named at "
             "discovery for this platform. Then state what the vendor manages "
             "(application, database and processing infrastructure) and what "
             "the client must provide (secure connectivity, integration agents "
             "or connectors, gateway or proxy components, firewall rules). "
             "Never invent CPU, memory or storage figures."
             "{% endif %}"),
            ("Consolidated Environment and Infrastructure Model",
             "{% if is_saas %}"
             "one markdown TABLE with columns NO, Solution Area, Platform, "
             "Deployment Model, Baseline Environments, Infrastructure Sizing: "
             "one row per solution area in scope (for example WIAM, CIAM, IGA, "
             "PAM), each mapped to the vendor platform that owns it. "
             "Infrastructure Sizing reads 'Managed by <vendor>' for the "
             "platform itself; client-side connectivity components are sized "
             "after the discovery assessment."
             "{% endif %}"),
            ("Proposed HRMS Integration and Joiner Workflow",
             "the authoritative source feed and the joiner workflow it triggers, "
             "step by step through to account creation in the target systems."),
            ("Proposed Active Directory and Exchange Integration",
             "AD and Exchange provisioning, including password capture and propagation "
             "where discovery specifies it."),
            ("Proposed Integration with Identity Provider for SSO",
             "how the existing identity provider is integrated and what it continues to own."),
            ("Application Onboarding Approach",
             "the repeatable pattern for onboarding applications, and how the batches "
             "named at discovery are sequenced."),
        ),
    ),
    SectionSpec(
        id="implementation_approach",
        title="Implementation Approach",
        purpose="How delivery is executed: maturity journey, deliverables, responsibilities.",
        query_template=f"implementation approach, identity maturity journey, project deliverables, RACI matrix, build current state {_CTX}",
        subsections=(
            ("Identity Maturity Journey",
             "the progression from the current state to governed identity, as stages."),
            ("Project Deliverables",
             "deliverables as a markdown TABLE with columns Deliverable, Description, Phase."),
            ("Benefits",
             "the concrete benefits tied to the client's stated pain points."),
            # IV splits RACI into a legend plus TWO matrices -- governance
            # activities, then delivery activities -- 33 rows in total. Run 9
            # produced a single 10-row table.
            ("Discovery and Current-State Baseline",
             "the discovery and baseline stage: what is inventoried before any "
             "build begins - applications, identities, entitlements, existing "
             "integrations and data quality. Present the inventory as a markdown "
             "TABLE with columns Area, What Is Captured, Source, Owner."),
            ("RACI Legend",
             "a short markdown TABLE with columns Role, Description defining "
             "R = Responsible, A = Accountable, C = Consulted, I = Informed."),
            ("RACI - Project Governance",
             "governance responsibilities as a markdown TABLE with columns "
             "Deliverable / Activity, {{ client_name }}, Inspirit Vision"
             "{% for v in iam_vendors %}, {{ v }}{% endfor %}, "
             "Description / Comments. "
             "{% if iam_vendors|length > 1 %}"
             "This is a MULTI-VENDOR engagement: mark R/A/C/I for a vendor "
             "ONLY on rows where that vendor genuinely has a role (e.g. a "
             "licensing decision for a module only one vendor supplies); "
             "leave the cell blank rather than marking every vendor on "
             "every row uniformly, which would misrepresent who actually "
             "does the work. "
             "{% endif %}"
             "Cover ways of working, steering committee, "
             "project tools, status reporting, change control, and risk and issue "
             "management. At least 10 rows."),
            ("RACI - Delivery Activities",
             "delivery responsibilities as a markdown TABLE with EXACTLY these "
             "columns: Deliverable / Activity, {{ client_name }}, Inspirit Vision"
             "{% for v in iam_vendors %}, {{ v }}{% endfor %}, "
             "Description / Comments. "
             "{% if iam_vendors|length > 1 %}"
             "This is a MULTI-VENDOR engagement: mark R/A/C/I for a vendor "
             "ONLY on rows describing work that vendor's own platform "
             "performs (e.g. only the PAM vendor is Responsible for "
             "privileged session recording); leave other vendors' cells "
             "blank on that row rather than marking every vendor uniformly. "
             "{% endif %}"
             "Cover scope definition, product acquisition, environment "
             "provisioning, design, build, integration, testing, UAT, cutover and "
             "handover. Use the responsibilities supplied at discovery. At least "
             "12 rows."),
            # Three sections IV writes and Shilpi had none of, all sitting
            # right after RACI in IV's own document: the team that delivers,
            # the risk register that tracks what could go wrong, and the
            # explicit boundary of what is NOT included -- three things a
            # reviewer checks for before signing, not decoration.
            ("Project Resources",
             "the delivery team as a markdown TABLE with columns Role, "
             "Allocation (Full Time / Part Time), Responsibilities. One row "
             "per role actually needed for THIS engagement's scale and "
             "duration -- typically Project Manager, IAM Solution "
             "Architect, Technical Lead, Developer(s), QA Engineer, scaled "
             "to what discovery states about duration and application "
             "count. Do not pad the team with roles this engagement's size "
             "does not warrant."),
            ("Initial Project RAID Log",
             "a starting Risks, Assumptions, Issues and Dependencies "
             "register as a markdown TABLE with columns Type (Risk / "
             "Assumption / Issue / Dependency), Description, Owner, "
             "Mitigation / Action. Draw genuine risks from what discovery "
             "actually states -- an unconfirmed dependency, a tight "
             "timeline, an integration with no discovered detail -- not "
             "generic project-risk boilerplate. At least 8 rows across all "
             "four types; this is a living document refined during "
             "Discovery, not a final risk assessment."),
            ("Scope Exclusions",
             "what this engagement explicitly does NOT include, as a "
             "bulleted list. Protects both parties from scope creep the "
             "same way the out-of-scope answer at discovery protects the "
             "estimate -- state it again here, in delivery terms, alongside "
             "standard implementation exclusions discovery does not "
             "usually think to mention: no application-side code changes, "
             "no data cleansing beyond what is explicitly scoped, no "
             "penetration testing, no load testing unless separately "
             "agreed. Do not invent an exclusion the discovery answers "
             "contradict."),
        ),
    ),
    SectionSpec(
        id="project_timeline",
        title="Project Management and Timeline",
        purpose="The delivery plan: phases, tranches and durations.",
        query_template=f"project timeline, implementation plan, tranche phasing, foundation phase, lifecycle management, access certification rollout {_CTX}",
        subsections=(
            ("High-Level Implementation Plan",
             "the overall plan as a markdown TABLE with columns Phase, Key Activities, "
             "Duration. Use the engagement duration and phase count supplied at discovery "
             "exactly; do not substitute a generic timeline."),
            # IV gives each tranche its own milestone TABLE with success
            # criteria -- 14, 6 and 6 rows. Run 9 wrote prose for all three,
            # which is why the table count trails the human original.
            ("Project Plan",
             "how the plan is run rather than what it contains: governance cadence, "
             "status reporting, change control, risk and issue management, and the "
             "tooling used to track it."),
            ("Tranche 1 - Foundation",
             "foundation scope as a markdown TABLE with columns Sr#, Category, "
             "Milestone, Success Criteria, numbering rows M1.1, M1.2, .... "
             "Cover licence delivery, kickoff, consulting and design workshops, "
             "provisioning of the environments named at discovery (installation "
             "only where the platform is self-managed), core configuration and "
             "authoritative source onboarding. Success Criteria states what "
             "evidence closes that milestone. At least 10 rows."),
            ("Tranche 2 - Lifecycle Management and Initial Applications",
             "lifecycle automation plus the first application batch, as a markdown "
             "TABLE with EXACTLY these columns: Sr#, Category, Milestone, "
             "Success Criteria, numbering rows M2.1, M2.2, .... Cover joiner/mover/leaver automation, birthright "
             "roles, the first application batch named at discovery, UAT and "
             "documentation. At least 6 rows."),
            ("Tranche 3 - Access Certification and Application Onboarding",
             "certification campaigns plus continued onboarding, as a markdown "
             "TABLE with EXACTLY these columns: Sr#, Category, Milestone, "
             "Success Criteria, numbering rows M3.1, M3.2, ..., in the batch size "
             "named at discovery. "
             "At least 6 rows."),
        ),
    ),
    SectionSpec(
        id="assumptions_responsibilities",
        title="Key Assumptions and Responsibilities",
        purpose="The assumptions the estimate rests on and what the client must provide.",
        query_template=f"key assumptions, client responsibilities, resource commitments, infrastructure prerequisites, dependencies {_CTX}",
        subsections=(
            ("Build and Infrastructure Prerequisites",
             "what must exist before build starts: environments, access, network readiness."),
            ("{{ client_name }} Resource Commitments",
             "the client roles and time commitments required, as a bulleted list."),
            ("Logistics",
             "working arrangements: onsite versus remote split, working hours and "
             "calendar, access and facility requirements, travel. Use the discovery "
             "answers on travel and resourcing."),
            ("Working Assumptions",
             "the delivery assumptions the plan depends on, as a bulleted list. Use the "
             "assumptions supplied at discovery."),
            ("{{ iam_vendor }} Assumptions",
             "assumptions specific to the PLATFORM rather than the engagement: "
             "licensing model, version and patch level, vendor support "
             "entitlement, and any product capability the plan depends on."),
            ("Dependencies",
             "external dependencies and what happens to the plan if each slips."),
        ),
    ),
    SectionSpec(
        id="knowledge_transfer",
        title="Knowledge Transfer and Training",
        purpose="How the client is left able to run the platform.",
        query_template=f"knowledge transfer plan, administrator training, handover to support team, hypercare and post production support {_CTX}",
        subsections=(
            ("Knowledge Transfer Objectives",
             "what the KT process is designed to achieve."),
            ("Knowledge Transfer Plan",
             "the KT plan as a markdown TABLE with columns Audience, Topic, Format, Timing."),
            ("Training Strategy",
             "who is trained, on what, and why that split matches how the "
             "client's teams will actually operate the platform day to day."),
        ),
    ),
    SectionSpec(
        id="post_production_support",
        title="Post-Production Support",
        purpose="What happens after go-live: onsite hypercare, then remote AMC, with real SLA commitments.",
        query_template=f"post implementation support, annual maintenance contract, hypercare period, service level agreement, support tiers {_CTX}",
        subsections=(
            ("Post-Implementation Support (Onsite) and AMC (Remote)",
             "the two-phase support model: an initial onsite hypercare period "
             "immediately following go-live, transitioning to a remote Annual "
             "Maintenance Contract. Use the hypercare duration and support "
             "model named at discovery; where discovery does not state one, "
             "say the hypercare duration will be agreed with the client. "
             "Never state a duration as IV's standard unless the EVIDENCE "
             "shows it."),
            ("Scope of Operation Support",
             "what IS and is NOT covered under AMC, as a markdown TABLE with "
             "columns Service, In Scope, Out of Scope. Cover application-level "
             "patching and configuration support, incident and problem "
             "management, minor enhancements. Explicitly exclude what remains "
             "the client's own responsibility: underlying OS/network/platform "
             "patching, infrastructure monitoring, anything client-managed per "
             "the discovery responsibility split."),
            ("Coverage",
             "support coverage tiers as a markdown TABLE with columns Tier, "
             "Coverage Window, Channels. Name the tiers by what they actually "
             "offer (e.g. a 24x7 tier for Severity 1, business-hours for lower "
             "severities) rather than generic tier labels with no content "
             "behind them; use the severity/response targets from discovery "
             "where supplied."),
            ("Service Level Agreement",
             "response and resolution targets as a markdown TABLE with "
             "columns Severity, Definition, Response Time, Resolution "
             "Target. Use the severity definitions and targets from "
             "discovery exactly where supplied; where discovery gives none, "
             "propose IV's standard SLA targets and say explicitly that "
             "these are proposed defaults, not client-confirmed figures."),
        ),
    ),
    SectionSpec(
        id="commercial",
        title="Commercial",
        purpose="Commercial structure and basis. Figures belong to the commercial owner.",
        query_template=f"commercial structure, license bill of quantities, implementation pricing basis, payment milestones, resident engineer {_CTX}",
        subsections=(
            ("{{ iam_vendor }} Licence Bill of Quantities",
             "the licence line items for {{ iam_vendor }} specifically as a "
             "markdown TABLE with columns Item, Description, Quantity, Unit, "
             "Basis. Leave price cells as 'To be confirmed' - Shilpi does "
             "NOT invent commercial figures. Vendor-specific: only line "
             "items {{ iam_vendor }} itself supplies, not other vendors in "
             "this engagement. List only products and modules IN scope; "
             "never a module this proposal excludes, and never a product "
             "code or SKU the evidence does not give. Take quantities from the "
             "population by domain (workforce users, customer identities, "
             "privileged accounts) where one applies."),
            ("Total Bill of Quantities",
             "the combined BOQ as a markdown TABLE with EXACTLY these columns: "
             "#, Item, Description, Unit Price, Total Price. One row per "
             "delivery phase from the plan, plus one licence row per vendor, "
             "each description one short sentence. Leave price cells "
             "as 'To be confirmed' - Shilpi does NOT invent commercial figures."),
            # IV splits payment into a licence schedule (by year) and an
            # implementation schedule (by milestone, with percentages).
            ("Payment Milestone - Licence",
             "licence payment schedule as a markdown TABLE with columns Item #, "
             "Invoice Date, Invoice Amount. One row per licence year where "
             "discovery gives a term. Amounts stay 'To be confirmed' unless "
             "supplied - never invent a figure."),
            ("Payment Milestone - Implementation",
             "implementation payment schedule as a markdown TABLE with columns "
             "Item #, Milestone, Payment %, Amount. Milestones follow the delivery "
             "plan (kickoff, requirements and design, application onboarding, "
             "certification, go-live, handover). Percentages must total 100 where "
             "discovery supplies them, otherwise 'To be confirmed'. Amounts stay "
             "'To be confirmed' unless supplied."),
            ("Payment Milestone - Resident Engineer",
             "the resident engineer commercial line as a markdown TABLE with "
             "columns Item #, Period, Rate Basis, Amount. Include only if "
             "discovery names a resident engineer. Amounts stay 'To be confirmed' "
             "unless supplied."),
            ("Payment Milestone - Application Integration Bucket",
             "the per-batch application integration line as a markdown TABLE with "
             "columns Item #, Batch, Applications, Payment %, Amount. Use the "
             "batch size named at discovery. Amounts stay 'To be confirmed' "
             "unless supplied."),
            ("Commercial Assumptions",
             "what the commercial structure assumes: travel, taxes, support terms, "
             "validity, using the answers supplied at discovery."),
        ),
    ),
    SectionSpec(
        id=COMPLIANCE_SECTION_ID,
        title="Compliance Matrix",
        purpose="Requirement-by-requirement coverage assessment against the RFP.",
        query_template="{{ rfp_text }}",
        optional=True,
    ),
]


MSS_SECTIONS: list[SectionSpec] = [
    SectionSpec(
        id="executive_summary",
        title="Executive Summary",
        purpose="High-level overview of the managed support engagement and value.",
        query_template=f"executive summary, managed support value, service outcomes {_CTX}. {{{{ rfp_text[:400] }}}}",
    ),
    SectionSpec(
        id="current_state",
        title="Current State & Support Objectives",
        purpose="Summarize the client's current IAM estate and support objectives.",
        query_template=f"current state IAM estate, support objectives, pain points {_CTX}. {{{{ rfp_text[:400] }}}}",
    ),
    SectionSpec(
        id="service_model",
        title="Managed Support Service Model",
        purpose="Describe the managed support service model and scope of services.",
        query_template=f"managed support service model, scope of services, {{{{ iam_vendor }}}} operations, run and maintain {_CTX}",
    ),
    SectionSpec(
        id="sla_coverage",
        title="SLA & Coverage Tiers",
        purpose="Present service levels, response/resolution targets, and coverage tiers.",
        query_template=f"SLA service levels, response and resolution targets, coverage tiers, support hours {_CTX}",
    ),
    SectionSpec(
        id="operating_model",
        title="Operating Model & Governance",
        purpose="Describe the operating model, roles, and governance cadence.",
        query_template=f"operating model, governance, roles and responsibilities, reporting and service reviews {_CTX}",
    ),
    SectionSpec(
        id="escalation_incident",
        title="Escalation & Incident Management",
        purpose="Detail incident, problem, and escalation management processes.",
        query_template=f"escalation management, incident and problem management, priority handling {_CTX}",
    ),
    SectionSpec(
        id="assumptions_open_questions",
        title="Assumptions & Open Questions",
        purpose="Capture assumptions, dependencies, and items needing SME/client input.",
        query_template=f"assumptions, dependencies, prerequisites and open questions {_CTX}",
    ),
    SectionSpec(
        id=COMPLIANCE_SECTION_ID,
        title="Compliance Matrix",
        purpose="Requirement-by-requirement coverage assessment against the RFP.",
        query_template="{{ rfp_text }}",
        optional=True,
    ),
]


# A migration proposal is NOT an implementation proposal with a different title.
# The client already has a working identity platform and people logging in with
# it every day. The questions a reviewer asks are therefore different: what
# happens to the data, what happens to sessions during cutover, how do we get
# back if it fails, and what stays behind. Those sections have no counterpart in
# a greenfield implementation, and drafting a migration off the implementation
# template would silently omit every one of them.
#
# The intake template has offered `migration` since it was written, but no
# template existed here, so `get_template("migration")` raised ValueError AFTER
# a consultant had answered all 22 discovery areas. The corpus now holds 39
# migration files across 12 engagements (NWC Oracle Access Manager, ForgeRock
# upgrades, BTPN, Maxis, KAU, TVS, DFCC, Brunei Shell, East West Bank), so this
# type finally has grounding to draft from.
MIGRATION_SECTIONS: list[SectionSpec] = [
    SectionSpec(
        id="executive_summary",
        title="Executive Summary",
        purpose="The migration in one page: from what, to what, why now, and what changes for users.",
        query_template=f"executive summary, platform migration drivers, business case for replacing an incumbent identity platform {_CTX}. {{{{ rfp_text[:400] }}}}",
    ),
    SectionSpec(
        id="company_profile",
        title="Company Profile",
        purpose="Introduce Inspirit Vision: who we are, where we operate, what we can field.",
        query_template="Inspirit Vision company profile, branch locations, service offices, workforce capabilities and certifications",
        static_from_corpus=True,
        subsections=(
            ("Inspirit Vision",
             "who IV is, the focus on identity and access management, and market position."),
            ("Branch Locations and Service Offices",
             "where IV operates from and which regions each office serves."),
            ("Migration Delivery Capability",
             "IV's specific track record moving clients BETWEEN identity platforms, "
             "as distinct from greenfield implementation."),
        ),
    ),
    SectionSpec(
        id="similar_experience",
        title="Similar Migration Experience",
        purpose="Evidence of comparable platform moves.",
        query_template="platform migration case studies, identity platform replacement, upgrade and re-platforming engagements",
        static_from_corpus=True,
        subsections=(
            ("Comparable Migrations",
             "past engagements moving between identity platforms. State the source "
             "platform, target platform, scale and sector. Naming past clients is "
             "permitted."),
            ("Lessons Applied",
             "what IV learned on those engagements that shapes the approach here."),
        ),
    ),
    SectionSpec(
        id="current_state",
        title="Current State Assessment",
        purpose="What exists today. A migration is defined by its starting point.",
        query_template=f"current state assessment, incumbent identity platform, existing integrations, technical debt {_CTX}. {{{{ rfp_text[:400] }}}}",
        subsections=(
            ("Incumbent Platform",
             "the platform being replaced, its version, and what it currently does. "
             "Use the platform named at discovery; never guess a version."),
            ("Existing Integrations and Dependencies",
             "the applications, directories and authoritative sources connected today, "
             "as a markdown TABLE with columns System, Integration Type, Owner, "
             "Migration Complexity."),
            ("Identity and Entitlement Data Today",
             "what identity data exists, where it lives, and its known quality issues. "
             "Data quality is the usual cause of migration overrun, so be specific."),
            ("Constraints Carried Forward",
             "what about the current estate constrains the target design."),
        ),
    ),
    SectionSpec(
        id="target_state",
        title="Target State Architecture",
        purpose="What the client ends up with.",
        query_template=f"target state architecture, {{{{ iam_vendor }}}} deployment architecture, cluster topology, environments {_CTX}",
        subsections=(
            ("Proposed Target Architecture",
             "the target platform architecture, zones and components."),
            ("Proposed Production Hardware Sizing",
             "{% if is_saas %}"
             "production sizing as a markdown TABLE with columns Component, Role, "
             "vCPU, Memory, Storage, Operating System. This is a SaaS platform: "
             "there is no IV- or client-provisioned hardware. Write ONE row "
             "stating the platform is vendor-managed, N/A in every sizing "
             "column, and the SLA commitment in a closing note. Do not invent "
             "vCPU, memory or storage figures for a vendor-managed tenant."
             "{% else %}"
             "production sizing as a markdown TABLE with columns Component, Role, "
             "vCPU, Memory, Storage, Operating System. Use the discovery figures "
             "exactly; write N/A where a column does not apply rather than "
             "estimating -- an invented specification is worse than an "
             "acknowledged gap."
             "{% endif %}"),
            ("Proposed DR and Non-Production Sizing",
             "{% if is_saas %}"
             "DR, UAT and development sizing as a markdown TABLE, same columns "
             "as Production above. Production is vendor-managed SaaS with no "
             "sized hardware; these environments must say the same thing, not "
             "invent client-provisioned specifications that do not exist. One "
             "row per environment, N/A in every sizing column, notes on the "
             "vendor's own DR/failover model where discovery states one."
             "{% else %}"
             "DR, UAT and development sizing as a markdown TABLE, same columns. "
             "Where no discovery figures exist for an environment, write N/A "
             "rather than estimating."
             "{% endif %}"),
            ("Capability Mapping - Current to Target",
             "a markdown TABLE with columns Current Capability, Target Capability, "
             "Gap, Notes. This is the section a client reads most closely: it proves "
             "nothing they rely on today is being dropped."),
        ),
    ),
    SectionSpec(
        id="migration_strategy",
        title="Migration Strategy and Approach",
        purpose="How the move is executed without breaking access.",
        query_template=f"migration strategy, phased cutover, big bang versus parallel run, coexistence, user migration approach {_CTX}",
        subsections=(
            ("Migration Pattern",
             "the chosen pattern (phased, parallel run, or cutover) and why it suits "
             "this estate. State the trade-off honestly rather than asserting one is best."),
            ("Coexistence Period",
             "how the incumbent and target platforms operate side by side, which is "
             "authoritative for what, and for how long."),
            ("Identity and Credential Migration",
             "how identities, entitlements and credentials move. Address password "
             "migration explicitly: whether hashes can be carried across or users "
             "must re-enrol, because this is the decision that most affects users."),
            ("Application Cutover Sequencing",
             "the order applications move and what determines it, as a markdown TABLE "
             "with columns Wave, Applications, Rationale, Dependencies."),
            ("Data Quality and Remediation",
             "how data issues found during migration are handled, and what is "
             "explicitly NOT in scope for cleansing."),
        ),
    ),
    SectionSpec(
        id="rollback_risk",
        title="Rollback and Risk Management",
        purpose="What happens when something goes wrong mid-cutover.",
        query_template=f"rollback plan, cutover risk, fallback to incumbent platform, migration risk register {_CTX}",
        subsections=(
            ("Rollback Position",
             "at each cutover point, what the fallback is and how long it takes. A "
             "migration proposal without a credible rollback is not credible."),
            ("Migration Risk Register",
             "risks as a markdown TABLE with columns Risk, Likelihood, Impact, "
             "Mitigation, Owner. Focus on migration-specific risks: data quality, "
             "credential carry-over, integration drift, cutover window overrun."),
            ("Business Continuity During Cutover",
             "what users experience during the move and what downtime, if any, is required."),
        ),
    ),
    SectionSpec(
        id="decommissioning",
        title="Decommissioning and Transition",
        purpose="What happens to the old platform. Usually forgotten, always asked about.",
        query_template=f"decommissioning legacy identity platform, licence retirement, data retention and archival {_CTX}",
        subsections=(
            ("Legacy Platform Decommissioning",
             "the steps to retire the incumbent, and who performs each. State clearly "
             "if decommissioning is out of scope, as it often is."),
            ("Data Retention and Archival",
             "what audit history and identity data is retained from the old platform, "
             "in what form, and for how long. Compliance usually drives this."),
            ("Licence and Contract Implications",
             "what happens to incumbent licences and support contracts. Structure only; "
             "figures stay with the commercial owner."),
        ),
    ),
    SectionSpec(
        id="implementation_approach",
        title="Delivery Approach",
        purpose="How delivery is run: phases, deliverables, responsibilities.",
        query_template=f"migration delivery approach, project deliverables, RACI matrix, testing strategy {_CTX}",
        subsections=(
            ("Delivery Phases",
             "the phases from assessment through cutover to hypercare."),
            ("Project Deliverables",
             "deliverables as a markdown TABLE with columns Deliverable, Description, Phase."),
            # IV's BTPN proposal carries a 34-row Implementation Task List --
            # the largest artefact in that document and the technical heart of
            # an upgrade proposal. Shilpi had nothing comparable; the closest
            # was a RACI, which answers "who" rather than "what".
            ("Implementation Task List",
             "the work itself as a markdown TABLE with EXACTLY these columns: "
             "Task, Comments / Assumptions. One row per discrete task, in "
             "delivery order: requirements gathering and current-state "
             "assessment, architecture and design, environment setup per "
             "environment, configuration migration, data migration, "
             "integration, functional testing, UAT support, cutover and "
             "handover. Comments carry the assumption or dependency that task "
             "rests on. At least 15 rows - this is the section a technical "
             "reviewer reads most closely."),
            ("Testing and Validation Strategy",
             "how the migration is proved before cutover: data reconciliation, "
             "functional parity testing, and UAT. Reconciliation matters most - the "
             "client needs evidence that no identity or entitlement was lost."),
            ("RACI Matrix",
             "responsibilities as a markdown TABLE with columns Activity, "
             "{{ client_name }}, Inspirit Vision"
             "{% for v in iam_vendors %}, {{ v }}{% endfor %}, "
             "using R/A/C/I values. "
             "{% if iam_vendors|length > 1 %}"
             "This is a MULTI-VENDOR engagement: mark R/A/C/I for a vendor "
             "ONLY on activities that vendor's own platform performs; "
             "leave other vendors' cells blank on that row rather than "
             "marking every vendor uniformly."
             "{% endif %}"),
            ("Project Resources",
             "the delivery team as a markdown TABLE with columns Role, "
             "Allocation (Full Time / Part Time), Responsibilities. One row "
             "per role actually needed for THIS engagement's scale and "
             "duration. Do not pad the team with roles this engagement's "
             "size does not warrant."),
            ("Initial Project RAID Log",
             "a starting Risks, Assumptions, Issues and Dependencies "
             "register as a markdown TABLE with columns Type (Risk / "
             "Assumption / Issue / Dependency), Description, Owner, "
             "Mitigation / Action. Draw genuine risks from what discovery "
             "actually states, not generic project-risk boilerplate. At "
             "least 8 rows across all four types."),
            ("Scope Exclusions",
             "what this engagement explicitly does NOT include, as a "
             "bulleted list: standard implementation exclusions discovery "
             "does not usually think to mention (no application-side code "
             "changes, no data cleansing beyond what is explicitly scoped, "
             "no penetration testing) alongside anything the discovery "
             "out-of-scope answer already named. Do not invent an "
             "exclusion the discovery answers contradict."),
        ),
    ),
    SectionSpec(
        id="project_timeline",
        title="Project Management and Timeline",
        purpose="The plan, structured around cutover events rather than build phases.",
        query_template=f"migration timeline, cutover windows, phased delivery plan, wave planning {_CTX}",
        subsections=(
            ("High-Level Migration Plan",
             "the plan as a markdown TABLE with columns Phase, Key Activities, "
             "Duration. Use the engagement duration supplied at discovery exactly."),
            ("Cutover Windows and Milestones",
             "the cutover events, what each moves, and the decision gate before each."),
        ),
    ),
    SectionSpec(
        id="assumptions_responsibilities",
        title="Key Assumptions and Responsibilities",
        purpose="The assumptions the plan rests on and what the client must provide.",
        query_template=f"migration assumptions, client responsibilities, incumbent platform access, dependencies {_CTX}",
        subsections=(
            ("Access to the Incumbent Platform",
             "what access, documentation and vendor support IV requires for the "
             "platform being replaced. Migrations stall here more than anywhere else."),
            ("{{ client_name }} Resource Commitments",
             "the client roles and time required, as a bulleted list."),
            ("Working Assumptions",
             "the delivery assumptions the plan depends on, as a bulleted list."),
            ("Dependencies",
             "external dependencies and the plan impact if each slips."),
        ),
    ),
    SectionSpec(
        id="knowledge_transfer",
        title="Knowledge Transfer and Training",
        purpose="Leaving the client able to run the new platform.",
        query_template=f"knowledge transfer, administrator training on the target platform, handover, hypercare {_CTX}",
        subsections=(
            ("Knowledge Transfer Plan",
             "the KT plan as a markdown TABLE with columns Audience, Topic, Format, Timing."),
            ("Training on the Target Platform",
             "training for administrators moving from the incumbent, framed around "
             "the differences from what they use today."),
            ("Hypercare and Post-Cutover Support",
             "the hypercare period and support model named at discovery."),
        ),
    ),
    SectionSpec(
        id="post_production_support",
        title="Post-Production Support",
        purpose="What happens after cutover: onsite hypercare, then remote AMC, with real SLA commitments.",
        query_template=f"post implementation support, annual maintenance contract, hypercare period, service level agreement, support tiers {_CTX}",
        subsections=(
            ("Post-Implementation Support (Onsite) and AMC (Remote)",
             "the two-phase support model: an initial onsite hypercare period "
             "immediately following cutover, transitioning to a remote Annual "
             "Maintenance Contract. Use the hypercare duration and support "
             "model named at discovery; where discovery does not state one, "
             "say the hypercare duration will be agreed with the client. "
             "Never state a duration as IV's standard unless the EVIDENCE "
             "shows it."),
            ("Scope of Operation Support",
             "what IS and is NOT covered under AMC, as a markdown TABLE with "
             "columns Service, In Scope, Out of Scope."),
            ("Coverage",
             "support coverage tiers as a markdown TABLE with columns Tier, "
             "Coverage Window, Channels."),
            ("Service Level Agreement",
             "response and resolution targets as a markdown TABLE with "
             "columns Severity, Definition, Response Time, Resolution "
             "Target. Use discovery's severity definitions where supplied; "
             "otherwise propose IV's standard targets and say explicitly "
             "these are proposed defaults, not client-confirmed figures."),
        ),
    ),
    SectionSpec(
        id="commercial",
        title="Commercial",
        purpose="Commercial structure and basis. Figures belong to the commercial owner.",
        query_template=f"commercial structure, migration pricing basis, licence bill of quantities, payment milestones {_CTX}",
        subsections=(
            ("Licence Bill of Quantities",
             "licence line items as a markdown TABLE with columns Item, Description, "
             "Quantity, Unit, Basis. Leave price cells as 'To be confirmed'."),
            ("Payment Milestones",
             "milestones as a markdown TABLE with columns Milestone, Trigger, "
             "Percentage, tied to cutover events rather than build phases."),
            ("Commercial Assumptions",
             "travel, taxes, support terms and validity, from the discovery answers."),
        ),
    ),
    SectionSpec(
        id=COMPLIANCE_SECTION_ID,
        title="Compliance Matrix",
        purpose="Requirement-by-requirement coverage assessment against the RFP.",
        query_template="{{ rfp_text }}",
        optional=True,
    ),
]


_TEMPLATES: dict[str, list[SectionSpec]] = {
    "implementation": IMPLEMENTATION_SECTIONS,
    "mss": MSS_SECTIONS,
    "migration": MIGRATION_SECTIONS,
}

VALID_PROPOSAL_TYPES = frozenset(_TEMPLATES.keys())


def get_template(proposal_type: str) -> list[SectionSpec]:
    """Return the ordered section specs for a proposal type.

    Raises ValueError for an unknown type so callers can surface a 400.
    """
    key = (proposal_type or "").strip().lower()
    if key not in _TEMPLATES:
        raise ValueError(
            f"unknown proposal_type {proposal_type!r}; expected one of {sorted(VALID_PROPOSAL_TYPES)}"
        )
    return _TEMPLATES[key]


# ---------------------------------------------------------------------------
# Pass 3 — proposal-depth tiers (long-form depth via STRUCTURED fan-out)
# ---------------------------------------------------------------------------
# Depth is controlled by (a) how many independent drafting calls run per section
# (subsections) and (b) how many retrieval queries run per section (fan-out) —
# NOT by inflating a single call's token cap. ``per_call_max_tokens`` stays at or
# below the module's hard cap so no single call runs away. Cap raised 1500->3500
# to let full-depth subsections run longer toward 100+ pp; anti-spiral guardrails
# (frequency_penalty, max_retries, truncation guard) remain intact.
_PER_CALL_TOKEN_HARD_CAP = 3500


@dataclass(frozen=True)
class DepthTier:
    """A proposal-depth tier plan.

    subsections_per_section : independent drafting LLM calls per section
    retrieval_fanout        : retrieval queries issued per section (merged/deduped)
    include_appendices      : whether the DOCX gets the appendix pack
    per_call_max_tokens     : per-call token budget (never above the hard cap)
    """

    name: str
    subsections_per_section: int
    retrieval_fanout: int
    include_appendices: bool
    per_call_max_tokens: int

    def __post_init__(self) -> None:
        # Enforce the hard cap defensively — depth must never raise per-call
        # token budgets irresponsibly (a Pass 3 hard constraint).
        if self.per_call_max_tokens > _PER_CALL_TOKEN_HARD_CAP:
            object.__setattr__(self, "per_call_max_tokens", _PER_CALL_TOKEN_HARD_CAP)


# Facets used to split a section into independent, focused drafting calls when a
# tier requests multiple subsections. Each facet is drafted by its own LLM call
# (same per-call token cap) then assembled under an H2 subheading.
SUBSECTION_FACETS: list[tuple[str, str]] = [
    ("Overview", "a high-level overview: objectives, scope and the value delivered"),
    ("Detailed Design", "the detailed technical design: components, connectors, workflows and configuration specifics"),
    ("Considerations & Dependencies", "operational considerations, dependencies, assumptions and risks to manage"),
    ("Security & Compliance Considerations", "security architecture, data protection, and regulatory/compliance considerations specific to this facet"),
    ("Testing, Validation & Quality Assurance", "the testing approach, validation criteria, and acceptance/quality-assurance activities relevant to this facet"),
    ("Change Management, Training & Adoption", "change management, end-user training, communication, and adoption support relevant to this facet"),
]


DEPTH_TIERS: dict[str, DepthTier] = {
    # brief: leaner than default — single call, single query, tighter budget.
    "brief": DepthTier("brief", subsections_per_section=1, retrieval_fanout=1,
                       include_appendices=False, per_call_max_tokens=900),
    # standard: preserves existing Pass 1/2 behaviour exactly (the safe default).
    "standard": DepthTier("standard", subsections_per_section=1, retrieval_fanout=1,
                          include_appendices=False, per_call_max_tokens=1500),
    # full: multi-subsection drafting + wider retrieval fan-out + appendix pack.
    # UNCHANGED from before this patch — existing callers keep the same output.
    "full": DepthTier("full", subsections_per_section=3, retrieval_fanout=3,
                      include_appendices=True, per_call_max_tokens=2500),
    # deep: NEW — the #1 remaining length lever toward 100+ pp. Uses all 6
    # SUBSECTION_FACETS (was clamped to 3 by len(SUBSECTION_FACETS) before this
    # patch — adding facets was required, raising subsections_per_section alone
    # would have been another no-op, same trap as the earlier token-cap gotcha).
    # retrieval_fanout kept at 4 (not 6) to bound embed+retrieve calls/cost;
    # the 6 subsection drafts all read from the same merged evidence set.
    # per_call_max_tokens stays at the hard cap — depth grows via more calls,
    # never a bigger single call. Opt-in via proposal_depth="deep"; measure
    # page count and iterate (more facets / higher fanout) if still short of 100+.
    "deep": DepthTier("deep", subsections_per_section=6, retrieval_fanout=4,
                      include_appendices=True, per_call_max_tokens=2500),
}

DEFAULT_DEPTH = "standard"
VALID_DEPTHS = frozenset(DEPTH_TIERS.keys())


def get_depth_tier(proposal_depth: str | None) -> DepthTier:
    """Resolve a depth name to its plan, falling back to the safe default.

    Unknown/missing values return the ``standard`` tier so existing callers that
    omit ``proposal_depth`` keep their current behaviour.
    """
    key = (proposal_depth or "").strip().lower()
    return DEPTH_TIERS.get(key, DEPTH_TIERS[DEFAULT_DEPTH])
