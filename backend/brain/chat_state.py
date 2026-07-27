"""
Chat conversation state for the OWUI-facing /v1/chat/completions endpoint.

WHY THIS EXISTS
---------------
Open WebUI speaks the OpenAI chat-completions protocol, which is stateless: it
has no field for our ``intake_session_id`` and no server-side session handle.
The original gate assumed an "OWUI pipe" would inject that id; no such pipe was
ever built, so ``parse_intake_session_id()`` returned None on EVERY request from
the UI. The gate therefore fired unconditionally and replied with the same
deterministic Stage-1 opener forever — the interview could never advance and the
retrieval/drafting path below the gate was unreachable from the chat UI.

THE FIX
-------
OWUI *does* echo the full prior ``messages`` array back on every request. That is
our state channel — free, and already in the payload. We encode a compact state
marker into each assistant reply as an HTML comment (invisible once the markdown
is rendered) and recover it by scanning the message history backwards.

    <!--sarvam:v1;mode=interview;session=<uuid>;bucket=3-->

No OWUI plugin, no schema change, no migration.

This module is PURE: data + pure functions, no network, no secrets, no import of
app.py — so it is fully unit-testable offline (same contract as
``intake_template.py`` and ``proposal_templates.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

# --- modes ------------------------------------------------------------------
# router       : we have asked what the user wants to do; awaiting their choice.
# interview    : walking the discovery buckets; ``bucket`` is the index awaiting an
#                answer, ``session`` is the intake session id.
# architecture : discovery is done; a diagram has been proposed and we are waiting
#                for the user to approve or reject it. This is the V1 human-in-loop
#                gate — drafting must not start until it is passed.
# drafting     : architecture approved; the full proposal can now be generated.
# vault        : free-form Q&A grounded in the proposal corpus (the existing RAG
#                path). Sticky, so follow-up questions stay in RAG mode.
MODE_ROUTER = "router"
MODE_INTERVIEW = "interview"
MODE_ARCHITECTURE = "architecture"
MODE_DRAFTING = "drafting"
MODE_VAULT = "vault"

VALID_MODES = frozenset({
    MODE_ROUTER, MODE_INTERVIEW, MODE_ARCHITECTURE, MODE_DRAFTING, MODE_VAULT,
})

# Router choices.
CHOICE_NEW_PROPOSAL = "new_proposal"
CHOICE_VAULT = "vault"
CHOICE_DISCUSS = "discuss"

MARKER_VERSION = "v1"

# Legacy marker form (v1, shipped in 63170cd): an HTML comment. Open WebUI does
# NOT strip HTML comments — it escapes them, so users saw the raw marker text in
# the chat. Kept in the decoder only, so threads started before the fix keep
# working; never emitted any more.
_LEGACY_MARKER_RE = re.compile(r"<!--\s*sarvam:v1;([^>]*?)-->", re.IGNORECASE | re.DOTALL)

# Current marker form: the payload is encoded as zero-width characters, which
# every renderer treats as invisible whitespace, so nothing is displayed while
# the bytes still survive the OWUI messages round-trip.
_ZW_ZERO = "\u200b"      # ZERO WIDTH SPACE          -> bit 0
_ZW_ONE = "\u200c"       # ZERO WIDTH NON-JOINER     -> bit 1
_ZW_FENCE = "\u200d"     # ZERO WIDTH JOINER         -> payload delimiter
_ZW_RE = re.compile(f"{_ZW_FENCE}([{_ZW_ZERO}{_ZW_ONE}]*){_ZW_FENCE}")
_ZW_ANY = re.compile(f"{_ZW_FENCE}[{_ZW_ZERO}{_ZW_ONE}]*{_ZW_FENCE}")


def _zw_encode(payload: str) -> str:
    bits = "".join(f"{byte:08b}" for byte in payload.encode("utf-8"))
    body = "".join(_ZW_ONE if b == "1" else _ZW_ZERO for b in bits)
    return f"{_ZW_FENCE}{body}{_ZW_FENCE}"


def _zw_decode(text: str) -> str | None:
    matches = _ZW_RE.findall(text or "")
    if not matches:
        return None
    bits = "".join("1" if ch == _ZW_ONE else "0" for ch in matches[-1])
    if not bits or len(bits) % 8:
        return None
    try:
        return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _parse_fields(raw: str) -> "ChatState | None":
    fields: dict[str, str] = {}
    for pair in raw.split(";"):
        if "=" in pair:
            k, _, v = pair.partition("=")
            fields[k.strip().lower()] = v.strip()

    mode = fields.get("mode", "")
    if mode not in VALID_MODES:
        return None
    session = fields.get("session") or None
    proposal = fields.get("proposal") or None
    try:
        bucket = int(fields.get("bucket", "0"))
    except ValueError:
        bucket = 0
    return ChatState(mode=mode, session=session, bucket=max(0, bucket), proposal=proposal)


@dataclass(frozen=True)
class ChatState:
    """Recovered conversation state.

    ``bucket``   is meaningful only in interview mode.
    ``session``  is the intake_sessions id (survives into later modes so the
                 answers stay reachable).
    ``proposal`` is the generated_proposals id, set once discovery completes and
                 carried through architecture review and drafting.
    """

    mode: str = MODE_ROUTER
    session: str | None = None
    bucket: int = 0
    proposal: str | None = None

    def advanced(self) -> "ChatState":
        """Same state, pointing at the next interview bucket."""
        return replace(self, bucket=self.bucket + 1)


# --- marker encode / decode -------------------------------------------------
def encode_marker(state: ChatState) -> str:
    """Render an invisible state marker to append to an assistant message.

    Encoded as zero-width characters so the user sees nothing at all. (The first
    implementation used an HTML comment; OWUI escapes those and displayed the
    raw marker in the chat.)
    """
    parts = [f"mode={state.mode}"]
    if state.session:
        parts.append(f"session={state.session}")
    if state.proposal:
        parts.append(f"proposal={state.proposal}")
    if state.mode == MODE_INTERVIEW:
        parts.append(f"bucket={state.bucket}")
    return _zw_encode(f"{MARKER_VERSION};{';'.join(parts)}")


def decode_marker(text: str) -> ChatState | None:
    """Parse the last state marker in ``text``; None if absent/unparseable.

    Accepts the current zero-width form and the legacy HTML-comment form, so
    threads started before the fix continue to advance.
    """
    if not text:
        return None

    payload = _zw_decode(text)
    if payload:
        raw = payload.split(";", 1)[1] if payload.startswith(f"{MARKER_VERSION};") else payload
        state = _parse_fields(raw)
        if state is not None:
            return state

    legacy = _LEGACY_MARKER_RE.findall(text)
    if legacy:
        return _parse_fields(legacy[-1])
    return None


def strip_markers(text: str) -> str:
    """Remove state markers of either form (for logging/tests)."""
    return _ZW_ANY.sub("", _LEGACY_MARKER_RE.sub("", text or ""))


def find_chat_state(messages: list[dict]) -> ChatState | None:
    """Recover the most recent state marker from assistant turns.

    Scans backwards so the newest marker wins. Returns None for a fresh thread,
    which the caller treats as "show the router".
    """
    for m in reversed(messages or []):
        if m.get("role") != "assistant":
            continue
        content = m.get("content", "")
        if isinstance(content, list):  # multimodal shape
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )
        state = decode_marker(str(content))
        if state is not None:
            return state
    return None


# --- router -----------------------------------------------------------------
ROUTER_MESSAGE = """Hi, I'm **Sarvam**, Inspirit Vision's proposal architect. What would you like to do?

**1. Start a new proposal / RFP** — I'll run a short discovery interview, propose an architecture for your approval, then draft the full document grounded in IV's past work.

**2. Search past proposals** — ask me anything about the proposals already in the vault (clients, vendors, architectures, scope, how we've positioned something before) and I'll answer with citations.

**3. Something else** — a question, a second opinion, or just thinking out loud.

Reply with **1**, **2**, or **3** — or just tell me in your own words."""


_NEW_PROPOSAL_HINTS = (
    "new proposal", "new rfp", "start a proposal", "start new", "draft a proposal",
    "draft proposal", "new deal", "new bid", "respond to an rfp", "respond to rfp",
    "write a proposal", "create a proposal", "build a proposal",
)
_VAULT_HINTS = (
    "vault", "past proposal", "previous proposal", "prior proposal", "old proposal",
    "search", "retrieve", "look up", "lookup", "past work", "previous work",
    "history", "archive", "already done", "have we", "did we", "what did we",
)
_DISCUSS_HINTS = (
    "something else", "discuss", "just talk", "question", "second opinion",
    "thinking out loud", "brainstorm", "advice", "chat",
)

_RESTART_HINTS = (
    "start over", "restart", "reset", "scrap that", "start again",
    "new proposal instead", "go back to the start", "back to the menu", "main menu",
    "start from scratch", "cancel that", "abort",
)

# A restart request is always SHORT. Without this cap, substring matching turns
# any long answer containing an incidental hint word into an accidental reset —
# e.g. "password reset volume overwhelming the helpdesk" (a normal IAM pain
# point) matched "reset" and silently threw away a 15-area interview.
_RESTART_MAX_WORDS = 8


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", (text or "").strip().lower())


def classify_router_choice(text: str) -> str | None:
    """Map a router reply to a choice. None when genuinely ambiguous.

    Deliberately deterministic and conservative — no LLM call, and no guessing.
    An unrecognised reply re-prompts rather than silently picking a branch and
    dragging the user into the wrong flow (the failure mode we just fixed).
    """
    raw = (text or "").strip()
    if not raw:
        return None

    # Bare numeric / lettered choice.
    bare = _normalise(raw)
    first_token = bare.split()[0] if bare.split() else ""
    if first_token in {"1", "one", "a"}:
        return CHOICE_NEW_PROPOSAL
    if first_token in {"2", "two", "b"}:
        return CHOICE_VAULT
    if first_token in {"3", "three", "c"}:
        return CHOICE_DISCUSS

    # Phrase matching. Vault before new-proposal: "what did we propose for X"
    # mentions a proposal but is a lookup, not a new engagement.
    padded = f" {bare} "
    if any(h in padded for h in _VAULT_HINTS):
        return CHOICE_VAULT
    if any(h in padded for h in _NEW_PROPOSAL_HINTS):
        return CHOICE_NEW_PROPOSAL
    if any(h in padded for h in _DISCUSS_HINTS):
        return CHOICE_DISCUSS
    return None


def wants_restart(text: str) -> bool:
    """True when the user asks to abandon the current flow and start over.

    Only ever matches a SHORT message. A long answer that happens to contain a
    hint word (a "password reset" pain point, a "restart the service" runbook
    note) is an answer, not a command — see ``_RESTART_MAX_WORDS``.
    """
    norm = _normalise(text)
    words = norm.split()
    if not words or len(words) > _RESTART_MAX_WORDS:
        return False
    padded = f" {norm} "
    return any(h in padded for h in _RESTART_HINTS)


ROUTER_REPROMPT = """I didn't catch which of those you meant. Reply **1** to start a new proposal, **2** to search past proposals, or **3** for anything else."""


# --- Open WebUI internal task prompts ---------------------------------------
# OWUI issues extra completions against the SAME endpoint to auto-generate a
# chat title, tags and follow-up suggestions — observed live as two POSTs per
# user turn. Those payloads carry the conversation history, so without this
# guard the handler would read our own state marker, treat OWUI's instruction as
# the user's answer, and advance a discovery bucket spuriously.
#
# Matching is ANCHORED on OWUI's own template scaffolding rather than loose
# keywords: these strings come from OWUI's task templates, not from anything a
# consultant would plausibly type mid-interview.
_OWUI_TASK_MARKERS = (
    "### task:",
    "### guidelines:",
    "### output:",
    "json format:",
    "generate a concise",
    "create a concise, 3-5 word title",
    "generate 1-3 broad tags",
    "categorizing the main themes",
    "chat history:\n<chat_history>",
    "<chat_history>",
    "### chat history:",
    "suggest 3-5 relevant follow-up questions",
)


def is_owui_task_prompt(text: str) -> bool:
    """True when a message is Open WebUI's own title/tag/follow-up scaffolding.

    Such a turn must be answered harmlessly WITHOUT mutating conversation state.
    """
    low = (text or "").lower()
    return any(marker in low for marker in _OWUI_TASK_MARKERS)


# --- architecture review ----------------------------------------------------
INTENT_APPROVE = "approve"
INTENT_REJECT = "reject"
INTENT_REGENERATE = "regenerate"
INTENT_DRAFT = "draft"

_APPROVE_HINTS = (
    "approve", "approved", "looks good", "lgtm", "go ahead", "sign off",
    "signed off", "accept", "accepted", "yes proceed", "proceed", "ship it",
)
_REJECT_HINTS = ("reject", "not right", "wrong", "change", "revise", "fix", "no ")
_REGEN_HINTS = ("regenerate", "try again", "redo", "another version", "different diagram")
_DRAFT_HINTS = (
    "generate the proposal", "draft the proposal", "generate proposal",
    "draft proposal", "build the proposal", "write the proposal",
    "full proposal", "generate the document", "make the document",
)

# Same anchoring lesson as wants_restart: a long paragraph that happens to
# contain "change" is feedback, not a command. Commands are short.
_INTENT_MAX_WORDS = 12


def classify_architecture_intent(text: str) -> str | None:
    """Map a reply during architecture review to an intent. None if unclear.

    Approval is deliberately the strictest: it is the V1 human-in-loop gate, so
    an ambiguous reply must never be read as sign-off.
    """
    norm = _normalise(text)
    words = norm.split()
    if not words:
        return None
    padded = f" {norm} "

    # Drafting can be requested in a longer sentence — it is explicit either way.
    if any(h in padded for h in _DRAFT_HINTS):
        return INTENT_DRAFT
    if any(h in padded for h in _REGEN_HINTS):
        return INTENT_REGENERATE

    if len(words) <= _INTENT_MAX_WORDS and any(h in padded for h in _APPROVE_HINTS):
        return INTENT_APPROVE

    # Anything that reads as corrective feedback is a rejection, and long free
    # text during review is feedback by default rather than an approval.
    if any(h in padded for h in _REJECT_HINTS) or len(words) > _INTENT_MAX_WORDS:
        return INTENT_REJECT
    return None


# Maps the intake template's diagram-type vocabulary onto diagram_engine's
# DIAGRAM_TYPES. IV's sample proposals use the left-hand names; the renderer
# only understands the right-hand ones.
DIAGRAM_TYPE_MAP: dict[str, str] = {
    "solution/reference": "architecture",
    "solution": "architecture",
    "reference": "architecture",
    "target_reference": "architecture",
    "deployment": "architecture",
    "tenant": "architecture",
    "security": "network",
    "network": "network",
    "integration/joiner flow": "flow",
    "integration": "component",
    "joiner flow": "flow",
    "migration phases": "flow",
    "auth/customer journey": "sequence",
    "customer journey": "sequence",
    "user journey": "sequence",
    "authentication journey": "sequence",
}

# Cap how many diagrams one review round generates — each is an LLM call plus a
# render, and the reviewer only needs enough to judge the architecture.
MAX_DIAGRAMS_PER_ROUND = 3


def plan_diagrams(answers: dict) -> list[tuple[str, str]]:
    """Decide which diagrams to generate from the discovery answers.

    Returns a list of ``(title, engine_diagram_type)``. Falls back to a single
    solution-architecture diagram when the answers say nothing useful, so the
    approval gate always has something concrete to review.
    """
    raw = answers.get("required_diagram_types") or ""
    if isinstance(raw, (list, tuple)):
        requested = [str(x) for x in raw]
    else:
        requested = [p.strip() for p in re.split(r"[,;]", str(raw)) if p.strip()]

    planned: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in requested:
        key = item.strip().lower()
        engine_type = DIAGRAM_TYPE_MAP.get(key)
        if engine_type is None:
            # Try a loose contains match against the known vocabulary.
            engine_type = next(
                (v for k, v in DIAGRAM_TYPE_MAP.items() if k in key or key in k), None
            )
        if engine_type is None:
            continue
        title = item.strip().title().replace("/", " / ")
        pair = (title, engine_type)
        if pair not in seen:
            seen.add(pair)
            planned.append(pair)
        if len(planned) >= MAX_DIAGRAMS_PER_ROUND:
            break

    if not planned:
        planned = [("Solution Architecture", "architecture")]
    return planned


def build_architecture_message(rendered: list[dict], *, iteration: int = 1) -> str:
    """Present the proposed architecture for approval.

    ``rendered`` items: {"title", "diagram_type", "text_representation", "url"}.
    """
    lines = [
        "## Proposed Architecture — for your approval",
        "",
        "This is the **V1 approval gate**: I won't draft the proposal until you sign "
        "off on the architecture below.",
        "",
    ]
    if iteration > 1:
        lines += [f"_Revision {iteration}._", ""]

    for i, d in enumerate(rendered, 1):
        lines.append(f"### {i}. {d.get('title')} ({d.get('diagram_type')})")
        lines.append("")
        if d.get("url"):
            lines.append(f"![{d.get('title')}]({d['url']})")
            lines.append("")
            lines.append(f"[Open diagram]({d['url']}) — link expires in 1 hour.")
            lines.append("")
        else:
            lines.append("_(render unavailable — the text representation below is "
                         "the authoritative spec.)_")
            lines.append("")
        if d.get("text_representation"):
            lines.append("**Architecture Flow (Text Representation)**")
            lines.append("")
            lines.append(d["text_representation"])
            lines.append("")

    lines += [
        "---",
        "",
        "Reply **approve** to sign off and unlock drafting, **regenerate** for another "
        "attempt, or just tell me what to change.",
    ]
    return "\n".join(lines)


def build_spec_text_representation(spec_json: dict) -> str:
    """Render a DiagramSpec as IV's house 'Architecture Flow (Text Representation)'.

    IV's own proposals describe architecture as a node/edge narrative, so the
    reviewer sees it in the format they already use rather than raw JSON.
    """
    nodes = spec_json.get("nodes") or []
    edges = spec_json.get("edges") or []
    by_id = {n.get("id"): n for n in nodes if isinstance(n, dict)}

    lines: list[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        label = n.get("label") or n.get("id") or "component"
        group = n.get("group") or n.get("cluster")
        suffix = f" — _{group}_" if group else ""
        outgoing = [e for e in edges
                    if isinstance(e, dict) and e.get("source") == n.get("id")]
        lines.append(f"- **{label}**{suffix}")
        for e in outgoing:
            tgt = by_id.get(e.get("target"), {})
            tgt_label = tgt.get("label") or e.get("target") or "?"
            via = f" ({e['label']})" if e.get("label") else ""
            lines.append(f"  → {tgt_label}{via}")
    return "\n".join(lines) if lines else "_(empty spec)_"


ARCHITECTURE_APPROVED_MESSAGE = (
    "Architecture approved and recorded. The approval gate is now passed, so I can "
    "draft the full proposal grounded in IV's past work — the approved diagram(s) "
    "will be embedded in the document.\n\n"
    "Say **generate the proposal** when you're ready."
)

ARCHITECTURE_REJECTED_MESSAGE = (
    "Noted — I've logged that as a rejection with your comments, so the architecture "
    "is back in draft. Say **regenerate** and I'll produce a fresh version taking "
    "your feedback into account."
)

DRAFTING_PROMPT_MESSAGE = (
    "Architecture is approved. Say **generate the proposal** and I'll produce the "
    "full document — it takes a few minutes at full depth."
)


# --- interview --------------------------------------------------------------
def bucket_count(template: dict) -> int:
    return len(template.get("buckets") or [])


def get_bucket(template: dict, index: int) -> dict | None:
    buckets = template.get("buckets") or []
    if 0 <= index < len(buckets):
        return buckets[index]
    return None


def build_bucket_message(template: dict, index: int, *, first: bool = False) -> str:
    """Render the questions for one discovery bucket.

    Unlike the old ``build_interview_start_message``, this is parameterised by
    bucket index — which is what makes the interview able to advance at all.
    """
    bucket = get_bucket(template, index)
    if bucket is None:
        return ""
    total = bucket_count(template)
    lines: list[str] = []
    if first:
        lines += [
            "Good — let's scope the new proposal. I'll work through "
            f"{total} short discovery areas, then propose an architecture for your approval.",
            "",
        ]
    lines.append(f"**{bucket['title']}** — area {index + 1} of {total}")
    lines.append("")
    for q in bucket.get("questions", []):
        req = " *(required)*" if q.get("required") else ""
        opts = q.get("options")
        hint = f" — options: {', '.join(str(o) for o in opts)}" if opts else ""
        lines.append(f"- {q['label']}{req}{hint}")
    lines.append("")
    lines.append(
        "Answer in one message — plain prose is fine, I'll sort out which answer "
        "goes where. Say **skip** to leave an area for later."
    )
    return "\n".join(lines)


def build_recap_line(recorded: dict[str, str]) -> str:
    """One-line confirmation of what was captured, so the user can catch mistakes."""
    if not recorded:
        return "_Nothing captured from that — I'll come back to this area._"
    pairs = ", ".join(f"**{k}**: {v}" for k, v in list(recorded.items())[:6])
    return f"_Noted — {pairs}_"


def build_interview_complete_message(missing: list[str] | None) -> str:
    """Closing message once every bucket has been walked."""
    if missing:
        listed = ", ".join(f"`{m}`" for m in missing[:12])
        return (
            "That's the end of the discovery areas, but some **required** details are "
            f"still missing: {listed}.\n\n"
            "Give me those and I'll move on to the architecture proposal."
        )
    return (
        "Discovery is complete and saved. Next step is the **architecture proposal** — "
        "I'll put forward a design for your approval before drafting anything.\n\n"
        "Say **propose the architecture** when you're ready, or ask me to revisit any area first."
    )


SKIP_TOKENS = frozenset({"skip", "pass", "next", "n/a", "na", "none", "later", "dunno", "unknown"})


def is_skip(text: str) -> bool:
    """True for the short 'no answer here' tokens.

    Compares a whitespace-collapsed form so punctuated variants ("N/A", "n/a")
    match, while a real sentence that merely starts with one of these words
    ("none of our systems are cloud-based") does not.
    """
    compact = _normalise(text).replace(" ", "")
    return bool(compact) and compact in SKIP_TOKENS
