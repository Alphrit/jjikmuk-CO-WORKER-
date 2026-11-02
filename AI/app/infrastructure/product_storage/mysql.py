"""product storage / mysql repository implementation."""

from app.api.adapters import normalize_product_payload
from app.core.models import Product
from app.data.dictionaries import alternative_categories_for
from typing import Any, Dict, List, Optional

from app.infrastructure.mysql_support import (
    _mysql_connection_pool_settings,
    _mysql_connection_timeout_kwargs,
    _mysql_connection_tls_kwargs,
    _quote_mysql_identifier,
    _shared_mysql_connection_pool,
)

from app.infrastructure.product_storage.common import (
    ALLERGY_COLUMNS,
    BARCODE_COLUMNS,
    CLEAN_PRODUCT_NAME_COLUMNS,
    MANUFACTURER_COLUMNS,
    NUTRITION_COLUMNS,
    PRODUCT_ID_COLUMNS,
    PRODUCT_LABEL_PROVENANCE_COLUMNS,
    PRODUCT_NAME_COLUMNS,
    PRODUCT_TYPE_COLUMNS,
    ProductRepository,
    RAW_MATERIAL_COLUMNS,
    RECOMMENDATION_QUERY_MIN_ROWS,
    RECOMMENDATION_QUERY_OVERSAMPLE_FACTOR,
    REPORT_NUMBER_COLUMNS,
    SEARCH_KEYWORD_COLUMNS,
    _alternative_search_tokens,
    _alternative_similarity,
    _dedupe_scored_products,
    _expanded_product_name_tokens,
    _product_text_score,
    _same_product_identity,
    _select_preferred_fulltext_index,
    _should_merge_like_fallback,
    _tokenize_search_text,
)

import logging

logger = logging.getLogger(__name__)


class MySQLProductRepository(ProductRepository):
    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        database: str = "jjikmuk",
        user: str = "root",
        password: str = "",
        table_name: str = "products",
        charset: str = "utf8mb4",
        ensure_search_indexes: bool = False,
    ):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as exc:
            raise RuntimeError(
                "MySQL DB를 사용하려면 PyMySQL이 필요합니다. "
                "`pip install -r requirements.txt`를 다시 실행해주세요."
            ) from exc

        self._pymysql = pymysql
        self._cursor_class = DictCursor
        self.connection_kwargs = {
            "host": host,
            "port": int(port),
            "database": database,
            "user": user,
            "password": password,
            "charset": charset,
            "cursorclass": DictCursor,
            **_mysql_connection_timeout_kwargs(),
            **_mysql_connection_tls_kwargs(),
        }
        self.connection_pool_settings = _mysql_connection_pool_settings()
        self._connection_pool = None
        self.table_name = table_name
        self.quoted_table_name = _quote_mysql_identifier(table_name)
        self.ensure_search_indexes = False
        if ensure_search_indexes:
            logger.warning(
                "Runtime MySQL search-index creation is disabled; apply AI/migrations instead."
            )

    def _connect(self):
        if self._connection_pool is None:
            self._connection_pool = _shared_mysql_connection_pool(
                self._pymysql,
                self.connection_kwargs,
                self.connection_pool_settings,
            )
        return self._connection_pool.connect()

    def _is_database_error(self, exc: Exception) -> bool:
        return isinstance(exc, (self._pymysql.MySQLError, OSError, TimeoutError))

    def readiness_check(self) -> Dict[str, Any]:
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(f"SELECT 1 FROM {self.quoted_table_name} LIMIT 1")
                    product_row_available = cursor.fetchone() is not None
                    cursor.execute(f"SHOW COLUMNS FROM {self.quoted_table_name}")
                    columns = {
                        row["Field"]
                        for row in cursor.fetchall()
                        if isinstance(row, dict) and row.get("Field")
                    }
            identity_available = bool(
                columns
                & set(PRODUCT_ID_COLUMNS + BARCODE_COLUMNS + REPORT_NUMBER_COLUMNS)
            )
            product_name_available = bool(
                columns & set(PRODUCT_NAME_COLUMNS + CLEAN_PRODUCT_NAME_COLUMNS)
            )
            safety_evidence_available = bool(
                columns
                & set(RAW_MATERIAL_COLUMNS + ALLERGY_COLUMNS + NUTRITION_COLUMNS)
            )
            label_lineage_schema_ready = PRODUCT_LABEL_PROVENANCE_COLUMNS.issubset(
                columns
            )
            schema_ready = (
                identity_available
                and product_name_available
                and safety_evidence_available
            )
            available = schema_ready and product_row_available
            return {
                "available": available,
                "table": self.table_name,
                "identity_column_available": identity_available,
                "product_name_column_available": product_name_available,
                "safety_evidence_column_available": safety_evidence_available,
                "label_lineage_schema_ready": label_lineage_schema_ready,
                "mysql_session_timezone_contract": "+00:00",
                "missing_label_lineage_columns": sorted(
                    PRODUCT_LABEL_PROVENANCE_COLUMNS - columns
                ),
                "product_row_available": product_row_available,
                "reason": (
                    None
                    if available
                    else (
                        "product_safety_schema_incomplete"
                        if not schema_ready
                        else "product_catalog_empty"
                    )
                ),
            }
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            return {
                "available": False,
                "table": self.table_name,
                "reason": "mysql_product_check_failed",
                "error_type": type(exc).__name__,
            }

    def health_check(self) -> Dict[str, Any]:
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(f"SELECT COUNT(*) AS count FROM {self.quoted_table_name}")
                    row = cursor.fetchone()
                search_index = self.search_index_status(conn)
            return {
                "available": True,
                "table": self.table_name,
                "row_count": row["count"] if row else None,
                "search_index": search_index,
            }
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            return {
                "available": False,
                "table": self.table_name,
                "reason": "mysql_product_check_failed",
                "error_type": type(exc).__name__,
            }

    def is_available(self) -> bool:
        return bool(self.health_check().get("available"))

    def _find_existing_column(self, conn, candidates: list[str]) -> Optional[str]:
        with conn.cursor() as cursor:
            cursor.execute(f"SHOW COLUMNS FROM {self.quoted_table_name}")
            rows = cursor.fetchall()

        existing_columns = {row["Field"] for row in rows}
        for candidate in candidates:
            if candidate in existing_columns:
                return candidate
        return None

    def _existing_columns(self, conn) -> set[str]:
        with conn.cursor() as cursor:
            cursor.execute(f"SHOW COLUMNS FROM {self.quoted_table_name}")
            rows = cursor.fetchall()
        return {row["Field"] for row in rows}

    def _search_columns(self, conn) -> List[str]:
        existing_columns = self._existing_columns(conn)
        return [
            column
            for column in [
                *SEARCH_KEYWORD_COLUMNS,
                *CLEAN_PRODUCT_NAME_COLUMNS,
                *PRODUCT_NAME_COLUMNS,
                *MANUFACTURER_COLUMNS,
                *RAW_MATERIAL_COLUMNS,
                *PRODUCT_TYPE_COLUMNS,
            ]
            if column in existing_columns
        ]

    def search_index_status(self, conn=None) -> Dict[str, Any]:
        should_close = conn is None
        if conn is None:
            conn = self._connect()
        try:
            search_columns = self._search_columns(conn)
            with conn.cursor() as cursor:
                cursor.execute(f"SHOW INDEX FROM {self.quoted_table_name} WHERE Index_type = 'FULLTEXT'")
                rows = cursor.fetchall()
            index_names = sorted({row["Key_name"] for row in rows})
            indexed_columns = sorted({row["Column_name"] for row in rows})
            columns_by_index: Dict[str, List[str]] = {}
            for row in sorted(rows, key=lambda item: (item["Key_name"], item["Seq_in_index"])):
                columns_by_index.setdefault(row["Key_name"], []).append(row["Column_name"])
            usable_index, usable_columns = _select_preferred_fulltext_index(columns_by_index, search_columns)
            return {
                "available": bool(index_names),
                "type": "FULLTEXT" if index_names else "none",
                "indexes": index_names,
                "columns": indexed_columns,
                "target_columns": search_columns,
                "usable_index": usable_index,
                "usable_columns": usable_columns,
            }
        finally:
            if should_close:
                conn.close()

    def ensure_search_index(self) -> Dict[str, Any]:
        try:
            with self._connect() as conn:
                status = self.search_index_status(conn)
            return {
                **status,
                "runtime_ddl_enabled": False,
                "migration_required": not bool(status.get("usable_columns")),
                "schema_change_source": "AI/migrations",
            }
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            logger.warning(
                "MySQL product search-index validation failed: %s",
                type(exc).__name__,
            )
            return {
                "available": False,
                "type": "FULLTEXT",
                "runtime_ddl_enabled": False,
                "migration_required": True,
                "schema_change_source": "AI/migrations",
                "error_type": type(exc).__name__,
            }

    def get_by_product_id(self, product_id: int) -> Optional[Product]:
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id < 0:
            return None
        try:
            with self._connect() as conn:
                product_id_column = self._find_existing_column(conn, ["product_id", "id"])
                if product_id_column is None:
                    return None
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"SELECT * FROM {self.quoted_table_name} WHERE {_quote_mysql_identifier(product_id_column)} = %s LIMIT 1",
                        (product_id,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            logger.warning("MySQL product-id lookup failed: %s", type(exc).__name__)
            return None

        return normalize_product_payload(
            row, repository_record=True
        ) if row is not None else None

    def get_by_barcode(self, barcode: str) -> Optional[Product]:
        try:
            with self._connect() as conn:
                barcode_column = self._find_existing_column(conn, ["barcode", "바코드", "BAR_CD"])
                if barcode_column is None:
                    return None
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"SELECT * FROM {self.quoted_table_name} WHERE {_quote_mysql_identifier(barcode_column)} = %s LIMIT 1",
                        (barcode,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            logger.warning("MySQL product barcode lookup failed: %s", exc)
            return None

        if row is None:
            return None
        return normalize_product_payload(row, repository_record=True)

    def get_by_report_number(self, report_number: str) -> Optional[Product]:
        try:
            with self._connect() as conn:
                report_column = self._find_existing_column(
                    conn,
                    ["report_number", "report_no", "품목제조보고번호", "품목보고번호", "PRDLST_REPORT_NO"],
                )
                if report_column is None:
                    return None
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"SELECT * FROM {self.quoted_table_name} WHERE {_quote_mysql_identifier(report_column)} = %s LIMIT 1",
                        (report_number,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            logger.warning("MySQL product report-number lookup failed: %s", exc)
            return None

        if row is None:
            return None
        return normalize_product_payload(row, repository_record=True)

    def search_by_text(self, text: str) -> Optional[Product]:
        candidates = self.search_candidates(text, limit=1)
        return candidates[0] if candidates else None

    def _fulltext_boolean_query(self, tokens: List[str]) -> str:
        safe_tokens = [
            "".join(char for char in token if char.isalnum())
            for token in tokens
        ]
        safe_tokens = [token for token in safe_tokens if len(token) >= 2]
        return " ".join(f"+{token}*" for token in safe_tokens)

    def _fetch_fulltext_rows(self, conn, columns: List[str], tokens: List[str], limit: int):
        query = self._fulltext_boolean_query(tokens)
        if not columns or not query:
            return []

        columns_sql = ", ".join(_quote_mysql_identifier(column) for column in columns)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *, MATCH ({columns_sql}) AGAINST (%s IN BOOLEAN MODE) AS search_score
                FROM {self.quoted_table_name}
                WHERE MATCH ({columns_sql}) AGAINST (%s IN BOOLEAN MODE)
                ORDER BY search_score DESC
                LIMIT %s
                """,
                (query, query, limit),
            )
            return cursor.fetchall()

    def _fetch_like_rows(self, conn, columns: List[str], tokens: List[str], limit: int, preferred_tokens=None):
        if not columns or not tokens:
            return []

        clauses = []
        params = []
        score_parts = []
        score_params = []
        compact_query = "".join(preferred_tokens or [])
        for column in columns:
            if compact_query and column in (*PRODUCT_NAME_COLUMNS, *CLEAN_PRODUCT_NAME_COLUMNS):
                compact_name = f"REPLACE(LOWER({_quote_mysql_identifier(column)}), ' ', '')"
                score_parts.append(f"CASE WHEN {compact_name} = %s THEN 100000 WHEN {compact_name} LIKE %s THEN 10000 ELSE 0 END")
                score_params.extend([compact_query, f"%{compact_query}%"])
        # Rank coverage of the original request ahead of expanded broad food
        # terms before LIMIT truncates the candidate pool.
        for token in preferred_tokens or []:
            parts = [f"{_quote_mysql_identifier(column)} LIKE %s" for column in columns]
            score_parts.append(f"CASE WHEN ({' OR '.join(parts)}) THEN 1000 ELSE 0 END")
            score_params.extend([f"%{token}%"] * len(columns))
        for column in columns:
            quoted_column = _quote_mysql_identifier(column)
            for token in tokens:
                clauses.append(f"{quoted_column} LIKE %s")
                params.append(f"%{token}%")
                score_parts.append(f"CASE WHEN {quoted_column} LIKE %s THEN 1 ELSE 0 END")
                score_params.append(f"%{token}%")

        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *, ({' + '.join(score_parts)}) AS like_match_count
                FROM {self.quoted_table_name}
                WHERE {' OR '.join(clauses)}
                ORDER BY like_match_count DESC
                LIMIT %s
                """,
                (*score_params, *params, limit),
            )
            return cursor.fetchall()

    def search_candidates(self, text: str, limit: int = 3) -> List[Product]:
        normalized_text = text.strip()
        if not normalized_text:
            return []

        try:
            with self._connect() as conn:
                product_name_column = self._find_existing_column(conn, PRODUCT_NAME_COLUMNS)
                if product_name_column is None:
                    return []
                search_columns = self._search_columns(conn)

                primary_tokens = _tokenize_search_text(normalized_text)
                tokens = list(dict.fromkeys([*primary_tokens, *_expanded_product_name_tokens(normalized_text)]))
                if not tokens:
                    return []
                rows = []
                search_index = self.search_index_status(conn)
                fulltext_columns = search_index.get("usable_columns") or []
                if fulltext_columns:
                    try:
                        rows = self._fetch_fulltext_rows(conn, fulltext_columns, tokens, 50)
                    except Exception as exc:
                        if not self._is_database_error(exc):
                            raise
                        logger.warning("MySQL fulltext product search failed; falling back to LIKE: %s", exc)
                if len(primary_tokens) > 1 or _should_merge_like_fallback(fulltext_columns, len(rows)):
                    like_rows = self._fetch_like_rows(conn, search_columns, tokens, 50, preferred_tokens=primary_tokens)
                    rows = [*rows, *like_rows]
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            logger.warning("MySQL product text search failed: %s", exc)
            return []

        scored = []
        for row in rows:
            product = normalize_product_payload(row, repository_record=True)
            if product is None:
                continue
            score = _product_text_score(normalized_text, product)
            if score >= 0.35:
                scored.append((score, product))

        scored = _dedupe_scored_products(scored)
        return [
            product.model_copy(update={"match_confidence": round(score, 3)})
            for score, product in scored[:limit]
        ]

    def recommend_alternatives(self, product: Product, limit: int = 3) -> List[Product]:
        try:
            with self._connect() as conn:
                category_column = self._find_existing_column(conn, PRODUCT_TYPE_COLUMNS)
                product_name_column = self._find_existing_column(conn, PRODUCT_NAME_COLUMNS)
                clean_name_column = self._find_existing_column(conn, CLEAN_PRODUCT_NAME_COLUMNS)
                search_keywords_column = self._find_existing_column(conn, SEARCH_KEYWORD_COLUMNS)
                barcode_column = self._find_existing_column(conn, ["barcode", "바코드", "BAR_CD"])
                report_column = self._find_existing_column(
                    conn,
                    ["report_number", "report_no", "품목제조보고번호", "품목보고번호", "PRDLST_REPORT_NO"],
                )

                alternative_categories = alternative_categories_for(product.prdlst_dcnm)
                where_parts = []
                params = []
                name_columns = [column for column in [search_keywords_column, clean_name_column, product_name_column] if column]
                name_tokens = _alternative_search_tokens(product)
                rows = []
                search_index = self.search_index_status(conn)
                usable_columns = search_index.get("usable_columns") or []
                fulltext_columns = usable_columns if all(column in name_columns for column in usable_columns) else []
                if fulltext_columns:
                    try:
                        rows = self._fetch_fulltext_rows(
                            conn,
                            fulltext_columns,
                            name_tokens,
                            max(limit * RECOMMENDATION_QUERY_OVERSAMPLE_FACTOR, RECOMMENDATION_QUERY_MIN_ROWS),
                        )
                    except Exception as exc:
                        if not self._is_database_error(exc):
                            raise
                        logger.warning("MySQL fulltext alternative lookup failed; falling back to LIKE: %s", exc)

                if not rows and name_columns and name_tokens:
                    name_clauses = []
                    for column in name_columns:
                        for token in name_tokens:
                            name_clauses.append(f"{_quote_mysql_identifier(column)} LIKE %s")
                            params.append(f"%{token}%")
                    where_parts.append(f"({' OR '.join(name_clauses)})")

                if category_column and alternative_categories:
                    placeholders = ", ".join("%s" for _ in alternative_categories)
                    where_parts.append(f"{_quote_mysql_identifier(category_column)} IN ({placeholders})")
                    params.extend(alternative_categories)
                if barcode_column and product.barcode:
                    where_parts.append(f"({_quote_mysql_identifier(barcode_column)} IS NULL OR {_quote_mysql_identifier(barcode_column)} != %s)")
                    params.append(product.barcode)
                if report_column and product.report_number:
                    where_parts.append(f"({_quote_mysql_identifier(report_column)} IS NULL OR {_quote_mysql_identifier(report_column)} != %s)")
                    params.append(product.report_number)

                where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
                if not rows:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            f"SELECT * FROM {self.quoted_table_name} {where_sql} LIMIT %s",
                            (
                                *params,
                                max(limit * RECOMMENDATION_QUERY_OVERSAMPLE_FACTOR, RECOMMENDATION_QUERY_MIN_ROWS),
                            ),
                        )
                        rows = cursor.fetchall()
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            logger.warning("MySQL alternative recommendation lookup failed: %s", exc)
            return []

        scored = []
        for row in rows:
            candidate = normalize_product_payload(row, repository_record=True)
            if candidate is None or _same_product_identity(product, candidate):
                continue
            similarity = _alternative_similarity(product, candidate)
            if similarity < 0.2 and not (
                product.prdlst_dcnm and candidate.prdlst_dcnm in alternative_categories
            ):
                continue
            scored.append((similarity, candidate))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            candidate.model_copy(update={"match_confidence": round(score, 3)})
            for score, candidate in scored[:limit]
        ]
