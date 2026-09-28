"""product storage / memory repository implementation."""

from app.api.adapters import normalize_product_payload
from app.core.models import Product
from app.data.dictionaries import alternative_categories_for
from typing import Any, Dict, List, Optional

from app.infrastructure.product_storage.common import (
    BARCODE_COLUMNS,
    ProductRepository,
    _alternative_similarity,
    _dedupe_scored_products,
    _product_text_score,
    _same_product_identity,
)


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
                "id": 101,
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
        normalized_payload = dict(payload)
        if not any(normalized_payload.get(column) for column in BARCODE_COLUMNS):
            # Add repository identity before Product computes its deterministic
            # content revision. model_copy would leave the revision stale.
            normalized_payload["barcode"] = key
        return normalize_product_payload(normalized_payload, repository_record=True)

    def readiness_check(self) -> Dict[str, Any]:
        return {"available": True, "provider": "memory"}

    def get_by_product_id(self, product_id: int) -> Optional[Product]:
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id < 0:
            return None
        for key, payload in self.seed_data.items():
            product = self._normalize_seed_product(key, payload)
            if product and product.product_id == product_id:
                return product
        return None

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

        scored = _dedupe_scored_products(scored)
        return [
            product.model_copy(update={"match_confidence": round(score, 3)})
            for score, product in scored[:limit]
        ]

    def recommend_alternatives(self, product: Product, limit: int = 3) -> List[Product]:
        alternative_categories = alternative_categories_for(product.prdlst_dcnm)
        scored = []
        for key, payload in self.seed_data.items():
            candidate = self._normalize_seed_product(key, payload)
            if candidate is None:
                continue
            if _same_product_identity(product, candidate):
                continue
            similarity = _alternative_similarity(product, candidate)
            category_match = bool(
                product.prdlst_dcnm
                and (
                    candidate.prdlst_dcnm == product.prdlst_dcnm
                    or candidate.prdlst_dcnm in alternative_categories
                )
            )
            if similarity < 0.25 and not category_match:
                continue
            scored.append((similarity + (0.15 if category_match else 0), candidate))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            candidate.model_copy(update={"match_confidence": round(score, 3)})
            for score, candidate in scored[:limit]
        ]
