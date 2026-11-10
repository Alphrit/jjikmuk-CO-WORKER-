import os
import logging
import secrets
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

AI_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = AI_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.api.request_guard import (
    ChatRequestGuardMiddleware,
    EXACT_REQUEST_BODY_SCOPE_KEY,
    chat_rate_limit_enabled,
    secure_text_matches,
    trust_proxy_headers,
)
from app.infrastructure.request_identity import (
    conversation_identity_boundary_status,
    verify_conversation_identity,
)
from app.services.activity import (
    get_activity_repository,
    log_chat_activity,
    raw_chat_message_logging_enabled,
    set_activity_repository,
)
from app.core.models import (
    ActivityRecord,
    ActivityProductLabelTrace,
    ChatHistoryItem,
    ChatRequest,
    ChatResponse,
    Product,
    ExternalAIProcessingSummary,
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationMessageResponse,
    ConversationRenameRequest,
    ConversationSummaryResponse,
    MAX_CHAT_HISTORY_CONTENT_LENGTH,
    MAX_CHAT_HISTORY_ITEMS,
    MAX_CHAT_HISTORY_TOTAL_LENGTH,
    MAX_CHAT_CONTEXT_TOTAL_LENGTH,
)
from app.core.public_api_schema import (
    ConversationDeleteAllResponse,
    ProductLabelMismatchReportRequest,
    ProductLabelMismatchReportResponse,
    PublicProductIdentifier,
)
from app.core.policy_manifest import assert_required_policy_ready, policy_manifest_status
from app.core.product_provenance import (
    content_revision_binding_status,
    effective_label_lineage_status,
    label_freshness_status,
    product_label_max_age_days,
    product_label_source_registry_ready,
    product_label_content_revision_sha256,
)
from app.core.public_product import (
    internal_product_reference,
    public_conversation_state,
)
from app.api.adapters import normalize_active_profiles, normalize_chat_request, normalize_known_profiles
from app.services.product import find_product_by_barcode
from app.services.product import get_product_repository, set_product_repository
from app.infrastructure.repository import (
    InMemoryActivityRepository,
    InMemoryProductRepository,
    MySQLActivityRepository,
    MySQLProductRepository,
    SQLiteActivityRepository,
    SQLiteProductRepository,
    dispose_mysql_connection_pools,
    mysql_tls_configuration_status,
)
from app.infrastructure.conversations import (
    ConversationCapacityError,
    ConversationConflictError,
    ConversationIntegrityError,
    ConversationMessage,
    ConversationNotFoundError,
    InMemoryConversationRepository,
    MySQLConversationRepository,
    SQLiteConversationRepository,
    MAX_MESSAGE_LIST_LIMIT,
    MAX_MESSAGE_METADATA_BYTES,
)
from app.infrastructure.llm import (
    external_ai_processing_enabled,
    get_llm_endpoint_policy_status,
    get_llm_provider,
    is_supported_llm_provider,
)
from app.infrastructure.external_ai_consent import (
    current_external_ai_processing_summary,
    external_ai_consent_boundary_status,
)
from app.infrastructure.rag import get_rag_embedding_endpoint_policy_status
from app.services.chat import generate_chat_response, build_allergy_clarification_response
from app.api.response_contract import PublicChatResponse, PublicConversationDetailResponse
from app.services.conversation_router import (
    pending_original_message_for_selection,
    resolve_product_context,
    resolve_clarification_product,
)
from app.services.emergency_triage import build_emergency_triage_response
from app.services.response_assessment import attach_response_assessment
from app.services.conversation_title import (
    DEFAULT_CONVERSATION_TITLE,
    generate_conversation_title,
    validate_conversation_title,
)
from app.services.conversations import (
    get_conversation_repository,
    set_conversation_repository,
)


logger = logging.getLogger(__name__)


class ConversationSnapshotTooLargeError(RuntimeError):
    """A public chatbot response cannot fit the bounded saved snapshot."""


_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_FALSE_ENV_VALUES = {"", "0", "false", "no", "off"}
_VALID_DEPLOYMENT_ENVIRONMENTS = {"development", "test", "production"}
_MIN_PRODUCTION_SECRET_BYTES = 32


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in _TRUE_ENV_VALUES:
        return True
    if normalized in _FALSE_ENV_VALUES:
        return False

    logger.warning("Invalid boolean environment variable %s; using false.", name)
    return False


def _db_error_fallback_enabled() -> bool:
    return _env_flag("FOOD_CHATBOT_FALLBACK_TO_MEMORY_ON_DB_ERROR", default=False)


def _conversations_enabled() -> bool:
    return _env_flag("FOOD_CHATBOT_ENABLE_CONVERSATIONS", default=False)


def _behind_trusted_gateway() -> bool:
    return _env_flag("FOOD_CHATBOT_BEHIND_TRUSTED_GATEWAY", default=False)


def _shared_replay_protection_asserted() -> bool:
    return _env_flag("FOOD_CHATBOT_SHARED_REPLAY_PROTECTION", default=False)


def _team_unverified_catalog() -> bool:
    from app.services.operational_policy import unverified_catalog_enabled
    return unverified_catalog_enabled()


def _has_minimum_utf8_bytes(value: str, *, minimum: int) -> bool:
    return len(value.strip().encode("utf-8")) >= minimum


def _is_http_header_safe_secret(value: str) -> bool:
    stripped = value.strip()
    return bool(stripped) and all("!" <= character <= "~" for character in stripped)


def _configured_db_provider() -> str:
    configured = (os.getenv("FOOD_CHATBOT_DB_PROVIDER") or "").strip().lower()
    if configured:
        return configured
    return "sqlite" if (os.getenv("FOOD_CHATBOT_DB_PATH") or "").strip() else "unconfigured"


def _deployment_boundary_status() -> dict:
    """Return non-secret production readiness errors."""

    raw_environment = os.getenv("FOOD_CHATBOT_ENV")
    environment = (
        "development" if raw_environment is None else raw_environment.strip().lower()
    )
    if environment not in _VALID_DEPLOYMENT_ENVIRONMENTS:
        return {
            "environment": "invalid",
            "valid": False,
            "error_codes": ["invalid_environment"],
        }

    errors: list[str] = []
    if environment == "production":
        provider = _configured_db_provider()
        if product_label_max_age_days() is None:
            errors.append("production_product_label_max_age_invalid")
        if not _team_unverified_catalog() and not product_label_source_registry_ready():
            errors.append("production_product_label_source_registry_invalid")
        from app.services.operational_policy import deployment_errors
        errors.extend(deployment_errors(external_enabled=external_ai_processing_enabled(),
                                        conversations_enabled=_conversations_enabled()))
        if provider in {"memory", "sqlite", "unconfigured"}:
            errors.append("production_persistent_mysql_required")
        elif provider == "mysql":
            tls = mysql_tls_configuration_status()
            if not tls["ca_configured"]:
                errors.append("production_mysql_tls_ca_missing")
            if not tls["client_certificate_pair_valid"]:
                errors.append("production_mysql_tls_client_certificate_pair_invalid")
        if _db_error_fallback_enabled():
            errors.append("production_memory_fallback_forbidden")
        if raw_chat_message_logging_enabled():
            errors.append("production_raw_message_storage_forbidden")
        if trust_proxy_headers() and not _behind_trusted_gateway():
            errors.append("production_proxy_header_trust_requires_gateway")

        api_token = (os.getenv("FOOD_CHATBOT_API_TOKEN") or "").strip()
        if not _has_minimum_utf8_bytes(api_token, minimum=_MIN_PRODUCTION_SECRET_BYTES):
            errors.append("production_api_token_too_short")
        if api_token and not _is_http_header_safe_secret(api_token):
            errors.append("production_api_token_not_header_safe")

        if _conversations_enabled():
            identity = conversation_identity_boundary_status()
            if not identity["configured"]:
                errors.append("production_conversation_identity_not_configured")
        if not (chat_rate_limit_enabled() or _behind_trusted_gateway()):
            errors.append("production_request_abuse_boundary_missing")
        if (
            _conversations_enabled() or external_ai_processing_enabled()
        ) and not _shared_replay_protection_asserted():
            errors.append("production_shared_replay_protection_missing")

        if external_ai_processing_enabled():
            consent = external_ai_consent_boundary_status()
            if not consent["configured"]:
                errors.append("production_external_ai_consent_not_configured")
            provider_name = get_llm_provider()
            if not is_supported_llm_provider(provider_name) or provider_name == "disabled":
                errors.append("production_external_ai_provider_invalid")
            else:
                generation = get_llm_endpoint_policy_status(provider_name)
                if not generation["endpoint_allowed"]:
                    errors.append("production_external_ai_generation_endpoint_invalid")
                embedding = get_rag_embedding_endpoint_policy_status()
                if not embedding["endpoint_allowed"]:
                    errors.append("production_external_ai_embedding_endpoint_invalid")

    return {
        "environment": environment,
        "valid": not errors,
        "error_codes": errors,
    }


def _assert_deployment_boundary() -> None:
    status = _deployment_boundary_status()
    if not status["valid"]:
        raise RuntimeError(
            "Unsafe FOOD_CHATBOT deployment configuration: "
            + ", ".join(status["error_codes"])
        )


def _bounded_env_int(
    name: str, *, default: int, minimum: int, maximum: int
) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        value = minimum - 1
    if not minimum <= value <= maximum:
        logger.warning("Invalid %s; using %d.", name, default)
        return default
    return value


def _max_conversations_per_user() -> int:
    return _bounded_env_int(
        "FOOD_CHATBOT_MAX_CONVERSATIONS_PER_USER",
        default=200,
        minimum=1,
        maximum=1000,
    )


def _conversation_auth_configured() -> bool:
    return bool(
        (os.getenv("FOOD_CHATBOT_API_TOKEN") or "").strip()
        and conversation_identity_boundary_status()["configured"]
    )


def _assert_chat_service_token(request: Request, *, feature: str) -> None:
    expected = (os.getenv("FOOD_CHATBOT_API_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=f"{feature} requires a configured chatbot API token.",
        )
    explicit = request.headers.get("x-food-chatbot-token", "").strip()
    authorization = request.headers.get("authorization", "")
    bearer = (
        authorization[7:].strip()
        if authorization.lower().startswith("bearer ")
        else ""
    )
    if not secure_text_matches(explicit or bearer, expected):
        raise HTTPException(
            status_code=401,
            detail="A valid chatbot API token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _signed_request_user_id(request: Request, *, purpose: str) -> int:
    if not conversation_identity_boundary_status()["configured"]:
        raise HTTPException(
            status_code=503,
            detail=(
                "A distinct visible-ASCII user identity signing secret of at "
                "least 32 bytes and a valid TTL are required."
            ),
        )
    request_state = request.scope.get("state")
    exact_body = (
        request_state.get(EXACT_REQUEST_BODY_SCOPE_KEY, b"")
        if isinstance(request_state, dict)
        else b""
    )
    verification = verify_conversation_identity(
        timestamp=request.headers.get(
            "x-food-chatbot-identity-timestamp", ""
        ).strip(),
        method=request.method.upper(),
        path=request.url.path,
        user_id=request.headers.get("x-food-chatbot-user-id", "").strip(),
        nonce=request.headers.get("x-food-chatbot-identity-nonce", "").strip(),
        signature=request.headers.get(
            "x-food-chatbot-identity-signature", ""
        ).strip(),
        body=exact_body if isinstance(exact_body, bytes) else b"",
        purpose=purpose,
    )
    if not verification.verified or verification.user_id is None:
        raise HTTPException(
            status_code=401,
            detail="A valid signed user identity is required.",
        )
    return verification.user_id


def _conversation_user_id(request: Request) -> int:
    if not _conversations_enabled():
        raise HTTPException(status_code=404, detail="Conversation storage is disabled.")
    _assert_chat_service_token(request, feature="Conversation storage")
    return _signed_request_user_id(request, purpose="conversation")
def _repository_readiness(repository) -> dict:
    check = getattr(repository, "readiness_check", None)
    if not callable(check):
        return {
            "available": False,
            "reason": "repository_readiness_check_missing",
        }
    try:
        status = check()
    except Exception as exc:
        logger.warning(
            "Repository readiness check failed for %s: %s",
            type(repository).__name__,
            type(exc).__name__,
        )
        return {
            "available": False,
            "reason": "repository_readiness_check_failed",
            "error_type": type(exc).__name__,
        }
    if not isinstance(status, dict):
        return {
            "available": False,
            "reason": "repository_readiness_check_invalid",
        }
    return status


def configure_repositories() -> None:
    _assert_deployment_boundary()
    # Validate all provenance relationships before accepting any request. A
    # malformed or expired required emergency policy must never fail first on
    # a user's urgent message.
    assert_required_policy_ready()
    if _conversations_enabled() and not _conversation_auth_configured():
        raise RuntimeError(
            "Saved conversations require FOOD_CHATBOT_API_TOKEN and a distinct "
            "FOOD_CHATBOT_USER_IDENTITY_SECRET of at least 32 UTF-8 bytes."
        )
    db_provider = _configured_db_provider()
    if db_provider == "memory":
        set_product_repository(InMemoryProductRepository())
        set_activity_repository(InMemoryActivityRepository())
        set_conversation_repository(
            InMemoryConversationRepository()
        )
        logger.info("Configured explicit in-memory repositories.")
        return

    if db_provider == "sqlite":
        db_path = (os.getenv("FOOD_CHATBOT_DB_PATH") or "").strip()
        if not db_path:
            raise RuntimeError(
                "FOOD_CHATBOT_DB_PROVIDER=sqlite requires FOOD_CHATBOT_DB_PATH."
            )
        set_product_repository(SQLiteProductRepository(db_path=db_path))
        set_activity_repository(SQLiteActivityRepository(db_path=db_path))
        if _conversations_enabled():
            set_conversation_repository(
                SQLiteConversationRepository(
                    db_path=db_path,
                    conversation_table_name=os.getenv(
                        "FOOD_CHATBOT_CONVERSATION_TABLE",
                        "chatbot_conversations",
                    ),
                    message_table_name=os.getenv(
                        "FOOD_CHATBOT_CONVERSATION_MESSAGE_TABLE",
                        "chatbot_messages",
                    ),
                )
            )
        else:
            set_conversation_repository(
                InMemoryConversationRepository()
            )
        logger.info("Configured SQLite repositories from %s", db_path)
        return

    if db_provider == "mysql":
        mysql_config = {
            "host": os.getenv("FOOD_CHATBOT_MYSQL_HOST", "localhost"),
            "port": int(os.getenv("FOOD_CHATBOT_MYSQL_PORT", "3306")),
            "database": os.getenv("FOOD_CHATBOT_MYSQL_DATABASE", "jjikmuk"),
            "user": os.getenv("FOOD_CHATBOT_MYSQL_USER", "root"),
            "password": os.getenv("FOOD_CHATBOT_MYSQL_PASSWORD", ""),
        }
        product_repository = MySQLProductRepository(
            **mysql_config,
            table_name=os.getenv("FOOD_CHATBOT_PRODUCT_TABLE", "products"),
        )
        activity_repository = MySQLActivityRepository(
            **mysql_config,
            table_name=os.getenv("FOOD_CHATBOT_HISTORY_TABLE", "histories"),
            chat_log_table_name=os.getenv("FOOD_CHATBOT_CHAT_LOG_TABLE", "chatbot_logs"),
            enable_legacy_history_writes=False,
        )
        conversation_repository = (
            MySQLConversationRepository(
                **mysql_config,
                conversation_table_name=os.getenv(
                    "FOOD_CHATBOT_CONVERSATION_TABLE",
                    "chatbot_conversations",
                ),
                message_table_name=os.getenv(
                    "FOOD_CHATBOT_CONVERSATION_MESSAGE_TABLE",
                    "chatbot_messages",
                ),
            )
            if _conversations_enabled()
            else InMemoryConversationRepository()
        )
        product_readiness = product_repository.readiness_check()
        if product_readiness.get("available") is not True:
            logger.warning(
                "MySQL product repository is not ready. reason=%s fallback_to_memory=%s",
                product_readiness.get("reason"),
                _db_error_fallback_enabled(),
            )
            connectivity_failure = (
                product_readiness.get("reason") == "mysql_product_check_failed"
                and product_readiness.get("error_type")
                in {"OSError", "TimeoutError", "OperationalError", "InterfaceError"}
            )
            if (
                _db_error_fallback_enabled()
                and connectivity_failure
                and not _conversations_enabled()
            ):
                set_product_repository(InMemoryProductRepository())
                set_activity_repository(InMemoryActivityRepository())
                set_conversation_repository(
                    InMemoryConversationRepository()
                )
                return
            raise RuntimeError(
                "MySQL product schema/catalog is not ready; verify the products table "
                "and apply AI/migrations before startup. "
                f"reason={product_readiness.get('reason') or 'unknown'}"
            )
        if (
            _deployment_boundary_status()["environment"] == "production"
            and product_readiness.get("label_lineage_schema_ready") is not True
        ):
            raise RuntimeError(
                "Production MySQL product-label lineage schema is not ready; "
                "apply AI/migrations/20260903_product_label_provenance.sql "
                "before startup. Legacy row values must remain NULL until "
                "backfilled from real source observations."
            )

        activity_repository.ensure_chatbot_log_table()
        if _conversations_enabled():
            conversation_readiness = conversation_repository.readiness_check()
            if conversation_readiness.get("available") is not True:
                raise RuntimeError(
                    "MySQL conversation schema is not ready; apply "
                    "AI/migrations/20260828_conversation_history.sql before startup. "
                    f"reason={conversation_readiness.get('reason') or 'unknown'}"
                )
        set_product_repository(product_repository)
        set_activity_repository(activity_repository)
        set_conversation_repository(conversation_repository)
        logger.info(
            "Configured MySQL repositories for database=%s product_table=%s history_table=%s",
            mysql_config["database"],
            os.getenv("FOOD_CHATBOT_PRODUCT_TABLE", "products"),
            os.getenv("FOOD_CHATBOT_HISTORY_TABLE", "histories"),
        )
        return

    raise RuntimeError(
        f"Unsupported FOOD_CHATBOT_DB_PROVIDER: {db_provider!r}. "
        "Set it explicitly to memory, sqlite, or mysql."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        configure_repositories()
        from app.start import rebuild_stale_rag_in_background
        rebuild_stale_rag_in_background()
        yield
    finally:
        dispose_mysql_connection_pools()


app = FastAPI(title="Food Chatbot MVP", lifespan=lifespan)
app.add_middleware(ChatRequestGuardMiddleware)


@app.exception_handler(RequestValidationError)
async def _validation_error_without_input(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI echoes the rejected body ('input'), including profiles and
    # allergies, into 422 responses that end up in backend logs.
    detail = [{key: value for key, value in error.items() if key not in {"input", "ctx", "url"}}
              for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(detail)})


@app.middleware("http")
async def private_chat_no_store_middleware(request: Request, call_next):
    """Prevent browsers and proxies from retaining user chat data."""

    response = await call_next(request)
    path = request.url.path
    sensitive_path = (
        path == "/chat"
        or path == "/conversations"
        or path.startswith("/conversations/")
        or (
            path.startswith("/products/")
            and path.endswith("/report")
        )
    )
    if sensitive_path:
        response.headers["Cache-Control"] = "no-store, private, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/")
def root():
    return {"message": "Food chatbot is running"}


@app.get("/health")
def health():
    return {"message": "Food chatbot is running"}


@app.get("/ready")
def readiness():
    deployment_boundary = _deployment_boundary_status()
    configured_provider = _configured_db_provider()
    product_repository = get_product_repository()
    activity_repository = get_activity_repository()
    conversation_repository = get_conversation_repository()
    product_repository_name = type(product_repository).__name__
    activity_repository_name = type(activity_repository).__name__
    conversation_repository_name = type(conversation_repository).__name__
    product_health = _repository_readiness(product_repository)
    activity_health = _repository_readiness(activity_repository)
    conversation_health = (
        _repository_readiness(conversation_repository)
        if _conversations_enabled()
        else {"available": True, "reason": "conversation_storage_disabled"}
    )

    expected_repositories = {
        "mysql": ("MySQLProductRepository", "MySQLActivityRepository"),
        "sqlite": ("SQLiteProductRepository", "SQLiteActivityRepository"),
        "memory": ("InMemoryProductRepository", "InMemoryActivityRepository"),
    }.get(configured_provider)
    provider_matches = (
        expected_repositories is not None
        and (product_repository_name, activity_repository_name) == expected_repositories
    )
    expected_conversation_repository = {
        "mysql": "MySQLConversationRepository",
        "sqlite": "SQLiteConversationRepository",
        "memory": "InMemoryConversationRepository",
    }.get(configured_provider)
    conversation_provider_matches = (
        not _conversations_enabled()
        or conversation_repository_name == expected_conversation_repository
    )
    conversation_auth_ready = (
        not _conversations_enabled() or _conversation_auth_configured()
    )
    product_available = product_health.get("available") is True
    product_label_lineage_schema_ready = (
        configured_provider != "mysql"
        or product_health.get("label_lineage_schema_ready") is True
    )
    product_label_freshness_policy_ready = (
        product_label_max_age_days() is not None
    )
    product_label_source_registry_configured = (
        product_label_source_registry_ready()
    )
    activity_available = activity_health.get("available") is True
    conversation_available = conversation_health.get("available") is True
    try:
        policy_status = policy_manifest_status()
    except Exception:
        policy_status = {
            "policy_manifest_valid": False,
            "policy_version": None,
            "current_rule_count": 0,
            "expired_rule_count": 0,
            "all_registered_rules_current": False,
            "required_emergency_rules_current": False,
        }
    ready = (
        deployment_boundary["valid"]
        and provider_matches
        and conversation_provider_matches
        and conversation_auth_ready
        and product_available
        and (
            deployment_boundary["environment"] != "production"
            or (
                product_label_lineage_schema_ready
                and product_label_freshness_policy_ready
                and (product_label_source_registry_configured or _team_unverified_catalog())
            )
        )
        and activity_available
        and conversation_available
        and policy_status["policy_manifest_valid"]
        and policy_status["all_registered_rules_current"]
        and policy_status["required_emergency_rules_current"]
    )

    payload = {
        "ready": ready,
        "deployment_environment": deployment_boundary["environment"],
        "deployment_boundary_valid": deployment_boundary["valid"],
        "deployment_boundary_error_codes": deployment_boundary["error_codes"],
        "configured_provider": configured_provider,
        "product_repository": product_repository_name,
        "activity_repository": activity_repository_name,
        "conversation_repository": conversation_repository_name,
        "provider_matches_configuration": provider_matches,
        "conversation_provider_matches_configuration": conversation_provider_matches,
        "conversation_auth_ready": conversation_auth_ready,
        "product_repository_available": product_available,
        "product_label_lineage_schema_ready": product_label_lineage_schema_ready,
        "product_label_freshness_policy_ready": (
            product_label_freshness_policy_ready
        ),
        "product_label_source_registry_configured": (
            product_label_source_registry_configured
        ),
        "team_unverified_catalog": _team_unverified_catalog(),
        "activity_repository_available": activity_available,
        "conversation_repository_available": conversation_available,
        "conversation_storage_enabled": _conversations_enabled(),
        "audit_log_ready": activity_health.get("audit_log_ready"),
        **policy_status,
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)


def _run_chat_pipeline(
    req: ChatRequest,
    *,
    log_activity: bool = True,
    allow_raw_audit_message: bool = True,
    trusted_user_id: int | None = None,
):
    if req.analysis_context is None:
        return _run_legacy_chat_pipeline(req, log_activity=log_activity,
            allow_raw_audit_message=allow_raw_audit_message, trusted_user_id=trusted_user_id)
    from app.services.backend_analysis import run_backend_pipeline
    from app.core.backend_analysis import backend_analysis_active
    token = backend_analysis_active.set(True)
    try:
        response, product, activity_context = run_backend_pipeline(req, _run_legacy_chat_pipeline)
    finally:
        backend_analysis_active.reset(token)
    if log_activity:
        _log_pipeline_response(response, activity_context=activity_context,
            allow_raw_audit_message=allow_raw_audit_message, trusted_user_id=trusted_user_id)
    return response, product, activity_context


def _run_legacy_chat_pipeline(
    req: ChatRequest,
    *,
    log_activity: bool = True,
    allow_raw_audit_message: bool = True,
    trusted_user_id: int | None = None,
):
    from app.intent.request_semantics import current_topic_message
    req = req.model_copy(update={"message": current_topic_message(req.message)})
    # Time-sensitive symptom guidance must not wait for a product repository or
    # an LLM. Profile normalization is local and is used only to recognize a
    # known allergy context; no product facts are trusted here.
    from app.services.comparison_language import split_comparison_cancellation, clear_comparison_context
    remaining_message, comparison_cancelled = split_comparison_cancellation(req.message)
    if comparison_cancelled:
        req = req.model_copy(update={
            "conversation_state": clear_comparison_context(req.conversation_state),
            # Keep a standalone cancellation recognizable in the service.
            "message": remaining_message or req.message,
        })
    active_profiles = normalize_active_profiles(req)
    from app.services.conversation_controls import conversation_control_response, history_after_reset
    req = req.model_copy(update={"chat_history": history_after_reset(req.chat_history)})
    emergency_response = build_emergency_triage_response(
        req.message,
        profiles=active_profiles,
        has_food_context=bool(req.product or req.barcode or req.report_number
                              or req.conversation_state.get("last_confirmed_product")),
    )
    from app.services.request_language import standalone_cancellation_response, request_lifecycle, request_state_after_action
    current_condition_message = req.message
    lifecycle = request_lifecycle(req.message)
    if emergency_response is None and lifecycle.remaining_message:
        req = req.model_copy(update={
            "message": lifecycle.remaining_message,
            "conversation_state": request_state_after_action(lifecycle, req.conversation_state),
            "chat_history": [] if lifecycle.cancel or lifecycle.cancel_search else req.chat_history,
        })
    cancellation_response = (
        (conversation_control_response(req.message, req.conversation_state) or standalone_cancellation_response(req.message, req.conversation_state,
                                         profiles=active_profiles, known_profiles=normalize_known_profiles(req))
        )
        if emergency_response is None else None
    )
    from app.services.condition_subject import unresolved_profile_target_response
    target_response = (
        unresolved_profile_target_response(req.message, active_profiles, normalize_known_profiles(req), req.conversation_state)
        if emergency_response is None and cancellation_response is None else None
    )
    if target_response is not None:
        # Preserve only lookup identifiers while the person is unresolved. No
        # catalog fact or suitability decision is accepted from the request.
        reference = {key: value for key, value in {
            "barcode": req.barcode or (req.product.barcode if req.product else None),
            "report_number": req.report_number or (req.product.report_number if req.product else None),
        }.items() if value}
        if reference:
            state = {**req.conversation_state, "pending_target_product": reference}
            target_response = target_response.model_copy(update={
                "conversation_state": public_conversation_state(state), "internal_conversation_state": state,
            })
    if emergency_response is not None or cancellation_response is not None or target_response is not None:
        response = emergency_response.model_copy(
            # A safety interruption must not discard the previously confirmed
            # product or other bounded conversation state.
            update={
                "conversation_state": public_conversation_state(
                    req.conversation_state
                ),
                "internal_conversation_state": req.conversation_state,
            },
        ) if emergency_response is not None else (cancellation_response or target_response)
        response = attach_response_assessment(
            response,
            profiles=active_profiles,
            product=None,
        )
        profile = active_profiles[0] if active_profiles else None
        request_product = req.product
        activity_context = {
            "profile": profile,
            "product": None,
            "barcode": req.barcode or (request_product.barcode if request_product else None),
            "report_number": req.report_number or (
                request_product.report_number if request_product else None
            ),
            "message": req.message,
        }
        if log_activity:
            _log_pipeline_response(
                response,
                activity_context=activity_context,
                allow_raw_audit_message=allow_raw_audit_message,
                trusted_user_id=trusted_user_id,
            )
        return response, None, activity_context

    # Do not search for candidates or recommend while a new allergy is unclear.
    # A known catalog identifier can still reveal a registered conflict: keep
    # that warning alongside the clarification without assigning the new allergy.
    from app.services.food_conditions import prepare_food_conditions
    from app.services.recipe_request_context import recipe_source_reply
    resumed_source_request = recipe_source_reply(
        req.message, req.conversation_state,
        has_identifier=bool(req.barcode or req.report_number or req.product),
    )
    prepared_conditions = prepare_food_conditions(
        req.message, active_profiles, req.conversation_state, normalize_known_profiles(req),
        resumed_product_message=resumed_source_request,
        current_condition_message=current_condition_message,
    )
    req = req.model_copy(update={
        "message": req.message if prepared_conditions.selecting_product else prepared_conditions.message,
        "conversation_state": prepared_conditions.state,
    })
    message_constraint_response = prepared_conditions.allergy_guard
    if message_constraint_response is not None:
        profile, request_product, barcode, report_number = normalize_chat_request(req)
        product = resolve_clarification_product(
            message=req.message, product=request_product, barcode=barcode,
            report_number=report_number, conversation_state=req.conversation_state,
        )
        response = build_allergy_clarification_response(
            message_constraint_response, message=req.message, product=product,
            profiles=active_profiles, known_profiles=normalize_known_profiles(req),
            state=req.conversation_state,
            prepared_conditions=prepared_conditions,
        )
        response = response.model_copy(update={
            "conversation_state": public_conversation_state(response.internal_conversation_state),
        })
        activity_context = {
            "profile": profile,
            "product": product,
            "barcode": barcode,
            "report_number": report_number,
            "message": req.message,
        }
        if log_activity:
            _log_pipeline_response(
                response,
                activity_context=activity_context,
                allow_raw_audit_message=allow_raw_audit_message,
                trusted_user_id=trusted_user_id,
            )
        return response, product, activity_context

    from app.services.recipe_followup import recipe_followup_response
    recipe_followup = (recipe_followup_response(req.message, req.conversation_state, prepared_conditions.profiles)
                       if not (req.product or req.barcode or req.report_number or prepared_conditions.ambiguous_diet) else None)
    if recipe_followup is not None:
        response = attach_response_assessment(recipe_followup, profiles=active_profiles, product=None)
        activity_context = {"profile": active_profiles[0] if active_profiles else None,
                            "product": None, "barcode": None, "report_number": None, "message": req.message}
        if log_activity:
            _log_pipeline_response(response, activity_context=activity_context,
                                   allow_raw_audit_message=allow_raw_audit_message, trusted_user_id=trusted_user_id)
        return response, None, activity_context

    profile, product, barcode, report_number = normalize_chat_request(req)
    resumed_original_message = pending_original_message_for_selection(
        req.message,
        req.conversation_state,
    )
    product, barcode, report_number, confirmation_response = resolve_product_context(
        message=req.message,
        profile=profile,
        product=product,
        barcode=barcode,
        report_number=report_number,
        chat_history=req.chat_history,
        conversation_state=req.conversation_state,
        explicit_barcode_reference=req.barcode,
        explicit_report_number_reference=req.report_number,
    )
    if confirmation_response is None and product is not None and product.product_found is False:
        from app.intent.rules import is_recipe_recommend_question
        if is_recipe_recommend_question(req.message):
            # A failed explicit identifier is still an unfinished source lookup,
            # not a completed recipe answer that can consume temporary allergy.
            from app.services.recipe_request_context import missing_recipe_source_response
            confirmation_response = missing_recipe_source_response(req.message, req.conversation_state)
            product = None
    response = confirmation_response or generate_chat_response(
        profile=profile,
        product=product,
        message=resumed_original_message or req.message,
        active_profiles=active_profiles,
        chat_history=req.chat_history,
        conversation_state=req.conversation_state,
        known_profiles=normalize_known_profiles(req),
        prepared_conditions=(prepared_conditions if not resumed_original_message or prepared_conditions.selecting_product else None),
    )
    if confirmation_response is not None and prepared_conditions.pending:
        # Product selection is still part of this request; do not lose a
        # resolved temporary allergy before the actual assessment runs.
        from app.core.conversation_context_fields import json_state
        pending_state = {
            **prepared_conditions.state,
            **json_state(response.internal_conversation_state or response.conversation_state),
            "pending_allergy": prepared_conditions.pending,
        }
        if response.intent == "missing_product":
            for key in ("last_confirmed_product", "recipe_product", "pending_product_candidates"):
                pending_state.pop(key, None)
        response = response.model_copy(update={
            "internal_conversation_state": pending_state,
            "conversation_state": public_conversation_state(pending_state),
        })
    # Confirmation and product-selection responses do not pass through the
    # chat service, so attach the same UI-safe trust contract at the API edge.
    # Re-attaching is intentional and idempotent for normal generated replies.
    # Trust metadata must be bound to the exact product snapshot exposed in the
    # response. Repository-miss placeholders carry only lookup identifiers and
    # intentionally are not returned as ``current_product``.
    assessment_product = (
        product
        if response.current_product is not None
        else None
    )
    response = attach_response_assessment(
        response,
        profiles=active_profiles,
        product=assessment_product,
    )
    if assessment_product is not None:
        response = response.model_copy(update={
            "product_reference": internal_product_reference(assessment_product),
        })
    from app.core.conversation_context_fields import carry_task_context
    public_state = carry_task_context(req.conversation_state, response.conversation_state)
    internal_state = carry_task_context(
        req.conversation_state, response.internal_conversation_state or public_state,
    )
    response = response.model_copy(update={
        "internal_conversation_state": internal_state,
        "conversation_state": public_conversation_state(public_state),
    })
    activity_context = {
        "profile": profile,
        "product": product,
        "barcode": barcode,
        "report_number": report_number,
        "message": req.message,
    }
    if log_activity:
        _log_pipeline_response(
            response,
            activity_context=activity_context,
            allow_raw_audit_message=allow_raw_audit_message,
            trusted_user_id=trusted_user_id,
        )
    return response, product, activity_context


def _log_pipeline_response(
    response: ChatResponse,
    *,
    activity_context: dict,
    allow_raw_audit_message: bool,
    trusted_user_id: int | None = None,
) -> None:
    # Read the request-local disclosure marker at log time. Providers mark an
    # attempt before network I/O, so a timeout followed by a rules fallback is
    # still auditable without retaining the prompt, profile, URL, or signature.
    external_ai_summary = current_external_ai_processing_summary()
    response_product_label_trace = _response_product_label_trace(
        response,
        activity_context.get("product"),
    )
    log_chat_activity(
        profile=activity_context["profile"],
        product=activity_context["product"],
        barcode=activity_context["barcode"],
        report_number=activity_context["report_number"],
        message=activity_context["message"],
        intent=response.intent,
        answer_source=response.answer_source,
        pipeline_stage=response.pipeline_stage,
        risk_level=response.risk_level,
        confidence=response.confidence,
        confidence_reasons=response.confidence_reasons,
        fallback_reason=response.fallback_reason,
        validation_failure_types=response.validation_failure_types,
        critical_safety_violation=response.critical_safety_violation,
        blocked_llm_output_stored=response.blocked_llm_output_stored,
        response_id=response.response_id,
        external_ai_used=bool(external_ai_summary.get("used")),
        external_ai_services=list(external_ai_summary.get("services") or []),
        policy_version=response.policy_version,
        applied_rule_ids=response.applied_rule_ids,
        source_ids=list(dict.fromkeys(
            item.source_id for item in response.evidence if item.source_id
        )),
        response_product_label_trace=response_product_label_trace,
        trusted_user_id=trusted_user_id,
        # Saved conversations have their own retention/deletion contract. Never
        # duplicate their raw text into the operational audit log.
        allow_raw_message_storage=allow_raw_audit_message,
    )


def _response_product_label_trace(
    response: ChatResponse,
    product: object,
) -> ActivityProductLabelTrace | None:
    """Return an audit trace only for the verified snapshot used in a response."""

    if (
        not isinstance(product, Product)
        or product.product_found is not True
        or product.facts_verified is not True
        or response.current_product is None
        or response.product_data_trust is None
        or response.product_data_trust.repository_identity_matched is not True
        or response.product_snapshot_provenance is None
        or response.product_snapshot_nutrition_basis is None
    ):
        return None

    trust = response.product_data_trust
    public_product = response.current_product
    public_label_trust = public_product.label_trust
    snapshot_provenance = response.product_snapshot_provenance
    snapshot_nutrition_basis = response.product_snapshot_nutrition_basis
    expected_reference = internal_product_reference(product)
    expected_hash = product_label_content_revision_sha256(product)
    expected_lineage = effective_label_lineage_status(product)
    expected_binding = content_revision_binding_status(product)

    # The public badge, private response snapshot, and request-local repository
    # product must all describe the same normalized label.  Any mismatch omits
    # the trace rather than writing a misleading post-incident record.
    if (
        expected_reference is None
        or response.product_reference != expected_reference
        or snapshot_nutrition_basis != product.nutrition_basis
        or public_product.nutrition_basis.basis_type
        != snapshot_nutrition_basis.basis_type
        or snapshot_provenance.source_type != product.label_provenance.source_type
        or snapshot_provenance.source_id != product.label_provenance.source_id
        or snapshot_provenance.source_record_id
        != product.label_provenance.source_record_id
        or snapshot_provenance.label_observed_at
        != product.label_provenance.label_observed_at
        or snapshot_provenance.upstream_updated_at
        != product.label_provenance.upstream_updated_at
        or snapshot_provenance.upstream_revision
        != product.label_provenance.upstream_revision
        or snapshot_provenance.ingested_content_revision_sha256
        != product.label_provenance.ingested_content_revision_sha256
        or snapshot_provenance.covered_fact_groups
        != product.label_provenance.covered_fact_groups
        or snapshot_provenance.lineage_status != expected_lineage
        or snapshot_provenance.content_revision_sha256 != expected_hash
        or snapshot_provenance.content_revision_binding_status != expected_binding
        or trust.label_lineage_status != expected_lineage
        or trust.label_observed_at != snapshot_provenance.label_observed_at
        or trust.content_revision_sha256 != expected_hash
        or trust.content_revision_binding_status != expected_binding
        or public_label_trust.lineage_status != trust.label_lineage_status
        or public_label_trust.freshness_status != trust.label_freshness_status
        or public_label_trust.label_observed_at != trust.label_observed_at
        or public_label_trust.content_revision_sha256
        != trust.content_revision_sha256
        or public_label_trust.content_revision_binding_status
        != trust.content_revision_binding_status
    ):
        return None

    return ActivityProductLabelTrace(
        repository_identity_matched=True,
        label_lineage_status=trust.label_lineage_status,
        label_freshness_status=trust.label_freshness_status,
        label_observed_at=trust.label_observed_at,
        content_revision_sha256=trust.content_revision_sha256,
        content_revision_binding_status=trust.content_revision_binding_status,
        nutrition_basis_type=public_product.nutrition_basis.basis_type,
    )


def _team_catalog_response(response: ChatResponse, *, resolved_product=None) -> ChatResponse:
    from app.services.team_catalog import restrict_team_catalog_response
    return restrict_team_catalog_response(response, resolved_product=resolved_product,
                                          external_enabled=external_ai_processing_enabled())


def _stateless_chat_response(
    req: ChatRequest,
    *,
    trusted_user_id: int | None = None,
) -> ChatResponse:
    if req.conversation_id is not None:
        raise ValueError(
            "Persisted conversations must be handled through the authenticated HTTP API."
        )
    response, _resolved_product, _activity_context = _run_chat_pipeline(
        req,
        log_activity=False,
        trusted_user_id=trusted_user_id,
    )
    from app.services.answer_polish import polish_product_answer
    response = polish_product_answer(response)
    if _team_unverified_catalog():
        response = _team_catalog_response(response, resolved_product=_resolved_product)
    from app.services.followup_questions import tidy_followups
    response = tidy_followups(response, req.message, team_mode=_team_unverified_catalog())
    _log_pipeline_response(response, activity_context=_activity_context,
                           allow_raw_audit_message=True,
                           trusted_user_id=trusted_user_id)
    return response.model_copy(
        update={
            "external_ai_processing": ExternalAIProcessingSummary.model_validate(
                current_external_ai_processing_summary()
            ),
        }
    )


def chat(req: ChatRequest) -> ChatResponse:
    """Backward-compatible stateless entry point used by tests and evaluation.

    Direct callers never receive authority to attribute an activity row to an
    account. Only the HTTP boundary can pass a body-bound verified user ID to
    ``_stateless_chat_response``.
    """

    if req.analysis_context is not None:
        raise ValueError("Backend analysis requires the authenticated HTTP API.")
    return _stateless_chat_response(req)


def _conversation_timestamp(value) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _conversation_summary(conversation) -> ConversationSummaryResponse:
    return ConversationSummaryResponse(
        conversation_id=conversation.conversation_id,
        title=conversation.title,
        title_manually_set=conversation.title_manually_set,
        revision=conversation.revision,
        created_at=_conversation_timestamp(conversation.created_at),
        updated_at=_conversation_timestamp(conversation.updated_at),
        expires_at=(
            _conversation_timestamp(conversation.expires_at)
            if conversation.expires_at is not None
            else None
        ),
    )


def _payload_fingerprint(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _request_fingerprint(req: ChatRequest) -> str:
    payload = req.model_dump(
        mode="json",
        exclude={"chat_history", "conversation_state"},
    )
    payload.pop("turn_id", None)
    return _payload_fingerprint(payload)


def _assistant_message_metadata(
    response: ChatResponse,
    *,
    request_fingerprint: str,
    write_attempt_id: str,
) -> dict:
    """Store one bounded response snapshot beside the assistant transcript."""

    response_payload = response.model_dump(
        mode="json",
        exclude={
            "response_id",
            "answer",
            "conversation_state",
            "internal_conversation_state",
        },
    )
    metadata = {
        "contract_version": 3,
        "request_fingerprint": request_fingerprint,
        "write_attempt_id": write_attempt_id,
        "response": response_payload,
    }
    encoded_size = len(
        json.dumps(
            metadata,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    if encoded_size > MAX_MESSAGE_METADATA_BYTES:
        raise ConversationSnapshotTooLargeError(
            "The structured chatbot response is too large to save."
        )
    return metadata


def _response_from_assistant_message(
    message: ConversationMessage,
) -> ChatResponse:
    payload = message.metadata.get("response")
    if not isinstance(payload, dict):
        raise ConversationIntegrityError("Saved response snapshot is missing.")
    return ChatResponse.model_validate({
        **payload,
        "answer": message.content,
        "response_id": message.message_id,
        "conversation_id": message.conversation_id,
        "turn_id": message.turn_id,
        "conversation_state": {},
        "internal_conversation_state": {},
    })


def _restore_exchange(
    exchange,
    *,
    user_id: int,
    expected_user_content: str,
    expected_fingerprint: str,
) -> ChatResponse:
    """Restore one owned, paired exchange without a second response-signing layer."""

    user_message, assistant_message = exchange
    if (
        user_message.role != "user"
        or assistant_message.role != "assistant"
        or user_message.conversation_id != assistant_message.conversation_id
        or user_message.turn_id is None
        or user_message.turn_id != assistant_message.turn_id
        or user_message.sequence + 1 != assistant_message.sequence
    ):
        raise ConversationIntegrityError("Saved response exchange is not paired.")
    if user_message.content != expected_user_content:
        raise ConversationConflictError(
            "The turn ID is already used by a different request."
        )
    if assistant_message.metadata.get("request_fingerprint") != expected_fingerprint:
        raise ConversationConflictError(
            "The turn ID is already used by a different request."
        )
    return _response_from_assistant_message(assistant_message)


def _conversation_message_response(
    message: ConversationMessage,
) -> ConversationMessageResponse:
    restored_response = None
    if message.role == "assistant":
        try:
            restored_response = _response_from_assistant_message(message)
        except (ConversationIntegrityError, ValueError):
            # Keep the transcript readable if an old or malformed structured
            # snapshot cannot be rendered. Ownership is still enforced by the
            # repository query.
            logger.warning(
                "Saved assistant response opened as transcript-only for message=%s",
                message.message_id,
            )
    return ConversationMessageResponse(
        message_id=message.message_id,
        sequence=message.sequence,
        role=message.role,
        content=message.content,
        turn_id=message.turn_id,
        created_at=_conversation_timestamp(message.created_at),
        response=restored_response,
    )


def _history_from_saved_messages(
    messages,
    *,
    current_message: str,
) -> list[ChatHistoryItem]:
    """Build bounded model context from complete normal chat pairs."""

    complete_pairs = []
    index = 0
    while index + 1 < len(messages):
        user_message = messages[index]
        assistant_message = messages[index + 1]
        if (
            user_message.role == "user"
            and assistant_message.role == "assistant"
            and user_message.turn_id is not None
            and user_message.turn_id == assistant_message.turn_id
        ):
            response_payload = assistant_message.metadata.get("response", {})
            if isinstance(response_payload, dict):
                complete_pairs.append((user_message, assistant_message))
            index += 2
            continue
        index += 1

    remaining = min(
        MAX_CHAT_HISTORY_TOTAL_LENGTH,
        MAX_CHAT_CONTEXT_TOTAL_LENGTH - len(current_message),
    )
    selected = []
    for user_message, assistant_message in reversed(complete_pairs):
        user_content = user_message.content[:MAX_CHAT_HISTORY_CONTENT_LENGTH].strip()
        assistant_content = assistant_message.content[
            :MAX_CHAT_HISTORY_CONTENT_LENGTH
        ].strip()
        if not user_content or not assistant_content:
            continue
        pair_length = len(user_content) + len(assistant_content)
        if pair_length > remaining:
            break
        selected.append((user_content, assistant_content))
        remaining -= pair_length

    history = []
    for user_content, assistant_content in reversed(selected):
        history.extend([
            ChatHistoryItem(role="user", content=user_content),
            ChatHistoryItem(role="assistant", content=assistant_content),
        ])
    return history[-MAX_CHAT_HISTORY_ITEMS:]


def _trusted_saved_conversation_state(conversation) -> dict:
    # The repository enforces owner access and bounded JSON state. A separate
    # response-MAC chain is intentionally not part of this chatbot.
    return dict(conversation.state)


def _processing_request(
    req: ChatRequest,
    *,
    conversation_state: dict,
    messages,
) -> ChatRequest:
    payload = req.model_dump(
        mode="json",
        exclude={
            "conversation_id",
            "turn_id",
            "chat_history",
            "conversation_state",
        },
    )
    payload["conversation_state"] = conversation_state
    payload["chat_history"] = [
        item.model_dump()
        for item in _history_from_saved_messages(
            messages,
            current_message=req.message,
        )
    ]
    if req.analysis_context is not None:
        from app.core.backend_analysis import MAX_V2_HISTORY_ITEMS
        payload["chat_history"] = payload["chat_history"][-MAX_V2_HISTORY_ITEMS:]
    return ChatRequest.model_validate(payload)



def _raise_conversation_http_error(
    exc: Exception,
    *,
    operation: str,
    invalid_status: int = 422,
) -> None:
    if isinstance(exc, ConversationNotFoundError):
        raise HTTPException(status_code=404, detail="Conversation not found.") from exc
    if isinstance(exc, (ConversationCapacityError, ConversationConflictError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ConversationIntegrityError):
        logger.error("Saved conversation integrity failure during %s", operation)
        raise HTTPException(
            status_code=500,
            detail="Saved conversation data could not be restored.",
        ) from exc
    if isinstance(exc, ConversationSnapshotTooLargeError):
        raise HTTPException(
            status_code=507,
            detail="The chatbot response is too large to save safely.",
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=invalid_status, detail=str(exc)) from exc
    logger.warning(
        "Conversation repository operation failed during %s: %s",
        operation,
        type(exc).__name__,
    )
    raise HTTPException(
        status_code=503,
        detail="Conversation storage is temporarily unavailable.",
    ) from exc


@app.post(
    "/conversations",
    response_model=ConversationSummaryResponse,
    status_code=201,
)
def create_conversation(
    req: ConversationCreateRequest,
    request: Request,
):
    user_id = _conversation_user_id(request)
    try:
        title = (
            validate_conversation_title(req.title)
            if req.title is not None
            else None
        )
        conversation = get_conversation_repository().create(
            user_id,
            title,
            max_conversations=_max_conversations_per_user(),
            conversation_id=str(req.conversation_id),
            title_manually_set=title is not None,
        )
        return _conversation_summary(conversation)
    except Exception as exc:
        _raise_conversation_http_error(exc, operation="create")


@app.get("/conversations", response_model=ConversationListResponse)
def list_conversations(
    request: Request,
    limit: int = 20,
    before: str | None = None,
):
    user_id = _conversation_user_id(request)
    try:
        page = get_conversation_repository().list(
            user_id,
            limit=limit,
            before=before,
        )
        return ConversationListResponse(
            conversations=[_conversation_summary(item) for item in page.items],
            next_cursor=page.next_cursor,
        )
    except Exception as exc:
        _raise_conversation_http_error(exc, operation="list")


@app.delete(
    "/conversations",
    response_model=ConversationDeleteAllResponse,
)
def delete_all_conversations(request: Request):
    """Erase every saved room for the authenticated user, including expired rows."""
    user_id = _conversation_user_id(request)
    try:
        deleted = get_conversation_repository().hard_delete_all_by_user(user_id)
        return {"deleted": deleted}
    except Exception as exc:
        _raise_conversation_http_error(exc, operation="delete_all")


@app.get(
    "/conversations/{conversation_id}",
    response_model=PublicConversationDetailResponse,
)
def get_conversation(
    conversation_id: str,
    request: Request,
    message_limit: int = Query(default=50, ge=1, le=100),
    before: str | None = None,
):
    user_id = _conversation_user_id(request)
    repository = get_conversation_repository()
    try:
        conversation = repository.get(user_id, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError("Conversation not found.")
        page = repository.list_messages(
            user_id,
            conversation_id,
            limit=message_limit,
            before=before,
        )
        return ConversationDetailResponse(
            conversation=_conversation_summary(conversation),
            messages=[
                _conversation_message_response(item)
                for item in page.items
            ],
            next_cursor=page.next_cursor,
        )
    except Exception as exc:
        _raise_conversation_http_error(exc, operation="get")


@app.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationSummaryResponse,
)
def rename_conversation(
    conversation_id: str,
    req: ConversationRenameRequest,
    request: Request,
):
    user_id = _conversation_user_id(request)
    try:
        conversation = get_conversation_repository().rename(
            user_id,
            conversation_id,
            validate_conversation_title(req.title),
        )
        return _conversation_summary(conversation)
    except Exception as exc:
        _raise_conversation_http_error(exc, operation="rename")


@app.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, request: Request):
    user_id = _conversation_user_id(request)
    try:
        if not get_conversation_repository().hard_delete(user_id, conversation_id):
            raise ConversationNotFoundError("Conversation not found.")
        return Response(status_code=204)
    except Exception as exc:
        _raise_conversation_http_error(exc, operation="delete")


def _chat_persisted(req: ChatRequest, *, user_id: int) -> ChatResponse:
    repository = get_conversation_repository()
    conversation_id = str(req.conversation_id)
    turn_id = str(req.turn_id)
    fingerprint = _request_fingerprint(req)
    try:
        conversation = repository.get(user_id, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError("Conversation not found.")

        existing = repository.get_exchange_by_turn_id(
            user_id,
            conversation_id,
            turn_id,
        )
        if existing is not None:
            return _restore_exchange(
                existing,
                user_id=user_id,
                expected_user_content=req.message,
                expected_fingerprint=fingerprint,
            )

        message_page = repository.list_messages(
            user_id,
            conversation_id,
            limit=MAX_MESSAGE_LIST_LIMIT,
        )
        processing_req = _processing_request(
            req,
            conversation_state=_trusted_saved_conversation_state(conversation),
            messages=message_page.items,
        )
        response, resolved_product, activity_context = _run_chat_pipeline(
            processing_req,
            log_activity=False,
            allow_raw_audit_message=False,
        )
        # Same post-processing as the stateless path: polish, team bounds and chips.
        from app.services.answer_polish import polish_product_answer
        response = polish_product_answer(response)
        if _team_unverified_catalog():
            response = _team_catalog_response(response, resolved_product=resolved_product)
        from app.services.followup_questions import tidy_followups
        response = tidy_followups(response, processing_req.message, team_mode=_team_unverified_catalog())
        response = response.model_copy(update={
            "external_ai_processing": ExternalAIProcessingSummary.model_validate(
                current_external_ai_processing_summary()
            ),
        })

        auto_title = None
        if (
            not conversation.title_manually_set
            and conversation.title == DEFAULT_CONVERSATION_TITLE
        ):
            auto_title = generate_conversation_title(
                req.message,
                verified_product_name=(
                    resolved_product.product_name
                    if resolved_product is not None
                    and resolved_product.facts_verified
                    else None
                ),
                intent=response.intent,
            )
        next_state = response.internal_conversation_state or response.conversation_state
        stored_view = response.model_copy(update={
            "conversation_id": conversation_id,
            "conversation_title": auto_title or conversation.title,
            "conversation_revision": conversation.revision + 1,
            "turn_id": turn_id,
            "conversation_state": {},
            "internal_conversation_state": {},
        })
        write_attempt_id = secrets.token_hex(16)
        exchange = repository.append_exchange(
            user_id,
            conversation_id,
            turn_id=turn_id,
            user_content=req.message,
            assistant_content=stored_view.answer,
            assistant_metadata=_assistant_message_metadata(
                stored_view,
                request_fingerprint=fingerprint,
                write_attempt_id=write_attempt_id,
            ),
            new_state=next_state,
            auto_title=auto_title,
            expected_revision=conversation.revision,
        )
        restored = _restore_exchange(
            exchange,
            user_id=user_id,
            expected_user_content=req.message,
            expected_fingerprint=fingerprint,
        )
        if exchange[1].metadata.get("write_attempt_id") == write_attempt_id:
            _log_pipeline_response(
                restored,
                activity_context=activity_context,
                allow_raw_audit_message=False,
                trusted_user_id=user_id,
            )
        return restored
    except Exception as exc:
        _raise_conversation_http_error(exc, operation="chat", invalid_status=500)


@app.post("/chat", response_model=PublicChatResponse)
def chat_http(req: ChatRequest, request: Request, response: Response):
    from app.core.backend_analysis import ANALYSIS_VERSION, CONTRACT_HEADER, CONTEXT_HEADER
    ctx = req.analysis_context
    if ctx is not None:
        _assert_chat_service_token(request, feature="Backend analysis")
        if (request.headers.get(CONTRACT_HEADER) != ANALYSIS_VERSION
                or request.headers.get(CONTEXT_HEADER) != ctx.contextId):
            raise HTTPException(status_code=422, detail={"code": "AI_ANALYSIS_CONTRACT_MISMATCH"})
    elif request.headers.get(CONTRACT_HEADER) or request.headers.get(CONTEXT_HEADER):
        raise HTTPException(status_code=422, detail={"code": "AI_ANALYSIS_CONTEXT_REQUIRED"})
    from app.infrastructure.llm import llm_request_budget
    with llm_request_budget():
        from app.infrastructure.external_ai_consent import verified_request_user_id
        result = (_stateless_chat_response(req, trusted_user_id=verified_request_user_id())
                  if req.conversation_id is None
                  else _chat_persisted(req, user_id=_conversation_user_id(request)))
    if ctx is not None:
        if result.backend_analysis is None or result.backend_analysis.contextId != ctx.contextId:
            raise HTTPException(status_code=500, detail={"code": "AI_ANALYSIS_NOT_PROCESSED"})
        response.headers[CONTRACT_HEADER] = ANALYSIS_VERSION
        response.headers[CONTEXT_HEADER] = ctx.contextId
    return result


@app.post(
    "/products/{barcode}/report",
    response_model=ProductLabelMismatchReportResponse,
    status_code=202,
)
def report_product_label_mismatch(
    barcode: PublicProductIdentifier,
    req: ProductLabelMismatchReportRequest,
    request: Request,
):
    """Record a bounded package-label mismatch without storing chat text."""

    _assert_chat_service_token(request, feature="Product label reports")
    user_id = _signed_request_user_id(request, purpose="conversation")
    product = find_product_by_barcode(barcode)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    try:
        get_activity_repository().save(ActivityRecord(
            user_id=user_id,
            barcode=product.barcode or barcode,
            report_number=product.report_number,
            product_id=product.product_id,
            action_type=f"PRODUCT_LABEL_MISMATCH_{req.field.upper()}",
        ))
    except Exception as exc:
        logger.warning(
            "Product label mismatch report storage failed: %s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="Product label report storage is temporarily unavailable.",
        ) from exc
    return ProductLabelMismatchReportResponse(barcode=barcode, field=req.field)
