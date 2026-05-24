import sqlite3
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from app.api.adapters import normalize_product_payload
from app.core.models import ActivityRecord, Product


class ProductRepository:
    def get_by_barcode(self, barcode: str) -> Optional[Product]:
        raise NotImplementedError

    def get_by_report_number(self, report_number: str) -> Optional[Product]:
        raise NotImplementedError

    def search_by_text(self, text: str) -> Optional[Product]:
        raise NotImplementedError

    def search_candidates(self, text: str, limit: int = 3) -> List[Product]:
        raise NotImplementedError

    def recommend_alternatives(self, product: Product, limit: int = 3) -> List[Product]:
        raise NotImplementedError


def _normalize_search_text(text: str) -> str:
    return "".join(char.lower() if char.isalnum() else " " for char in text).strip()


def _tokenize_search_text(text: str) -> List[str]:
    stopwords = {
        "이",
        "이거",
        "이건",
        "제품",
        "어때",
        "괜찮아",
        "괜찮을까",
        "먹어도",
        "돼",
        "되나",
        "설명해줘",
        "추천해줘",
        "먹어도돼",
    }
    return [
        token
        for token in _normalize_search_text(text).split()
        if len(token) >= 2 and token not in stopwords
    ]


GENERIC_PRODUCT_TOKENS = {"농심", "오리온", "매일", "서울우유", "남양", "90g", "50g", "60g", "70g", "200ml", "1l"}


def _distinctive_product_tokens(product_name: str) -> List[str]:
    return [
        token
        for token in _tokenize_search_text(product_name)
        if token not in GENERIC_PRODUCT_TOKENS and not token.isdigit()
    ]


def _product_text_score(query: str, product: Product) -> float:
    if not product.product_name:
        return 0.0

    normalized_query = _normalize_search_text(query)
    normalized_name = _normalize_search_text(product.product_name)
    if not normalized_query or not normalized_name:
        return 0.0

    if normalized_name in normalized_query:
        return 1.0

    query_tokens = set(_tokenize_search_text(normalized_query))
    name_tokens = set(_tokenize_search_text(normalized_name))
    distinctive_tokens = _distinctive_product_tokens(normalized_name)

    exact_distinctive_matches = [
        token for token in distinctive_tokens if token in normalized_query
    ]
    if exact_distinctive_matches:
        extra_query_tokens = [
            token
            for token in query_tokens
            if token not in distinctive_tokens and token not in GENERIC_PRODUCT_TOKENS
        ]
        match_ratio = len(exact_distinctive_matches) / len(distinctive_tokens)
        if match_ratio >= 1:
            if extra_query_tokens:
                return 0.78
            return min(0.99, 0.94 + (0.02 * len(exact_distinctive_matches)))
        return max(0.85, 0.55 + (0.3 * match_ratio))

    fuzzy_token_score = 0.0
    for query_token in query_tokens:
        for name_token in distinctive_tokens:
            fuzzy_token_score = max(
                fuzzy_token_score,
                SequenceMatcher(None, query_token, name_token).ratio(),
            )

    token_score = len(query_tokens & name_tokens) / len(name_tokens) if name_tokens else 0.0
    sequence_score = SequenceMatcher(None, normalized_query, normalized_name).ratio()
    if fuzzy_token_score >= 0.65:
        return max(token_score, sequence_score, 0.45)

    return max(token_score, sequence_score)


def _candidate_with_confidence(query: str, product: Product) -> Product:
    return product.model_copy(update={"match_confidence": round(_product_text_score(query, product), 3)})


class ActivityRepository:
    def save(self, activity: ActivityRecord) -> None:
        raise NotImplementedError

    def list_by_user_id(self, user_id: int, limit: int = 20) -> List[ActivityRecord]:
        raise NotImplementedError


class InMemoryProductRepository(ProductRepository):
    def __init__(self, seed_data: Optional[Dict[str, dict]] = None):
        self.seed_data = seed_data or {
            "8801234567890": {
                "식품명": "저염 두부스낵",
                "식품코드": "F0001",
                "품목제조보고번호": "202400000001",
                "바코드": "8801234567890",
                "RAWMTRL_NM": "분리대두단백, 감자전분, 정제소금",
                "allergy": "대두",
                "에너지": "180kcal",
                "단백질": "11g",
                "지방": "7g",
                "탄수화물": "18g",
                "당류": "4g",
                "나트륨": "420mg",
                "PRDLST_DCNM": "과자",
                "image_url": "https://example.com/product.png",
            },
            "8800000000000": {
                "식품명": "고단백 두유바",
                "식품코드": "F0002",
                "품목제조보고번호": "202400000002",
                "바코드": "8800000000000",
                "RAWMTRL_NM": "분리대두단백, 설탕, 아몬드, 정제소금",
                "allergy": "대두, 견과류",
                "에너지": "320kcal",
                "단백질": "12g",
                "지방": "18g",
                "탄수화물": "31g",
                "당류": "14g",
                "나트륨": "450mg",
                "PRDLST_DCNM": "에너지바",
            },
            "8801111111111": {
                "식품명": "오리온 닥터유 단백질바 50g",
                "식품코드": "F0003",
                "RAWMTRL_NM": "초콜릿, 땅콩, 아몬드, 분리대두단백너겟",
                "allergy": "대두, 땅콩, 견과류, 우유",
                "에너지": "259kcal",
                "단백질": "12g",
                "지방": "15g",
                "탄수화물": "19g",
                "당류": "13g",
                "나트륨": "160mg",
                "PRDLST_DCNM": "에너지바",
            },
            "8802222222222": {
                "식품명": "오리온 닥터유 프로 단백질바 70g",
                "식품코드": "F0004",
                "RAWMTRL_NM": "단백질 혼합 원료, 초콜릿, 견과류",
                "allergy": "대두, 견과류, 우유",
                "에너지": "287kcal",
                "단백질": "24g",
                "지방": "7g",
                "탄수화물": "32g",
                "당류": "9g",
                "나트륨": "210mg",
                "PRDLST_DCNM": "에너지바",
            },
            "8803333333333": {
                "식품명": "농심 새우깡 90g",
                "식품코드": "F0005",
                "바코드": "8801043035989",
                "RAWMTRL_NM": "소맥분, 팜유, 전분, 새우, 새우맛베이스",
                "allergy": "밀, 새우, 대두, 우유",
                "에너지": "465kcal",
                "단백질": "6g",
                "지방": "25g",
                "탄수화물": "54g",
                "당류": "5g",
                "나트륨": "610mg",
                "PRDLST_DCNM": "과자",
            },
            "8804444444444": {
                "식품명": "농심 매운 새우깡 90g",
                "식품코드": "F0006",
                "바코드": "8801043036078",
                "RAWMTRL_NM": "소맥분, 팜유, 전분, 새우, 매운맛 시즈닝",
                "allergy": "밀, 새우, 대두, 우유",
                "에너지": "455kcal",
                "단백질": "6g",
                "지방": "23g",
                "탄수화물": "56g",
                "당류": "7g",
                "나트륨": "650mg",
                "PRDLST_DCNM": "과자",
            },
            "8805555555555": {
                "식품명": "오리온 닥터유 단백질칩 60g",
                "식품코드": "F0007",
                "RAWMTRL_NM": "단백질 원료, 곡류, 식물성유지, 시즈닝",
                "allergy": "대두, 우유",
                "에너지": "288kcal",
                "단백질": "12g",
                "지방": "12g",
                "탄수화물": "33g",
                "당류": "5g",
                "나트륨": "430mg",
                "PRDLST_DCNM": "과자",
            },
            "8806666666666": {
                "식품명": "매일 아몬드브리즈 언스위트",
                "식품코드": "F0008",
                "RAWMTRL_NM": "아몬드액, 정제수, 식염, 영양강화제, 산도조절제",
                "allergy": "견과류",
                "에너지": "35kcal",
                "단백질": "1g",
                "지방": "3g",
                "탄수화물": "1g",
                "당류": "0g",
                "나트륨": "115mg",
                "PRDLST_DCNM": "음료",
            },
            "8807777777777": {
                "식품명": "오리온 닥터유 드링크 단백질",
                "식품코드": "F0009",
                "RAWMTRL_NM": "우유, 단백질 원료, 당류, 안정제",
                "allergy": "우유",
                "에너지": "178kcal",
                "단백질": "12g",
                "지방": "2g",
                "탄수화물": "28g",
                "당류": "24g",
                "나트륨": "115mg",
                "PRDLST_DCNM": "음료",
            },
            "P00156": {
                "식품명": "매일우유 오리지널 200ml",
                "식품코드": "F0010",
                "바코드": "P00156",
                "RAWMTRL_NM": "원유 100%",
                "allergy": "우유",
                "에너지": "130kcal",
                "단백질": "6g",
                "지방": "7g",
                "탄수화물": "9g",
                "당류": "9g",
                "나트륨": "100mg",
                "PRDLST_DCNM": "우유",
            },
            "SEOUL_NA100_200": {
                "식품명": "서울우유 나100%우유 200ml",
                "식품코드": "F0011",
                "바코드": "SEOUL_NA100_200",
                "RAWMTRL_NM": "원유 100%",
                "allergy": "우유",
                "에너지": "130kcal",
                "단백질": "6g",
                "지방": "7g",
                "탄수화물": "9g",
                "당류": "9g",
                "나트륨": "100mg",
                "PRDLST_DCNM": "우유",
            },
            "NAMYANG_GT_200": {
                "식품명": "남양 맛있는 우유GT 200ml",
                "식품코드": "F0012",
                "바코드": "NAMYANG_GT_200",
                "RAWMTRL_NM": "원유 100%",
                "allergy": "우유",
                "에너지": "125kcal",
                "단백질": "6g",
                "지방": "7.2g",
                "탄수화물": "9g",
                "당류": "9g",
                "나트륨": "100mg",
                "PRDLST_DCNM": "우유",
            },
        }

    def _normalize_seed_product(self, key: str, payload: Dict[str, Any]) -> Optional[Product]:
        product = normalize_product_payload(payload)
        if product and not product.barcode:
            return product.model_copy(update={"barcode": key})
        return product

    def get_by_barcode(self, barcode: str) -> Optional[Product]:
        payload = self.seed_data.get(barcode)
        if payload is not None:
            return self._normalize_seed_product(barcode, payload)

        for key, candidate_payload in self.seed_data.items():
            product = self._normalize_seed_product(key, candidate_payload)
            if product and product.barcode == barcode:
                return product
        return None 
    def get_by_report_number(self, report_number: str) -> Optional[Product]:
        for key, payload in self.seed_data.items():
            product = self._normalize_seed_product(key, payload)
            if product and product.report_number == report_number:
                return product
        return None

    def search_by_text(self, text: str) -> Optional[Product]:
        candidates = self.search_candidates(text, limit=1)
        return candidates[0] if candidates else None

    def search_candidates(self, text: str, limit: int = 3) -> List[Product]:
        scored = []
        for key, payload in self.seed_data.items():
            product = self._normalize_seed_product(key, payload)
            if product is None:
                continue

            score = _product_text_score(text, product)
            if score >= 0.35:
                scored.append((score, product))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            product.model_copy(update={"match_confidence": round(score, 3)})
            for score, product in scored[:limit]
        ]

    def recommend_alternatives(self, product: Product, limit: int = 3) -> List[Product]:
        candidates = []
        for key, payload in self.seed_data.items():
            candidate = self._normalize_seed_product(key, payload)
            if candidate is None:
                continue
            if candidate.barcode and product.barcode and candidate.barcode == product.barcode:
                continue
            if candidate.report_number and product.report_number and candidate.report_number == product.report_number:
                continue
            if product.prdlst_dcnm and candidate.prdlst_dcnm != product.prdlst_dcnm:
                continue
            candidates.append(candidate)
        return candidates[:limit]


class InMemoryActivityRepository(ActivityRepository):
    def __init__(self):
        self.records: List[ActivityRecord] = []

    def save(self, activity: ActivityRecord) -> None:
        self.records.append(activity)

    def list_by_user_id(self, user_id: int, limit: int = 20) -> List[ActivityRecord]:
        filtered = [record for record in self.records if record.user_id == user_id]
        return filtered[-limit:][::-1]


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

        return normalize_product_payload(dict(row))

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

        return normalize_product_payload(dict(row))

    def search_by_text(self, text: str) -> Optional[Product]:
        candidates = self.search_candidates(text, limit=1)
        return candidates[0] if candidates else None

    def search_candidates(self, text: str, limit: int = 3) -> List[Product]:
        normalized_text = text.strip()
        if not normalized_text:
            return []

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            product_name_column = self._find_existing_column(
                conn,
                ["product_name", "productName", "식품명", "PRDLST_NM", "food_name"],
            )
            if product_name_column is None:
                return []

            tokens = [
                token.strip()
                for token in normalized_text.replace("?", " ").replace("!", " ").split()
                if len(token.strip()) >= 2
            ]
            if tokens:
                clauses = " OR ".join([f"{product_name_column} LIKE ?" for _ in tokens])
                params = [f"%{token}%" for token in tokens]
                rows = conn.execute(
                    f"SELECT * FROM {self.table_name} WHERE {clauses} LIMIT 50",
                    params,
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM {self.table_name} LIMIT 50",
                ).fetchall()

        best_match: Optional[Product] = None
        best_score = 0.0
        scored = []
        for row in rows:
            product = normalize_product_payload(dict(row))
            if product is None:
                continue
            score = _product_text_score(normalized_text, product)
            if score >= 0.35:
                scored.append((score, product))
            if score > best_score:
                best_match = product
                best_score = score

        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            product.model_copy(update={"match_confidence": round(score, 3)})
            for score, product in scored[:limit]
        ]

    def recommend_alternatives(self, product: Product, limit: int = 3) -> List[Product]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            category_column = self._find_existing_column(conn, ["prdlst_dcnm", "PRDLST_DCNM", "유형명"])
            barcode_column = self._find_existing_column(conn, ["barcode", "바코드", "BAR_CD"])
            report_column = self._find_existing_column(
                conn,
                ["report_number", "report_no", "품목제조보고번호", "품목보고번호", "PRDLST_REPORT_NO"],
            )

            where_parts = []
            params = []
            if category_column and product.prdlst_dcnm:
                where_parts.append(f"{category_column} = ?")
                params.append(product.prdlst_dcnm)
            if barcode_column and product.barcode:
                where_parts.append(f"({barcode_column} IS NULL OR {barcode_column} != ?)")
                params.append(product.barcode)
            if report_column and product.report_number:
                where_parts.append(f"({report_column} IS NULL OR {report_column} != ?)")
                params.append(product.report_number)

            where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
            rows = conn.execute(
                f"SELECT * FROM {self.table_name} {where_sql} LIMIT ?",
                (*params, limit),
            ).fetchall()

        return [
            candidate
            for row in rows
            if (candidate := normalize_product_payload(dict(row))) is not None
        ]


class SQLiteActivityRepository(ActivityRepository):
    def __init__(self, db_path: str, table_name: str = "activity_history"):
        self.db_path = db_path
        self.table_name = table_name
        self._ensure_table()

    def _ensure_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    barcode TEXT,
                    report_number TEXT,
                    product_id INTEGER,
                    action_type TEXT NOT NULL,
                    message TEXT,
                    intent TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def save(self, activity: ActivityRecord) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table_name}
                (user_id, barcode, report_number, product_id, action_type, message, intent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    activity.user_id,
                    activity.barcode,
                    activity.report_number,
                    activity.product_id,
                    activity.action_type,
                    activity.message,
                    activity.intent,
                ),
            )
            conn.commit()

    def list_by_user_id(self, user_id: int, limit: int = 20) -> List[ActivityRecord]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT user_id, barcode, report_number, product_id, action_type, message, intent
                FROM {self.table_name}
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

        return [ActivityRecord(**dict(row)) for row in rows]
