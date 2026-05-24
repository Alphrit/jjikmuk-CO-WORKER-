from typing import Any, Dict, List, Optional

from app.core.models import ChatHistoryItem, Product, Profile
from app.infrastructure.repository import InMemoryProductRepository, ProductRepository


_default_repository: ProductRepository = InMemoryProductRepository()


def get_product_repository() -> ProductRepository:
    return _default_repository


def set_product_repository(repository: ProductRepository) -> None:
    global _default_repository
    _default_repository = repository


def find_product_by_barcode(barcode: Optional[str], repository: Optional[ProductRepository] = None) -> Optional[Product]:
    if not barcode:
        return None

    repo = repository or get_product_repository()
    return repo.get_by_barcode(barcode)


def find_product_by_report_number(
    report_number: Optional[str],
    repository: Optional[ProductRepository] = None,
) -> Optional[Product]:
    if not report_number:
        return None

    repo = repository or get_product_repository()
    return repo.get_by_report_number(report_number)


def find_product_by_text(
    text: Optional[str],
    repository: Optional[ProductRepository] = None,
) -> Optional[Product]:
    if not text:
        return None

    repo = repository or get_product_repository()
    return repo.search_by_text(text)


def find_product_candidates_by_text(
    text: Optional[str],
    limit: int = 3,
    repository: Optional[ProductRepository] = None,
) -> List[Product]:
    if not text:
        return []

    repo = repository or get_product_repository()
    return repo.search_candidates(text, limit=limit)


def find_product_from_history(
    chat_history: Optional[List[ChatHistoryItem]],
    repository: Optional[ProductRepository] = None,
) -> Optional[Product]:
    if not chat_history:
        return None

    repo = repository or get_product_repository()
    for item in reversed(chat_history[-6:]):
        product = repo.search_by_text(item.content)
        if product is not None:
            if product.match_confidence is not None and product.match_confidence <= 0.5:
                continue
            # 후속 질문은 여러 제품이 오간 대화에서도 가장 최근에 언급된 제품만 사용한다.
            return product
    return None


def _product_has_profile_allergy(product: Product, profile: Optional[Profile]) -> bool:
    if not profile:
        return False

    product_text = " ".join([
        product.rawmtrl_nm or "",
        " ".join(product.ingredients),
        " ".join(product.resolved_allergies()),
    ]).lower()
    return any(allergy.lower() in product_text for allergy in profile.resolved_allergies())


def _matched_profile_allergies(product: Product, profile: Optional[Profile]) -> List[str]:
    if not profile:
        return []

    product_text = " ".join([
        product.rawmtrl_nm or "",
        " ".join(product.ingredients),
        " ".join(product.resolved_allergies()),
    ]).lower()
    return [
        allergy
        for allergy in profile.resolved_allergies()
        if allergy.lower() in product_text
    ]


def _recommendation_score(reference: Product, candidate: Product, profile: Optional[Profile]) -> float:
    score = 0.0
    if reference.prdlst_dcnm and candidate.prdlst_dcnm == reference.prdlst_dcnm:
        score += 2.0

    for key in ["sodium", "sugars", "fat", "saturated_fat", "kcal"]:
        reference_value = reference.nutrition.get(key)
        candidate_value = candidate.nutrition.get(key)
        if reference_value is not None and candidate_value is not None and candidate_value < reference_value:
            score += 1.0

    if _product_has_profile_allergy(candidate, profile):
        score -= 10.0

    return score


def recommend_alternative_products(
    product: Optional[Product],
    profile: Optional[Profile] = None,
    limit: int = 3,
    repository: Optional[ProductRepository] = None,
) -> List[Dict[str, Any]]:
    if product is None:
        return []

    repo = repository or get_product_repository()
    candidates = repo.recommend_alternatives(product, limit=20)
    ranked = sorted(candidates, key=lambda candidate: _recommendation_score(product, candidate, profile), reverse=True)

    recommendations = []
    excluded = []
    for candidate in ranked:
        matched_allergies = _matched_profile_allergies(candidate, profile)
        if matched_allergies:
            excluded.append({
                "product_name": candidate.product_name,
                "reason": f"{', '.join(matched_allergies[:2])} 알레르기 관련 성분이 확인되어 제외했어요.",
            })
            continue

        reasons = []
        cautions = []
        nutrition_comparison = {}
        allergies = profile.resolved_allergies() if profile else []

        if allergies:
            reasons.append(f"{', '.join(allergies[:2])} 알레르기 관련 성분이 확인되지 않음")

        for key, label in [("sodium", "나트륨"), ("sugars", "당류"), ("fat", "지방"), ("kcal", "열량")]:
            original = product.nutrition.get(key)
            replacement = candidate.nutrition.get(key)
            if original is None or replacement is None:
                continue

            delta = replacement - original
            nutrition_comparison[key] = {
                "label": label,
                "current": original,
                "candidate": replacement,
                "delta": round(delta, 2),
            }

            if replacement < original:
                reasons.append(f"{label} 수치가 더 낮음")
            elif replacement > original:
                cautions.append(f"{label} 수치는 현재 제품보다 높음")

        if product.prdlst_dcnm and candidate.prdlst_dcnm == product.prdlst_dcnm:
            reasons.append("같은 제품 유형")

        reason_text = "이며 ".join(reasons[:3]) + "입니다." if reasons else "프로필과 충돌하는 알레르기 성분이 확인되지 않습니다."
        caution_text = " ".join(cautions[:2])
        recommendations.append({
            "product_name": candidate.product_name,
            "barcode": candidate.barcode,
            "report_number": candidate.report_number,
            "score": round(_recommendation_score(product, candidate, profile), 2),
            "reason": reason_text,
            "reasons": reasons[:3] or ["프로필과 충돌하는 알레르기 성분이 확인되지 않음"],
            "caution": caution_text,
            "cautions": cautions[:3],
            "nutrition_comparison": nutrition_comparison,
        })
        if len(recommendations) >= limit:
            break

    if not recommendations:
        details = [item["reason"] for item in excluded[:3]]
        return [{
            "product_name": None,
            "barcode": None,
            "report_number": None,
            "score": 0,
            "reason": "현재 DB에서는 조건에 맞는 대체 제품을 찾지 못했어요.",
            "reasons": ["추천 후보 부족"],
            "caution": "제품 DB가 더 연결되면 같은 유형에서 알레르기와 영양성분을 비교해 다시 추천할 수 있어요.",
            "cautions": details,
            "nutrition_comparison": {},
        }]

    return recommendations
