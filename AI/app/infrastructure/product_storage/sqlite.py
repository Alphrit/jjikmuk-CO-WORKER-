"""product storage / sqlite repository implementation."""

from app.api.adapters import normalize_product_payload
from app.core.models import Product
from app.data.dictionaries import alternative_categories_for
from typing import Any, Dict, List, Optional
import sqlite3

from app.infrastructure.product_storage.common import (
    ALLERGY_COLUMNS,
    BARCODE_COLUMNS,
    CLEAN_PRODUCT_NAME_COLUMNS,
    MANUFACTURER_COLUMNS,
    NUTRITION_COLUMNS,
    PRODUCT_ID_COLUMNS,
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
    _tokenize_search_text,
)

import logging

logger = logging.getLogger(__name__)


class SQLiteProductRepository(ProductRepository):
    def __init__(self, db_path: str, table_name: str = "products"):
        self.db_path = db_path
        self.table_name = table_name

    def _find_existing_column(self, conn: sqlite3.Connection, candidates: list[str]) -> Optional[str]:
        rows = conn.execute(f"PRAGMA table_info({self.table_name})").fetchall()
        existing_columns = {row[1] for row in rows}

        for candidate in candidates:
            if candidate in existing_columns:
                return candidate
        return None

    def readiness_check(self) -> Dict[str, Any]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                table_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
                    (self.table_name,),
                ).fetchone() is not None
                if not table_exists:
                    return {
                        "available": False,
                        "table": self.table_name,
                        "reason": "product_table_missing",
                    }
                columns = {
                    row[1]
                    for row in conn.execute(f"PRAGMA table_info({self.table_name})").fetchall()
                }
                product_row_available = conn.execute(
                    f"SELECT 1 FROM {self.table_name} LIMIT 1"
                ).fetchone() is not None
        except (sqlite3.Error, OSError) as exc:
            return {
                "available": False,
                "table": self.table_name,
                "reason": "sqlite_product_check_failed",
                "error_type": type(exc).__name__,
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

    def health_check(self) -> Dict[str, Any]:
        return self.readiness_check()

    def get_by_product_id(self, product_id: int) -> Optional[Product]:
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id < 0:
            return None
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                product_id_column = self._find_existing_column(conn, ["product_id", "id"])
                if product_id_column is None:
                    return None
                row = conn.execute(
                    f"SELECT * FROM {self.table_name} WHERE {product_id_column} = ? LIMIT 1",
                    (product_id,),
                ).fetchone()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("SQLite product-id lookup failed: %s", type(exc).__name__)
            return None

        return normalize_product_payload(
            dict(row), repository_record=True
        ) if row is not None else None

    def get_by_barcode(self, barcode: str) -> Optional[Product]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            barcode_column = self._find_existing_column(conn, ["barcode", "바코드", "BAR_CD"])
            if barcode_column is None:
                return None
            row = conn.execute(
                f"SELECT * FROM {self.table_name} WHERE {barcode_column} = ? LIMIT 1",
                (barcode,),
            ).fetchone()

        if row is None:
            return None

        return normalize_product_payload(dict(row), repository_record=True)

    def get_by_report_number(self, report_number: str) -> Optional[Product]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            report_column = self._find_existing_column(
                conn,
                ["report_number", "report_no", "품목제조보고번호", "품목보고번호", "PRDLST_REPORT_NO"],
            )
            if report_column is None:
                return None

            row = conn.execute(
                f"SELECT * FROM {self.table_name} WHERE {report_column} = ? LIMIT 1",
                (report_number,),
            ).fetchone()

        if row is None:
            return None

        return normalize_product_payload(dict(row), repository_record=True)

    def search_by_text(self, text: str) -> Optional[Product]:
        candidates = self.search_candidates(text, limit=1)
        return candidates[0] if candidates else None

    def search_candidates(self, text: str, limit: int = 3) -> List[Product]:
        normalized_text = text.strip()
        if not normalized_text:
            return []

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            product_name_column = self._find_existing_column(conn, PRODUCT_NAME_COLUMNS)
            if product_name_column is None:
                return []
            clean_name_column = self._find_existing_column(conn, CLEAN_PRODUCT_NAME_COLUMNS)
            search_keywords_column = self._find_existing_column(conn, SEARCH_KEYWORD_COLUMNS)
            manufacturer_column = self._find_existing_column(conn, MANUFACTURER_COLUMNS)
            raw_material_column = self._find_existing_column(conn, RAW_MATERIAL_COLUMNS)
            product_type_column = self._find_existing_column(conn, PRODUCT_TYPE_COLUMNS)
            search_columns = [
                column
                for column in [
                    search_keywords_column,
                    clean_name_column,
                    product_name_column,
                    manufacturer_column,
                    raw_material_column,
                    product_type_column,
                ]
                if column
            ]

            primary_tokens = _tokenize_search_text(normalized_text)
            tokens = list(dict.fromkeys([*primary_tokens, *_expanded_product_name_tokens(normalized_text)]))
            if not tokens:
                return []
            if tokens:
                clauses = []
                params = []
                score_parts = []
                score_params = []
                compact_query = "".join(primary_tokens)
                for column in [product_name_column, clean_name_column]:
                    if column and compact_query:
                        compact_name = f"REPLACE(LOWER({column}), ' ', '')"
                        score_parts.append(f"CASE WHEN {compact_name} = ? THEN 100000 WHEN {compact_name} LIKE ? THEN 10000 ELSE 0 END")
                        score_params.extend([compact_query, f"%{compact_query}%"])
                for token in primary_tokens:
                    parts = [f"{column} LIKE ?" for column in search_columns]
                    score_parts.append(f"CASE WHEN ({' OR '.join(parts)}) THEN 1000 ELSE 0 END")
                    score_params.extend([f"%{token}%"] * len(search_columns))
                for column in search_columns:
                    for token in tokens:
                        clauses.append(f"{column} LIKE ?")
                        params.append(f"%{token}%")
                        score_parts.append(f"CASE WHEN {column} LIKE ? THEN 1 ELSE 0 END")
                        score_params.append(f"%{token}%")
                rows = conn.execute(
                    f"""
                    SELECT *, ({' + '.join(score_parts)}) AS like_match_count
                    FROM {self.table_name}
                    WHERE {' OR '.join(clauses)}
                    ORDER BY like_match_count DESC
                    LIMIT 50
                    """,
                    (*score_params, *params),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM {self.table_name} LIMIT 50",
                ).fetchall()

        scored = []
        for row in rows:
            product = normalize_product_payload(dict(row), repository_record=True)
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
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            category_column = self._find_existing_column(conn, ["prdlst_dcnm", "PRDLST_DCNM", "유형명"])
            product_name_column = self._find_existing_column(
                conn,
                ["product_name", "productName", "식품명", "PRDLST_NM", "food_name"],
            )
            clean_name_column = self._find_existing_column(conn, ["clean_product_name", "cleanProductName"])
            barcode_column = self._find_existing_column(conn, ["barcode", "바코드", "BAR_CD"])
            report_column = self._find_existing_column(
                conn,
                ["report_number", "report_no", "품목제조보고번호", "품목보고번호", "PRDLST_REPORT_NO"],
            )

            where_parts = []
            params = []
            name_columns = [column for column in [clean_name_column, product_name_column] if column]
            name_tokens = _alternative_search_tokens(product)
            if name_columns and name_tokens:
                name_clauses = []
                for column in name_columns:
                    for token in name_tokens:
                        name_clauses.append(f"{column} LIKE ?")
                        params.append(f"%{token}%")
                where_parts.append(f"({' OR '.join(name_clauses)})")

            alternative_categories = alternative_categories_for(product.prdlst_dcnm)
            if category_column and alternative_categories:
                placeholders = ", ".join("?" for _ in alternative_categories)
                where_parts.append(f"{category_column} IN ({placeholders})")
                params.extend(alternative_categories)
            if barcode_column and product.barcode:
                where_parts.append(f"({barcode_column} IS NULL OR {barcode_column} != ?)")
                params.append(product.barcode)
            if report_column and product.report_number:
                where_parts.append(f"({report_column} IS NULL OR {report_column} != ?)")
                params.append(product.report_number)

            where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
            rows = conn.execute(
                f"SELECT * FROM {self.table_name} {where_sql} LIMIT ?",
                (*params, max(limit * RECOMMENDATION_QUERY_OVERSAMPLE_FACTOR, RECOMMENDATION_QUERY_MIN_ROWS)),
            ).fetchall()

        scored = []
        for row in rows:
            candidate = normalize_product_payload(dict(row), repository_record=True)
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
