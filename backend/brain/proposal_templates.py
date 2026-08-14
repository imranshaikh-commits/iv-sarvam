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

from dataclasses import dataclass

from jinja2 import Template

# Sentinel section id: this section is produced by the compliance-matrix
# pipeline (run_compliance_matrix) rather than by free-form LLM drafting.
COMPLIANCE_SECTION_ID = "compliance_matrix"


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
        """Subsection (heading, instruction) pairs with context substituted."""
        out: list[tuple[str, str]] = []
        for heading, instruction in self.subsections:
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
        # Deliberately no subsections: IV's executive summary is continuous prose
        # on a single page. Splitting it into facets is what made it bloat.
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
             "engagements comparable to this one by vendor, sector and scope. Name the "
             "sector and the nature of the work. Do NOT name a client unless the "
             "discovery answers explicitly authorise it."),
            ("Case Studies",
             "two or three short case studies: the problem, what was delivered, the outcome."),
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
             "what is IN scope, as a structured list: the capability areas, application "
             "counts, identity types and environments supplied at discovery."),
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
             "how the platform answers the visibility question: aggregation, correlation "
             "and the single view of entitlement across connected systems."),
            ("Who Should Have Access",
             "role modelling, birthright access, policy and segregation-of-duties controls."),
            ("Who Had Access",
             "historical audit: what was granted, by whom, when, and revocation evidence."),
            ("Access Certification",
             "certification campaign types scoped to the reviewers named at discovery."),
            ("Provisioning and Lifecycle Management",
             "automated joiner, mover and leaver flows and the connectors that fulfil them."),
            ("Segregation of Duties",
             "the SoD policy model, and detective versus preventive control points."),
            ("Reporting and Analytics",
             "standard and custom reporting, dashboards and audit evidence production."),
            ("Connectors and Integrations",
             "connector coverage for the systems named at discovery, and the approach "
             "where no out-of-box connector exists."),
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
            ("Proposed Production Hardware Sizing",
             "production sizing as a TABLE with columns Component, Role, vCPU, Memory, "
             "Storage, Operating System. Use the sizing figures supplied at discovery "
             "exactly. Split by server role where the discovery answer splits them. "
             "Output a markdown table, not prose."),
            ("Proposed DR Hardware Sizing",
             "disaster recovery sizing as a markdown TABLE, same columns as production. "
             "State the replication approach between sites."),
            ("Proposed UAT Hardware Sizing",
             "UAT sizing as a markdown TABLE, same columns."),
            ("Proposed Development Hardware Sizing",
             "development environment sizing as a markdown TABLE, same columns."),
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
            ("RACI Matrix",
             "responsibilities as a markdown TABLE with columns Activity, Inspirit Vision, "
             "{{ client_name }}, Vendor, using R/A/C/I values. Use the responsibilities "
             "supplied at discovery."),
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
            ("Tranche 1 - Foundation",
             "foundation scope: installation, environment build, core configuration, "
             "authoritative source onboarding."),
            ("Tranche 2 - Lifecycle Management and Initial Applications",
             "lifecycle automation plus the first application batch named at discovery."),
            ("Tranche 3 - Access Certification and Application Onboarding",
             "certification campaigns plus continued onboarding in the batch size named "
             "at discovery."),
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
            ("Working Assumptions",
             "the delivery assumptions the plan depends on, as a bulleted list. Use the "
             "assumptions supplied at discovery."),
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
            ("Training and Post-Production Support",
             "administrator training, the hypercare period and the support model named "
             "at discovery."),
        ),
    ),
    SectionSpec(
        id="commercial",
        title="Commercial",
        purpose="Commercial structure and basis. Figures belong to the commercial owner.",
        query_template=f"commercial structure, license bill of quantities, implementation pricing basis, payment milestones, resident engineer {_CTX}",
        subsections=(
            ("Licence Bill of Quantities",
             "the licence line items as a markdown TABLE with columns Item, Description, "
             "Quantity, Unit, Basis. Leave price cells as 'To be confirmed' - Shilpi does "
             "NOT invent commercial figures."),
            ("Total Bill of Quantities",
             "the combined BOQ as a markdown TABLE with the same discipline on figures."),
            ("Payment Milestones",
             "payment milestones as a markdown TABLE with columns Milestone, Trigger, "
             "Percentage. Use the milestone structure supplied at discovery. Percentages "
             "only where discovery supplies them, otherwise 'To be confirmed'."),
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


_TEMPLATES: dict[str, list[SectionSpec]] = {
    "implementation": IMPLEMENTATION_SECTIONS,
    "mss": MSS_SECTIONS,
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
