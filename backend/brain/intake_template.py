"""
Sprint 5 Pass 1 — structured intake (discovery interview) schema as data.

The interview is a list of buckets; each bucket holds questions. A question is a
plain dict so it serializes straight to JSON for the intake endpoints. Answers
are stored flat in a jsonb keyed by question ``id`` (all ids are globally unique
so buckets never collide).

Conditional questions carry a ``conditional`` rule string like
"proposal_type==mss". ``get_intake_template(proposal_type)`` filters out any
question whose rule does not hold for the given proposal_type.

Pure data + pure functions — NO network, NO secrets, NO import of app.py.
"""

from __future__ import annotations

TEMPLATE_VERSION = "2026-07-16.1"

# Recognised question types (for validation / UI hints).
QUESTION_TYPES = frozenset({
    "text", "textarea", "number", "select", "multiselect",
    "boolean", "rfp_text", "logo", "date",
})

PROPOSAL_TYPES = ("implementation", "mss", "migration")


def _q(qid, label, qtype, required=False, options=None, conditional=None, note=None):
    q = {"id": qid, "label": label, "type": qtype, "required": bool(required)}
    if options is not None:
        q["options"] = list(options)
    if conditional is not None:
        q["conditional"] = conditional
    if note is not None:
        q["note"] = note
    return q


# --- bucket definitions -----------------------------------------------------
# Each bucket: {"id", "title", "questions": [ ... ]}. Every question id is unique
# across all buckets so a flat answers jsonb never has key collisions.
_BUCKETS: list[dict] = [
    {
        "id": "client",
        "title": "Client",
        "questions": [
            _q("client_name", "Client / organisation name", "text", required=True),
            _q("industry", "Industry / sector", "text", required=True),
            _q("country", "Country / region", "text"),
        ],
    },
    {
        "id": "engagement",
        "title": "Engagement",
        "questions": [
            _q("iam_vendor", "Primary IAM vendor / platform", "text", required=True),
            _q("proposal_type", "Proposal type", "select", required=True,
               options=list(PROPOSAL_TYPES)),
            _q("deal_size_bucket", "Deal size", "select",
               options=["small", "medium", "large", "enterprise"]),
            _q("year", "Engagement year", "number"),
        ],
    },
    {
        "id": "scale",
        "title": "Scale & Volumetrics",
        "questions": [
            _q("user_count", "Total user / identity count", "number"),
            _q("app_count", "Number of applications in scope", "number"),
            _q("identity_types", "Identity types", "multiselect",
               options=["workforce", "customer", "CIAM", "mixed"]),
            _q("target_integrations", "Target integrations / connectors", "textarea"),
        ],
    },
    {
        "id": "scope",
        "title": "Scope",
        "questions": [
            _q("rfp_text", "RFP / tender text", "rfp_text"),
            _q("business_objectives", "Business objectives / drivers", "textarea", required=True),
            _q("in_scope", "In scope", "textarea"),
            _q("out_of_scope", "Out of scope", "textarea"),
            _q("current_state", "Current state summary", "textarea"),
        ],
    },
    {
        "id": "architecture",
        "title": "Architecture",
        "questions": [
            _q("deployment_model", "Deployment model", "select",
               options=["on-prem", "cloud", "saas", "hybrid"]),
            _q("required_diagram_types", "Required diagram types", "multiselect",
               options=["solution/reference", "deployment", "target_reference",
                        "security", "integration/joiner flow",
                        "auth/customer journey", "migration phases"]),
            _q("diagram_count", "Number of diagrams expected", "number"),
            _q("hardware_sizing_inputs", "Hardware sizing inputs", "textarea"),
            _q("ha_dr_requirements", "HA / DR requirements", "textarea"),
            _q("rto_rpo", "RTO / RPO targets", "text"),
            _q("security_architecture_needs", "Security architecture needs", "textarea"),
            _q("cluster_topology", "Cluster topology", "textarea"),
        ],
    },
    {
        "id": "migration",
        "title": "Migration",
        "questions": [
            _q("is_migration", "Is this a migration?", "boolean"),
            _q("source_systems", "Source system(s) to migrate from", "textarea",
               conditional="proposal_type==migration"),
            _q("migration_phases", "Migration phases", "textarea",
               conditional="proposal_type==migration"),
        ],
    },
    {
        "id": "integration",
        "title": "Integration",
        "questions": [
            _q("integration_hrms", "HRMS / authoritative source", "text"),
            _q("ad_exchange", "AD / Exchange details", "text"),
            _q("idp_sso", "IdP / SSO details", "text"),
            _q("apps_to_onboard", "Applications to onboard", "textarea"),
        ],
    },
    {
        "id": "compliance",
        "title": "Compliance & Regulatory",
        "questions": [
            _q("regulations", "Applicable regulations", "textarea"),
            _q("certifications", "Required certifications", "textarea"),
            _q("data_residency", "Data residency requirements", "text"),
            _q("pii_handling", "PII handling requirements", "textarea"),
            _q("sod", "Segregation of duties (SoD) required?", "boolean"),
            _q("access_review_cadence", "Access review cadence", "text"),
        ],
    },
    {
        "id": "timeline",
        "title": "Timeline",
        "questions": [
            _q("duration", "Engagement duration", "text"),
            _q("timeline_milestones", "Key milestones", "textarea"),
            _q("go_live_date", "Target go-live date", "date"),
        ],
    },
    {
        "id": "mss",
        "title": "Managed Support (MSS)",
        "questions": [
            _q("sla_tiers", "SLA tiers", "textarea", conditional="proposal_type==mss"),
            _q("coverage_hours", "Coverage hours", "text", conditional="proposal_type==mss"),
            _q("personnel", "Support personnel / staffing", "textarea", conditional="proposal_type==mss"),
            _q("contract_type", "Contract type", "text", conditional="proposal_type==mss"),
            _q("fees", "Fees / commercial model", "textarea", conditional="proposal_type==mss"),
            _q("mss_reporting_cadence", "Reporting cadence", "text", conditional="proposal_type==mss"),
            _q("mandatory_vs_on_demand_services", "Mandatory vs on-demand services", "textarea",
               conditional="proposal_type==mss"),
        ],
    },
    {
        "id": "branding",
        "title": "Branding",
        "questions": [
            _q("client_logo", "Client logo", "logo",
               note="If not provided, sourced online and confirmed with the client before use."),
        ],
    },
    {
        "id": "depth",
        "title": "Depth",
        "questions": [
            _q("proposal_depth", "Proposal depth", "select", required=True,
               options=["brief", "standard", "full"]),
        ],
    },
    {
        "id": "evidence",
        "title": "Evidence",
        "questions": [
            _q("case_studies_to_highlight", "Case studies to highlight", "textarea"),
            _q("partner_positioning", "Partner / vendor positioning", "textarea"),
        ],
    },
    {
        "id": "submission",
        "title": "Submission Constraints",
        "questions": [
            _q("due_date", "Submission due date", "date"),
            _q("required_format", "Required format", "select",
               options=["docx", "pdf", "both"]),
            _q("page_limit", "Page limit", "number"),
            _q("annexures", "Required annexures", "textarea"),
            _q("language", "Language", "text"),
            _q("currency", "Currency", "text"),
            _q("validity_period", "Proposal validity period", "text"),
        ],
    },
    {
        "id": "audience",
        "title": "Audience & Tone",
        "questions": [
            _q("audience", "Primary audience", "select",
               options=["executive", "technical", "commercial"]),
            _q("buyer_personas", "Buyer personas", "textarea"),
            _q("technical_depth_level", "Technical depth level", "select",
               options=["low", "medium", "high"]),
        ],
    },
    {
        "id": "win_themes",
        "title": "Client Pain & Win Themes",
        "questions": [
            _q("pain_points", "Client pain points", "textarea"),
            _q("decision_criteria", "Decision criteria", "textarea"),
            _q("differentiators", "Differentiators", "textarea"),
        ],
    },
    {
        "id": "current_systems",
        "title": "Current-State Systems",
        "questions": [
            _q("existing_iam_platform", "Existing IAM platform", "text"),
            _q("versions", "Product versions", "text"),
            _q("tenants", "Tenants", "text"),
            _q("directories", "Directories", "text"),
            _q("current_hrms", "HRMS", "text"),
            _q("current_idp", "IdP", "text"),
            _q("source_of_truth", "Source of truth", "text"),
        ],
    },
    {
        "id": "target_constraints",
        "title": "Target Architecture Constraints",
        "questions": [
            _q("regions", "Regions", "text"),
            _q("network_zones", "Network zones", "text"),
            _q("envs", "Environments", "multiselect", options=["prod", "uat", "dev"]),
        ],
    },
    {
        "id": "nfrs",
        "title": "Non-Functional Requirements",
        "questions": [
            _q("availability", "Availability", "text"),
            _q("scalability", "Scalability", "text"),
            _q("performance", "Performance", "text"),
            _q("monitoring", "Monitoring", "text"),
            _q("audit", "Audit", "text"),
            _q("data_retention", "Data retention", "text"),
        ],
    },
    {
        "id": "delivery_model",
        "title": "Delivery Model",
        "questions": [
            _q("delivery_phases", "Delivery phases", "textarea"),
            _q("delivery_milestones", "Delivery milestones", "textarea"),
            _q("governance", "Governance", "textarea"),
            _q("raci", "RACI", "textarea"),
            _q("client_responsibilities", "Client responsibilities", "textarea"),
            _q("dependencies", "Dependencies", "textarea"),
            _q("assumptions", "Assumptions", "textarea"),
        ],
    },
    {
        "id": "commercials",
        "title": "Commercials",
        "questions": [
            _q("license_included", "License included?", "boolean"),
            _q("pricing_model", "Pricing model", "select", options=["fixed", "t&m"]),
            _q("payment_milestones", "Payment milestones", "textarea"),
            _q("taxes", "Taxes", "text"),
            _q("travel", "Travel", "text"),
            _q("support_terms", "Support terms", "textarea"),
        ],
    },
    {
        "id": "post_go_live",
        "title": "Post-Go-Live",
        "questions": [
            _q("training", "Training", "textarea"),
            _q("kt", "Knowledge transfer", "textarea"),
            _q("hypercare", "Hypercare", "text"),
            _q("support_model", "Support model", "textarea"),
            _q("post_sla", "SLA", "textarea"),
            _q("postgolive_reporting_cadence", "Reporting cadence", "text"),
        ],
    },
    {
        "id": "reuse_controls",
        "title": "Reuse Controls",
        "questions": [
            _q("case_studies_include", "Case studies to include", "textarea"),
            _q("case_studies_exclude", "Case studies to exclude", "textarea"),
            _q("similar_projects", "Similar projects", "textarea"),
            _q("vendor_partner_positioning", "Vendor / partner positioning", "textarea"),
        ],
    },
]


def _conditional_applies(rule: str | None, proposal_type: str | None) -> bool:
    """Evaluate a simple 'field==value' conditional against the proposal_type.

    Only the proposal_type field is supported today. A question with no rule
    always applies. A rule that references proposal_type is satisfied only when
    the given proposal_type matches (so unknown/None excludes conditionals)."""
    if not rule:
        return True
    if "==" in rule:
        field, _, value = rule.partition("==")
        if field.strip() == "proposal_type":
            return (proposal_type or "").strip().lower() == value.strip().lower()
    # Unknown rule form: be conservative and exclude.
    return False


def get_intake_template(proposal_type: str | None = None) -> dict:
    """Return the applicable interview for a proposal type.

    Buckets with no applicable questions are dropped. Returns:
    {"template_version", "proposal_type", "buckets": [{id, title, questions}]}.
    """
    buckets_out = []
    for bucket in _BUCKETS:
        questions = [
            dict(q) for q in bucket["questions"]
            if _conditional_applies(q.get("conditional"), proposal_type)
        ]
        if questions:
            buckets_out.append({"id": bucket["id"], "title": bucket["title"], "questions": questions})
    return {
        "template_version": TEMPLATE_VERSION,
        "proposal_type": proposal_type,
        "buckets": buckets_out,
    }


def iter_questions(proposal_type: str | None = None):
    """Yield every applicable question (flattened) for the proposal type."""
    for bucket in get_intake_template(proposal_type)["buckets"]:
        yield from bucket["questions"]


def required_question_ids(proposal_type: str | None = None) -> list[str]:
    """Ids of applicable required questions for the given proposal type."""
    return [q["id"] for q in iter_questions(proposal_type) if q.get("required")]


def _is_answered(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True  # numbers, booleans (incl. False) count as answered


def missing_required(answers: dict, proposal_type: str | None = None) -> list[str]:
    """Return ids of applicable required questions that are unanswered.

    proposal_type falls back to the value inside answers when not passed
    explicitly, so validation respects conditional applicability."""
    answers = answers or {}
    ptype = proposal_type or answers.get("proposal_type")
    return [qid for qid in required_question_ids(ptype)
            if not _is_answered(answers.get(qid))]
