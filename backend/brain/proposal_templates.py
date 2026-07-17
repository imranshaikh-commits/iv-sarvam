"""
Sprint 5 — proposal section templates.

Ordered section definitions for the two proposal types Sarvam produces:
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

    def render_query(self, context: dict) -> str:
        """Render this section's retrieval query from the given context."""
        rendered = Template(self.query_template).render(**context)
        # Collapse whitespace so the embedding input is clean.
        return " ".join(rendered.split())


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


IMPLEMENTATION_SECTIONS: list[SectionSpec] = [
    SectionSpec(
        id="executive_summary",
        title="Executive Summary",
        purpose="High-level overview of the proposed engagement, value, and outcomes.",
        query_template=f"executive summary, engagement value, business outcomes and objectives {_CTX}. {{{{ rfp_text[:400] }}}}",
    ),
    SectionSpec(
        id="client_context",
        title="Client Context & Objectives",
        purpose="Restate the client's situation, drivers, and stated objectives.",
        query_template=f"client context, business drivers, current state and objectives {_CTX}. {{{{ rfp_text[:400] }}}}",
    ),
    SectionSpec(
        id="solution_architecture",
        title="Proposed Solution Architecture",
        purpose="Describe the target IAM solution architecture and key components.",
        query_template=f"proposed solution architecture, target state design, {{{{ iam_vendor }}}} components and connectors {_CTX}",
    ),
    SectionSpec(
        id="technical_approach",
        title="Technical Approach",
        purpose="Detail the technical approach, capabilities, and configuration.",
        query_template=f"technical approach, {{{{ iam_vendor }}}} capabilities, provisioning, access certification, workflows and configuration {_CTX}",
    ),
    SectionSpec(
        id="implementation_methodology",
        title="Implementation Methodology & Phasing",
        purpose="Lay out the delivery methodology, phases, and milestones.",
        query_template=f"implementation methodology, delivery phases, milestones, timeline and phasing {_CTX}",
    ),
    SectionSpec(
        id="integration_points",
        title="Integration Points",
        purpose="Enumerate systems, applications, and integration touch-points.",
        query_template=f"integration points, target applications, source systems, connectors and APIs {_CTX}",
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
# below the module's existing 1500 hard cap so no single call runs away.
_PER_CALL_TOKEN_HARD_CAP = 1500


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
]


DEPTH_TIERS: dict[str, DepthTier] = {
    # brief: leaner than default — single call, single query, tighter budget.
    "brief": DepthTier("brief", subsections_per_section=1, retrieval_fanout=1,
                       include_appendices=False, per_call_max_tokens=900),
    # standard: preserves existing Pass 1/2 behaviour exactly (the safe default).
    "standard": DepthTier("standard", subsections_per_section=1, retrieval_fanout=1,
                          include_appendices=False, per_call_max_tokens=1500),
    # full: multi-subsection drafting + wider retrieval fan-out + appendix pack.
    "full": DepthTier("full", subsections_per_section=3, retrieval_fanout=3,
                      include_appendices=True, per_call_max_tokens=1500),
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
