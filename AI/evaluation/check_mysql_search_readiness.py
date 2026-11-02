import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


AI_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = Path(__file__).with_name("results")
SAMPLE_QUERIES = ["단백질칩", "두유바", "매운새우깡"]


def _safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise ValueError(f"Unsafe MySQL identifier: {value}")
    return f"`{value}`"


def _fetch_one(cursor, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    cursor.execute(sql, params)
    return cursor.fetchone() or {}


def _ngram_token_size(cursor) -> int | None:
    try:
        row = _fetch_one(cursor, "SHOW VARIABLES LIKE 'ngram_token_size'")
        value = row.get("Value")
        return int(value) if value is not None else None
    except Exception:
        return None


def _show_create_uses_ngram(cursor, quoted_table: str) -> bool | None:
    try:
        row = _fetch_one(cursor, f"SHOW CREATE TABLE {quoted_table}")
        create_sql = next(
            (value for key, value in row.items() if "create table" in key.lower()),
            None,
        )
        if not isinstance(create_sql, str):
            return None
        return bool(re.search(r"WITH\s+PARSER\s+`?ngram`?", create_sql, flags=re.IGNORECASE))
    except Exception:
        return None


def _sample_fulltext_results(cursor, quoted_table: str) -> list[dict[str, Any]]:
    results = []
    for query in SAMPLE_QUERIES:
        try:
            cursor.execute(
                f"""
                SELECT barcode, product_name,
                       MATCH (`search_keywords`) AGAINST (%s IN BOOLEAN MODE) AS search_score
                FROM {quoted_table}
                WHERE MATCH (`search_keywords`) AGAINST (%s IN BOOLEAN MODE)
                ORDER BY search_score DESC
                LIMIT 5
                """,
                (query, query),
            )
            rows = cursor.fetchall()
            results.append(
                {
                    "query": query,
                    "result_count": len(rows),
                    "products": [
                        {
                            "barcode": row.get("barcode"),
                            "product_name": row.get("product_name"),
                            "score": round(float(row.get("search_score") or 0), 4),
                        }
                        for row in rows
                    ],
                    "error": None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "query": query,
                    "result_count": 0,
                    "products": [],
                    "error": str(exc),
                }
            )
    return results


def check_readiness() -> dict[str, Any]:
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError as exc:
        raise RuntimeError("PyMySQL is required. Install AI/requirements.txt first.") from exc

    load_dotenv(AI_DIR / ".env")
    database = os.getenv("FOOD_CHATBOT_MYSQL_DATABASE", "jjikmuk")
    table = os.getenv("FOOD_CHATBOT_MYSQL_PRODUCT_TABLE", "products")
    quoted_table = _safe_identifier(table)
    connection = pymysql.connect(
        host=os.getenv("FOOD_CHATBOT_MYSQL_HOST", "localhost"),
        port=int(os.getenv("FOOD_CHATBOT_MYSQL_PORT", "3306")),
        database=database,
        user=os.getenv("FOOD_CHATBOT_MYSQL_USER", "root"),
        password=os.getenv("FOOD_CHATBOT_MYSQL_PASSWORD", ""),
        charset="utf8mb4",
        cursorclass=DictCursor,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )

    try:
        with connection.cursor() as cursor:
            version_row = _fetch_one(cursor, "SELECT VERSION() AS version")
            cursor.execute(f"SHOW COLUMNS FROM {quoted_table}")
            columns = [row["Field"] for row in cursor.fetchall()]
            search_keywords_exists = "search_keywords" in columns

            cursor.execute(f"SHOW INDEX FROM {quoted_table} WHERE Index_type = 'FULLTEXT'")
            fulltext_rows = cursor.fetchall()
            fulltext_indexes: dict[str, list[str]] = {}
            for row in sorted(fulltext_rows, key=lambda item: (item["Key_name"], item["Seq_in_index"])):
                fulltext_indexes.setdefault(row["Key_name"], []).append(row["Column_name"])

            total_row = _fetch_one(cursor, f"SELECT COUNT(*) AS count FROM {quoted_table}")
            total_rows = int(total_row.get("count") or 0)
            populated_rows = 0
            if search_keywords_exists:
                populated_row = _fetch_one(
                    cursor,
                    f"""
                    SELECT COUNT(*) AS count
                    FROM {quoted_table}
                    WHERE search_keywords IS NOT NULL AND TRIM(search_keywords) <> ''
                    """,
                )
                populated_rows = int(populated_row.get("count") or 0)

            indexed_with_search_keywords = any(
                "search_keywords" in index_columns
                for index_columns in fulltext_indexes.values()
            )
            legacy_fulltext_indexes = {
                index_name: index_columns
                for index_name, index_columns in fulltext_indexes.items()
                if "search_keywords" not in index_columns
            }
            ngram_token_size = _ngram_token_size(cursor)
            show_create_uses_ngram = _show_create_uses_ngram(cursor, quoted_table)
            sample_results = (
                _sample_fulltext_results(cursor, quoted_table)
                if indexed_with_search_keywords
                else []
            )
    finally:
        connection.close()

    populated_ratio = round((populated_rows / total_rows) * 100, 2) if total_rows else 0.0
    sample_search_ok = bool(sample_results) and all(result["error"] is None for result in sample_results)
    ready = all(
        [
            search_keywords_exists,
            populated_rows == total_rows and total_rows > 0,
            indexed_with_search_keywords,
            ngram_token_size is not None,
            show_create_uses_ngram is True,
            sample_search_ok,
        ]
    )

    return {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "purpose": "Read-only MySQL search_keywords and ngram readiness check",
        "database": database,
        "table": table,
        "mysql_version": version_row.get("version"),
        "total_rows": total_rows,
        "search_keywords": {
            "column_exists": search_keywords_exists,
            "populated_rows": populated_rows,
            "population_ratio": populated_ratio,
        },
        "fulltext": {
            "indexes": fulltext_indexes,
            "legacy_indexes": legacy_fulltext_indexes,
            "legacy_index_count": len(legacy_fulltext_indexes),
            "search_keywords_indexed": indexed_with_search_keywords,
            "show_create_uses_ngram": show_create_uses_ngram,
        },
        "ngram": {
            "token_size": ngram_token_size,
            "configured": ngram_token_size is not None,
        },
        "sample_searches": sample_results,
        "safety": {
            "read_only": True,
            "ddl_executed": False,
            "data_modified": False,
            "credentials_in_report": False,
        },
        "final_summary": {
            "ready": ready,
            "column_ready": search_keywords_exists,
            "backfill_ready": populated_rows == total_rows and total_rows > 0,
            "fulltext_ready": indexed_with_search_keywords,
            "server_ngram_configured": ngram_token_size is not None,
            "ngram_index_ready": show_create_uses_ngram is True,
            "sample_search_ready": sample_search_ok,
            "legacy_fulltext_index_count": len(legacy_fulltext_indexes),
            "next_action": (
                "Run product search quality comparison."
                if ready
                else "Wait for the DB owner to finish column, backfill, and ngram FULLTEXT deployment, then rerun this check."
            ),
        },
    }


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    output_path = RESULTS_DIR / f"mysql_search_readiness_{stamp}.json"
    try:
        result = check_readiness()
    except Exception as exc:
        result = {
            "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "purpose": "Read-only MySQL search_keywords and ngram readiness check",
            "error": str(exc),
            "final_summary": {
                "ready": False,
                "blocking_error": str(exc),
            },
        }
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = result["final_summary"]
    print(f"ready: {summary.get('ready', False)}")
    print(f"column_ready: {summary.get('column_ready', False)}")
    print(f"backfill_ready: {summary.get('backfill_ready', False)}")
    print(f"fulltext_ready: {summary.get('fulltext_ready', False)}")
    print(f"server_ngram_configured: {summary.get('server_ngram_configured', False)}")
    print(f"ngram_index_ready: {summary.get('ngram_index_ready', False)}")
    print(f"saved_result: {output_path}")
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
