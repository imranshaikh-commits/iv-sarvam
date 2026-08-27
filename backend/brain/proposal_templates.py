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

from jinja2 import Template

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
            ("", "the engagement in one page: what Amlak is buying, why now, what "
                 "changes for the business, and the shape of the delivery. "
                 "Continuous prose, no sub-headings, no bullet lists."),
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
             "outcome metric the evidence does not support."),
            ("Case Studies",
             "two or three short case studies: client, problem, what was delivered, "
             "outcome. Keep each to about 80 words."),
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
            # IV's own sizing tables are ELEVEN columns wide and identical across
            # all four environments. Run 9 produced 6, 4, 3 and 3 columns from an
            # invented column list, which is why they read thinner than the human
            # original even where the numbers were right. These are IV's actual
            # headers, taken from the Amlak proposal.
            ("Proposed Production Hardware Sizing",
             "production sizing as a markdown TABLE with EXACTLY these columns: "
             "#, Server Category, Quantity, CPU per node, Memory per node (GB), "
             "Storage per node (GB), DB Storage (GB), Operating System, "
             "Application Server, Database, Remarks. "
             "One row per server category. Use the discovery sizing figures "
             "exactly; write N/A where a column does not apply, never leave a "
             "cell blank. Remarks names the node split (e.g. '2 x UI, 2 x Task') "
             "and any RAID or clustering requirement."),
            ("Proposed DR Hardware Sizing",
             "disaster recovery sizing as a markdown TABLE with the SAME eleven "
             "columns as production. DR mirrors production unless discovery says "
             "otherwise. Follow the table with one short paragraph on the "
             "replication approach between sites."),
            ("Proposed UAT Hardware Sizing",
             "UAT sizing as a markdown TABLE with the SAME eleven columns. UAT is "
             "normally reduced from production; use the discovery figures."),
            ("Proposed Development Hardware Sizing",
             "development sizing as a markdown TABLE with the SAME eleven columns. "
             "Development is the smallest environment, typically a single node."),
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
            ("RACI Legend",
             "a short markdown TABLE with columns Role, Description defining "
             "R = Responsible, A = Accountable, C = Consulted, I = Informed."),
            ("RACI - Project Governance",
             "governance responsibilities as a markdown TABLE with columns "
             "Deliverable / Activity, Inspirit Vision, {{ client_name }}, "
             "Description / Comments. Cover ways of working, steering committee, "
             "project tools, status reporting, change control, and risk and issue "
             "management. At least 10 rows."),
            ("RACI - Delivery Activities",
             "delivery responsibilities as a markdown TABLE with the same four "
             "columns. Cover scope definition, product acquisition, environment "
             "provisioning, design, build, integration, testing, UAT, cutover and "
             "handover. Use the responsibilities supplied at discovery. At least "
             "12 rows."),
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
            ("Tranche 1 - Foundation",
             "foundation scope as a markdown TABLE with columns Sr#, Category, "
             "Milestone, Success Criteria. Cover licence delivery, kickoff, "
             "consulting and design workshops, environment installation "
             "(development, QA, production, DR), core configuration and "
             "authoritative source onboarding. Success Criteria states what "
             "evidence closes that milestone. At least 10 rows."),
            ("Tranche 2 - Lifecycle Management and Initial Applications",
             "lifecycle automation plus the first application batch, as a markdown "
             "TABLE with the same four columns. Use the application counts named "
             "at discovery."),
            ("Tranche 3 - Access Certification and Application Onboarding",
             "certification campaigns plus continued onboarding, as a markdown "
             "TABLE with the same four columns, in the batch size named at "
             "discovery."),
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
             "production sizing as a markdown TABLE with columns Component, Role, "
             "vCPU, Memory, Storage, Operating System. Use the discovery figures exactly."),
            ("Proposed DR and Non-Production Sizing",
             "DR, UAT and development sizing as a markdown TABLE, same columns."),
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
            ("Testing and Validation Strategy",
             "how the migration is proved before cutover: data reconciliation, "
             "functional parity testing, and UAT. Reconciliation matters most - the "
             "client needs evidence that no identity or entitlement was lost."),
            ("RACI Matrix",
             "responsibilities as a markdown TABLE with columns Activity, "
             "Inspirit Vision, {{ client_name }}, Vendor, using R/A/C/I values."),
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
