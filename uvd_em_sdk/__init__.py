"""uvd-em-sdk — THE canonical Python client for the Execution Market REST API.

Formerly ``em-plugin-sdk`` inside the Execution Market monorepo (0.8.0 was
its last release there); 0.9.0 is the same API under this name. Enums are
generated from the backend (``mcp_server/models.py`` of Execution Market) by
``scripts/sync_enums.py``; ``tests/test_enums_sync.py`` enforces parity where
that backend source is present (skipped otherwise).
"""

# First on purpose: importing _deps refuses an uvd-x402-sdk older than 0.93.0,
# naming the fix, before any module below imports from it.
from . import _deps  # noqa: F401
from .client import EMClient
from .models import (
    Task,
    TaskList,
    TaskStatus,
    TaskCategory,
    EvidenceType,
    TargetExecutorType,
    DisputeReason,
    Submission,
    SubmissionList,
    Application,
    ApplicationList,
    Executor,
    H2AApplication,
    H2AApplicationList,
    HealthResponse,
    PlatformConfig,
    PaymentConfig,
    PaymentEvent,
    PaymentTimeline,
    AgentReputation,
    AgentIdentity,
    ReputationNetworkPreference,
    CrossChainReputation,
    EvidenceUploadInfo,
    EvidenceVerifyResult,
    Webhook,
    WebhookList,
    ServiceListing,
    ServiceListingList,
    ServiceOrder,
    SellerReputation,
    SellerCorrelation,
    CreateTaskParams,
    SubmitEvidenceParams,
    ApproveParams,
    RejectParams,
)
from .exceptions import (
    EMError,
    EMAuthError,
    EMNotFoundError,
    EMValidationError,
    EMServerError,
)
from .fees import FeeBreakdown, calculate_fee, calculate_reverse_fee, get_fee_rate
from .networks import (
    NetworkInfo,
    TokenInfo,
    NETWORKS,
    get_network,
    get_enabled_networks,
    get_supported_tokens,
    is_valid_pair,
    get_chain_id,
    get_escrow_networks,
    has_escrow_support,
    DEFAULT_NETWORK,
    DEFAULT_TOKEN,
)
from .escrow_signing import build_escrow_pre_auth, compute_escrow_nonce
from .metered_channel import (
    COUNT_UNITS,
    UNIT_SECONDS,
    MeteredChannel,
    MeteredChannelError,
    MeteredChannelUnavailable,
    streaming_requested,
)

# Wallet adapters — uvd-x402-sdk[wallet] is a hard dependency since 0.9.0.
from uvd_x402_sdk.wallet import WalletAdapter, EnvKeyAdapter, OWSWalletAdapter

# SINGLE SOURCE OF TRUTH for the SDK version (SDK-51):
# - pyproject.toml derives it at build time ([tool.hatch.version])
# - client.py derives the User-Agent from it at runtime
# Bump it HERE only; test_version.py enforces consistency.
__version__ = "0.9.0"

__all__ = [
    # Client
    "EMClient",
    # Models — responses
    "Task",
    "TaskList",
    "TaskStatus",
    "TaskCategory",
    "EvidenceType",
    "TargetExecutorType",
    "DisputeReason",
    "Submission",
    "SubmissionList",
    "Application",
    "ApplicationList",
    "Executor",
    "H2AApplication",
    "H2AApplicationList",
    "HealthResponse",
    "PlatformConfig",
    "PaymentConfig",
    "PaymentEvent",
    "PaymentTimeline",
    "AgentReputation",
    "AgentIdentity",
    "ReputationNetworkPreference",
    "CrossChainReputation",
    "EvidenceUploadInfo",
    "EvidenceVerifyResult",
    "Webhook",
    "WebhookList",
    "ServiceListing",
    "ServiceListingList",
    "ServiceOrder",
    "SellerReputation",
    "SellerCorrelation",
    # Request params
    "CreateTaskParams",
    "SubmitEvidenceParams",
    "ApproveParams",
    "RejectParams",
    # Exceptions
    "EMError",
    "EMAuthError",
    "EMNotFoundError",
    "EMValidationError",
    "EMServerError",
    # Fees
    "FeeBreakdown",
    "calculate_fee",
    "calculate_reverse_fee",
    "get_fee_rate",
    # Networks
    "NetworkInfo",
    "TokenInfo",
    "NETWORKS",
    "get_network",
    "get_enabled_networks",
    "get_supported_tokens",
    "is_valid_pair",
    "get_chain_id",
    "get_escrow_networks",
    "has_escrow_support",
    "DEFAULT_NETWORK",
    "DEFAULT_TOKEN",
    # Escrow signing (ADR-002 sign-on-assignment)
    "build_escrow_pre_auth",
    "compute_escrow_nonce",
    # Metered payment over a channel (payment_streaming)
    "MeteredChannel",
    "MeteredChannelError",
    "MeteredChannelUnavailable",
    "streaming_requested",
    "UNIT_SECONDS",
    "COUNT_UNITS",
    # Wallet adapters (re-exported from uvd_x402_sdk.wallet)
    "WalletAdapter",
    "EnvKeyAdapter",
    "OWSWalletAdapter",
]
