"""Pydantic v2 models for the Execution Market API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums — GENERATED from the backend single source of truth
# (``mcp_server/models.py``) by ``scripts/sync_enums.py`` (F3-6);
# ``tests/test_enums_sync.py`` fails when they drift. To resync::
#
#     python scripts/sync_enums.py && ruff format .
#
# ``DisputeReason`` below is hand-written (migration 004, not in models.py).
# ---------------------------------------------------------------------------


# --- BEGIN GENERATED: ENUMS (uvd-em-sdk/scripts/sync_enums.py) ---
class TaskStatus(str, Enum):
    """Task lifecycle states (synced from ``mcp_server/models.py``)."""

    PUBLISHED = "published"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    DISPUTED = "disputed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    # ADR-003 async assign: intermediate state while the escrow lock
    # runs. The API emits it (202 + poll) but the backend enum
    # predates async assign — clients must keep parsing it.
    ASSIGNING = "assigning"


class TaskCategory(str, Enum):
    """Task categories (synced from ``mcp_server/models.py``)."""

    PHYSICAL_PRESENCE = "physical_presence"
    KNOWLEDGE_ACCESS = "knowledge_access"
    HUMAN_AUTHORITY = "human_authority"
    SIMPLE_ACTION = "simple_action"
    DIGITAL_PHYSICAL = "digital_physical"
    LOCATION_BASED = "location_based"
    VERIFICATION = "verification"
    SOCIAL_PROOF = "social_proof"
    DATA_COLLECTION = "data_collection"
    SENSORY = "sensory"
    SOCIAL = "social"
    PROXY = "proxy"
    BUREAUCRATIC = "bureaucratic"
    EMERGENCY = "emergency"
    CREATIVE = "creative"
    DATA_PROCESSING = "data_processing"
    API_INTEGRATION = "api_integration"
    CONTENT_GENERATION = "content_generation"
    CODE_EXECUTION = "code_execution"
    RESEARCH = "research"
    MULTI_STEP_WORKFLOW = "multi_step_workflow"


class EvidenceType(str, Enum):
    """Evidence types (synced from ``mcp_server/models.py``)."""

    PHOTO = "photo"
    PHOTO_GEO = "photo_geo"
    VIDEO = "video"
    DOCUMENT = "document"
    RECEIPT = "receipt"
    SIGNATURE = "signature"
    NOTARIZED = "notarized"
    TIMESTAMP_PROOF = "timestamp_proof"
    TEXT_RESPONSE = "text_response"
    MEASUREMENT = "measurement"
    SCREENSHOT = "screenshot"
    JSON_RESPONSE = "json_response"
    API_RESPONSE = "api_response"
    CODE_OUTPUT = "code_output"
    FILE_ARTIFACT = "file_artifact"
    URL_REFERENCE = "url_reference"
    STRUCTURED_DATA = "structured_data"
    TEXT_REPORT = "text_report"


class TargetExecutorType(str, Enum):
    """Who can execute (synced from ``mcp_server/models.py``); ``any`` = wildcard."""

    HUMAN = "human"
    AGENT = "agent"
    ROBOT = "robot"
    ANY = "any"


# --- END GENERATED: ENUMS ---


class DisputeReason(str, Enum):
    """``dispute_reason`` enum values (migration 004) — the backend rejects
    anything else with 422 (``CreateDisputeRequest.reason`` pattern)."""

    INCOMPLETE_WORK = "incomplete_work"
    POOR_QUALITY = "poor_quality"
    WRONG_DELIVERABLE = "wrong_deliverable"
    LATE_DELIVERY = "late_delivery"
    FAKE_EVIDENCE = "fake_evidence"
    NO_RESPONSE = "no_response"
    PAYMENT_ISSUE = "payment_issue"
    UNFAIR_REJECTION = "unfair_rejection"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class Task(BaseModel):
    """A task in the Execution Market."""

    id: str
    title: str
    status: str
    category: str
    bounty_usd: float
    deadline: datetime
    created_at: datetime
    agent_id: str
    executor_id: Optional[str] = None
    instructions: Optional[str] = None
    evidence_schema: Optional[dict[str, Any]] = None
    location_hint: Optional[str] = None
    min_reputation: int = 0
    erc8004_agent_id: Optional[str] = None
    payment_network: str = "base"
    payment_token: str = "USDC"
    escrow_tx: Optional[str] = None
    refund_tx: Optional[str] = None
    target_executor_type: Optional[str] = None
    agent_name: Optional[str] = None
    skills_required: Optional[list[str]] = None
    payment_tx: Optional[str] = None
    escrow_status: Optional[str] = None
    #: Version of the skill.md the publisher used (semver).
    skill_version: Optional[str] = None
    #: Location matching strictness: strict | city | region | country | any.
    geo_match_mode: Optional[str] = None
    #: Geofence radius in METERS (geo_match_mode='strict').
    location_radius_m: Optional[int] = None


class TaskList(BaseModel):
    """Paginated list of tasks."""

    tasks: list[Task]
    total: int
    count: int
    offset: int
    has_more: bool


class Submission(BaseModel):
    """A worker's submission for a task."""

    id: str
    task_id: str
    executor_id: str
    status: str
    pre_check_score: Optional[float] = None
    submitted_at: datetime
    evidence: Optional[dict[str, Any]] = None
    agent_verdict: Optional[str] = None
    agent_notes: Optional[str] = None
    verified_at: Optional[datetime] = None


class SubmissionList(BaseModel):
    """List of submissions."""

    submissions: list[Submission]
    count: int


class Application(BaseModel):
    """A worker's application to a task."""

    id: str
    task_id: str
    executor_id: str
    message: Optional[str] = None
    status: str
    created_at: str


class ApplicationList(BaseModel):
    """List of applications."""

    applications: list[Application]
    count: int


class H2AApplication(BaseModel):
    """A worker's application to an H2A task.

    Mirrors ``GET /h2a/tasks/{id}/applications`` items — enriched with the
    executor profile (display_name, wallet, reputation) by the backend.
    """

    id: str
    task_id: str
    executor_id: str
    message: Optional[str] = None
    status: str
    created_at: str
    executor: Optional[dict[str, Any]] = None


class H2AApplicationList(BaseModel):
    """Applications for an H2A task (publisher-only view)."""

    task_id: str
    applications: list[H2AApplication]
    count: int


class Executor(BaseModel):
    """An executor (worker) profile."""

    id: str
    wallet_address: Optional[str] = None
    # Where SOLANA bounties land. Distinct from `wallet_address`, which is the
    # EVM identity (ERC-8128 auth verifies secp256k1 only, and the ERC-8004
    # reputation lookup keys off it). Bound via
    # PATCH /account/solana-payout-address with an ed25519 ownership proof.
    # Case-sensitive base58 — never lowercase it.
    solana_payout_address: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    reputation_score: Optional[float] = None
    tasks_completed: Optional[int] = None


class HealthResponse(BaseModel):
    """API health check response."""

    status: str
    version: Optional[str] = None


# ---------------------------------------------------------------------------
# Request param models (used by EMClient methods)
# ---------------------------------------------------------------------------


class CreateTaskParams(BaseModel):
    """Parameters for publishing a new task.

    Mirrors the backend ``CreateTaskRequest`` (``api/routers/_models.py``,
    ``extra="forbid"`` — a misspelled field name is a 422 server-side).
    """

    title: str = Field(..., min_length=5, max_length=255)
    instructions: str = Field(..., min_length=20, max_length=5000)
    category: TaskCategory
    bounty_usd: float = Field(..., gt=0, le=10000)
    deadline_hours: int = Field(..., ge=1, le=720)
    evidence_required: list[EvidenceType] = Field(..., min_length=1, max_length=5)
    evidence_optional: Optional[list[EvidenceType]] = None
    location_hint: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    #: Location matching strictness. ``strict`` requires lat/lng and uses
    #: ``location_radius_m`` as geofence; ``city``/``region``/``country``
    #: match administratively on ``location_hint``; ``any`` disables
    #: matching. Omit to let the server infer from the location fields.
    geo_match_mode: Optional[Literal["strict", "city", "region", "country", "any"]] = (
        None
    )
    #: Geofence radius in METERS for ``geo_match_mode='strict'`` (server
    #: default 500m when strict is inferred). Ignored for non-strict modes.
    location_radius_m: Optional[int] = Field(default=None, gt=0)
    min_reputation: int = 0
    payment_token: str = "USDC"
    payment_network: str = "base"
    agent_name: Optional[str] = None
    target_executor: Optional[TargetExecutorType] = None
    skills_required: Optional[list[str]] = None
    #: Version of the skill.md used to create this task (semver, e.g. "4.1.0").
    skill_version: Optional[str] = Field(default=None, max_length=20)
    #: Evidence verification mode: ``manual`` (agent reviews, server
    #: default), ``auto`` (Ring 2 ArbiterService releases/refunds without
    #: the agent), ``hybrid`` (arbiter recommends, agent confirms).
    arbiter_mode: Optional[Literal["manual", "auto", "hybrid"]] = None


class SubmitEvidenceParams(BaseModel):
    """Parameters for submitting evidence to a task."""

    executor_id: str
    evidence: dict[str, Any]
    notes: Optional[str] = None
    device_metadata: Optional[dict[str, Any]] = None


class ApproveParams(BaseModel):
    """Parameters for approving a submission."""

    notes: Optional[str] = None
    rating_score: Optional[int] = Field(default=None, ge=0, le=100)


class RejectParams(BaseModel):
    """Parameters for rejecting a submission."""

    notes: str = Field(..., min_length=10, max_length=1000)
    severity: str = "minor"
    reputation_score: Optional[int] = Field(default=None, ge=0, le=50)


# ---------------------------------------------------------------------------
# Payment models
# ---------------------------------------------------------------------------


class PaymentEvent(BaseModel):
    """A single payment event in a task's payment timeline."""

    id: str
    type: str
    actor: str
    timestamp: str
    network: str
    amount: Optional[float] = None
    tx_hash: Optional[str] = None
    note: Optional[str] = None


class PaymentTimeline(BaseModel):
    """Full payment status and event history for a task."""

    task_id: str
    status: str
    total_amount: float
    released_amount: float
    currency: str = "USDC"
    escrow_tx: Optional[str] = None
    escrow_contract: Optional[str] = None
    network: str = "base"
    events: list[PaymentEvent] = []
    created_at: str = ""
    updated_at: str = ""


class PlatformConfig(BaseModel):
    """Public platform configuration."""

    min_bounty_usd: float
    max_bounty_usd: float
    supported_networks: list[str]
    supported_tokens: list[str]
    preferred_network: str
    require_api_key: bool
    #: Minimum bounty (USD) that requires World ID Orb verification to apply.
    worldid_min_bounty_for_orb_usd: Optional[float] = None
    #: All valid task categories accepted by the platform.
    valid_categories: list[str] = []


class PaymentConfig(BaseModel):
    """Response of ``GET /h2a/payment-config``.

    Treasury + fee percent + the per-network escrow parameters the publisher
    needs to build the EIP-3009 escrow authorization at ASSIGNMENT time
    (all public on-chain constants). Feed the raw dict (``model_dump()``)
    or this model directly to :func:`~uvd_em_sdk.build_escrow_pre_auth`.
    """

    treasury: str
    fee_pct: float
    escrow: dict[str, Any] = {}

    @property
    def escrow_networks(self) -> list[str]:
        """Names of the escrow-capable networks served in ``escrow.networks``."""
        return sorted((self.escrow.get("networks") or {}).keys())


# ---------------------------------------------------------------------------
# Reputation / Identity models (ERC-8004)
# ---------------------------------------------------------------------------


class AgentReputation(BaseModel):
    """Reputation summary for an on-chain agent."""

    agent_id: int
    count: int = 0
    score: float = 0
    network: str = "base"


class AgentIdentity(BaseModel):
    """On-chain identity from ERC-8004 registry."""

    agent_id: int
    owner: str = ""
    agent_uri: str = ""
    agent_wallet: Optional[str] = None
    network: str = "base"
    name: Optional[str] = None
    description: Optional[str] = None
    image: Optional[str] = None
    services: list[dict[str, str]] = []


class ReputationNetworkPreference(BaseModel):
    """The chain where a user's ERC-8004 reputation is written (ratee-driven).

    Response of ``GET /workers/{wallet}/reputation-network``. Decoupled from
    the payment network (``preferred_network``): the *ratee* picks the chain
    their reputation lives on. While ``feature_enabled`` is ``False`` the
    preference is read-only — every PATCH returns 409 and a selector should
    render disabled / "coming soon"; the effective network stays ``"base"``.
    """

    wallet_address: str
    reputation_network: str = "base"
    selectable_networks: list[str] = []
    solana_enabled: bool = False
    feature_enabled: bool = False


class CrossChainReputation(BaseModel):
    """Multi-chain reputation as **describe.net** computed it (anti-laundering).

    Response of ``GET /reputation/wallet/{wallet_address}/cross-chain``. Since
    2026-08-29 the endpoint serves EM's snapshot of describe.net's aggregate
    rather than an aggregation EM performed itself: ``final_score`` is their
    ``global_score`` and ``per_chain`` maps each SCORED network to its
    ``{average, review_count, distinct_raters, agent_ids}`` breakdown. Surface
    the aggregate + the breakdown (the "primary chain" being the top-reviewed
    one), never a single-chain score — a ratee could otherwise launder
    negative rep by hopping chains.

    ``final_score`` is ``None`` when the index holds no eligible rating for
    the wallet, or when EM has never reconciled it. **It is never 0.** It used
    to default to ``0`` here, which meant a wallet nobody had rated was handed
    to callers as the worst possible score; ``retrieved_at`` is what separates
    "we asked and there is nothing" from "we never asked".

    ``retrieved_at`` is the AGE of the snapshot and belongs next to the number
    on any surface that shows it. ``policy_version`` is why two describe.net
    scores are not always comparable.
    """

    wallet_address: str
    final_score: Optional[float] = None
    source: Optional[str] = None
    policy_version: Optional[str] = None
    refreshed_at: Optional[str] = None
    retrieved_at: Optional[str] = None
    chain_count: int = 0
    total_reviews: int = 0
    chains_with_identity: int = 0
    chains_skipped: int = 0
    per_chain: dict[str, Any] = {}
    per_chain_retrieved_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Evidence models
# ---------------------------------------------------------------------------


class EvidenceUploadInfo(BaseModel):
    """Presigned URL info for evidence upload."""

    upload_url: str
    key: str
    public_url: Optional[str] = None
    content_type: str = "image/jpeg"
    expires_in: int = 900


class EvidenceVerifyResult(BaseModel):
    """Result of AI-powered evidence verification."""

    verified: bool
    confidence: float = 0
    decision: str = ""
    explanation: str = ""
    issues: list[str] = []


# ---------------------------------------------------------------------------
# Webhook models
# ---------------------------------------------------------------------------


class Webhook(BaseModel):
    """A registered webhook endpoint."""

    id: str
    url: str
    events: list[str]
    active: bool = True
    secret: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[str] = None


class WebhookList(BaseModel):
    """List of webhooks."""

    webhooks: list[Webhook]
    count: int = 0


# ---------------------------------------------------------------------------
# Service listing models (supply side)
# ---------------------------------------------------------------------------


class SellerReputation(BaseModel):
    """Read-only, public seller reputation surfaced on browse/detail."""

    reputation: float
    tasks_completed: int
    avg_rating: Optional[float] = None


class SellerCorrelation(BaseModel):
    """How diversified a seller's completed history is (advisory).

    The score alone cannot separate 100 tasks across a hundred buyers from
    100 tasks with a single buyer — the wash-trading shape. ``flagged`` is
    what makes that difference visible; it never blocks an order.
    """

    total_completed: int
    distinct_counterparties: int
    top_counterparty_share: float
    flagged: bool


class ServiceListing(BaseModel):
    """A supply-side service listing (a capability someone sells)."""

    id: str
    seller_executor_id: str
    seller_wallet: Optional[str] = None
    title: str
    description: str
    category: str
    unit_price_usd: float
    skills: list[str] = []
    evidence_schema: list[str] = []
    payment_network: str
    accepted_networks: list[str] = []
    availability: str
    orders_count: int
    created_at: str
    updated_at: Optional[str] = None
    seller_reputation: Optional[SellerReputation] = None
    #: COALESCE(on-chain ERC-8004 aggregate, DB heuristic) — the number
    #: ``min_reputation`` gates against and ``sort="reputation"`` ranks on.
    #: This is the score a hiring decision should cite.
    effective_reputation_score: Optional[float] = None
    #: The seller's ERC-8004 on-chain aggregate. ``None`` means no on-chain
    #: identity (or not reconciled yet) — which is NOT the same as a zero.
    onchain_reputation_score: Optional[float] = None
    seller_correlation: Optional[SellerCorrelation] = None


class ServiceListingList(BaseModel):
    """Paginated list of service listings."""

    listings: list[ServiceListing]
    count: int
    offset: int = 0


class ServiceOrder(BaseModel):
    """Result of ordering a service listing — the escrowed demand-side task
    the order materialized."""

    task_id: str
    listing_id: str
    seller_executor_id: str
    bounty_usd: float
    #: ``"locked"`` (synchronous escrow lock succeeded) or ``"assigning"``
    #: (async lock still in progress).
    escrow_status: str
    payment_network: str
    #: ``"accepted"`` or ``"assigning"``.
    task_status: str
