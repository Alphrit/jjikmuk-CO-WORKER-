"""Compatibility imports for product/activity repositories.

Implementations live in product_storage, activity_storage and mysql_support.
Existing application imports keep referring to the same classes and shared pool.
"""

from app.infrastructure.activity_storage.common import (
    ActivityRepository,
    _normalize_unknown_message,
    _activity_created_at_sort_value,
    _response_product_label_trace_json,
    _decode_response_product_label_trace,
    _normalize_nullable_activity_list_fields,
    _fallback_failure_types,
    _message_preview,
    _fallback_log_payload,
    _fallback_summary_from_records,
)

from app.infrastructure.activity_storage.memory import (
    InMemoryActivityRepository,
)

from app.infrastructure.activity_storage.mysql import (
    MySQLActivityRepository,
)

from app.infrastructure.activity_storage.sqlite import (
    SQLiteActivityRepository,
)

from app.infrastructure.mysql_support import (
    MYSQL_CONNECT_TIMEOUT_SECONDS_DEFAULT,
    MYSQL_READ_TIMEOUT_SECONDS_DEFAULT,
    MYSQL_WRITE_TIMEOUT_SECONDS_DEFAULT,
    MYSQL_TIMEOUT_SECONDS_MAX,
    MYSQL_POOL_SIZE_DEFAULT,
    MYSQL_POOL_SIZE_MAX,
    MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS_DEFAULT,
    MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS_MIN,
    MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS_MAX,
    MYSQL_POOL_RECYCLE_SECONDS_DEFAULT,
    MYSQL_POOL_RECYCLE_SECONDS_MIN,
    MYSQL_POOL_RECYCLE_SECONDS_MAX,
    _positive_mysql_timeout_seconds,
    _mysql_connection_timeout_kwargs,
    mysql_tls_configuration_status,
    _mysql_connection_tls_kwargs,
    _bounded_mysql_integer,
    _bounded_mysql_seconds,
    _mysql_connection_pool_settings,
    _SharedMySQLConnectionPool,
    _MYSQL_CONNECTION_POOLS,
    _MYSQL_CONNECTION_POOLS_LOCK,
    _mysql_pool_key_value,
    _shared_mysql_connection_pool,
    dispose_mysql_connection_pools,
    _quote_mysql_identifier,
)

from app.infrastructure.product_storage.common import (
    RECOMMENDATION_QUERY_OVERSAMPLE_FACTOR,
    RECOMMENDATION_QUERY_MIN_ROWS,
    ProductRepository,
    _normalize_search_text,
    _strip_korean_particle,
    SHORT_FOOD_SEARCH_TOKENS,
    _tokenize_search_text,
    GENERIC_PRODUCT_TOKENS,
    SEARCH_KEYWORD_COLUMNS,
    PRODUCT_ID_COLUMNS,
    BARCODE_COLUMNS,
    REPORT_NUMBER_COLUMNS,
    PRODUCT_NAME_COLUMNS,
    CLEAN_PRODUCT_NAME_COLUMNS,
    RAW_MATERIAL_COLUMNS,
    ALLERGY_COLUMNS,
    NUTRITION_COLUMNS,
    PRODUCT_TYPE_COLUMNS,
    MANUFACTURER_COLUMNS,
    PRODUCT_LABEL_PROVENANCE_COLUMNS,
    _select_preferred_fulltext_index,
    _should_merge_like_fallback,
    FOOD_SIMILARITY_TOKENS,
    _distinctive_product_tokens,
    _brand_removed_text,
    _expanded_product_name_tokens,
    build_product_search_keywords,
    _product_search_text,
    _product_text_score,
    _product_candidate_key,
    _product_information_score,
    _dedupe_scored_products,
    _alternative_search_text,
    _alternative_search_tokens,
    _alternative_similarity,
    _same_product_identity,
)

from app.infrastructure.product_storage.memory import (
    InMemoryProductRepository,
)

from app.infrastructure.product_storage.mysql import (
    MySQLProductRepository,
)

from app.infrastructure.product_storage.sqlite import (
    SQLiteProductRepository,
)

__all__ = [
    "ALLERGY_COLUMNS",
    "ActivityRepository",
    "BARCODE_COLUMNS",
    "CLEAN_PRODUCT_NAME_COLUMNS",
    "FOOD_SIMILARITY_TOKENS",
    "GENERIC_PRODUCT_TOKENS",
    "InMemoryActivityRepository",
    "InMemoryProductRepository",
    "MANUFACTURER_COLUMNS",
    "MYSQL_CONNECT_TIMEOUT_SECONDS_DEFAULT",
    "MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS_DEFAULT",
    "MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS_MAX",
    "MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS_MIN",
    "MYSQL_POOL_RECYCLE_SECONDS_DEFAULT",
    "MYSQL_POOL_RECYCLE_SECONDS_MAX",
    "MYSQL_POOL_RECYCLE_SECONDS_MIN",
    "MYSQL_POOL_SIZE_DEFAULT",
    "MYSQL_POOL_SIZE_MAX",
    "MYSQL_READ_TIMEOUT_SECONDS_DEFAULT",
    "MYSQL_TIMEOUT_SECONDS_MAX",
    "MYSQL_WRITE_TIMEOUT_SECONDS_DEFAULT",
    "MySQLActivityRepository",
    "MySQLProductRepository",
    "NUTRITION_COLUMNS",
    "PRODUCT_ID_COLUMNS",
    "PRODUCT_LABEL_PROVENANCE_COLUMNS",
    "PRODUCT_NAME_COLUMNS",
    "PRODUCT_TYPE_COLUMNS",
    "ProductRepository",
    "RAW_MATERIAL_COLUMNS",
    "RECOMMENDATION_QUERY_MIN_ROWS",
    "RECOMMENDATION_QUERY_OVERSAMPLE_FACTOR",
    "REPORT_NUMBER_COLUMNS",
    "SEARCH_KEYWORD_COLUMNS",
    "SHORT_FOOD_SEARCH_TOKENS",
    "SQLiteActivityRepository",
    "SQLiteProductRepository",
    "build_product_search_keywords",
    "dispose_mysql_connection_pools",
    "mysql_tls_configuration_status",
]
