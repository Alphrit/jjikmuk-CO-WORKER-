import logging
import re
from typing import Any, Dict, List, Literal, Optional, Sequence

from app.core.models import ChatHistoryItem, Product, Profile
from app.core.public_product import public_product_label_trust, public_product_payload
from app.core.product_provenance import label_provenance_supports
from app.data.dictionaries import (
    SPECIAL_DIET_KEYWORDS,
    SPECIAL_DIET_RESTRICTIONS,
)
from app.infrastructure.repository import InMemoryProductRepository, ProductRepository
from app.services.product_search import (
    GENERIC_PRODUCT_TOKENS,
    compact_search_text,
    extract_product_search_query,
    filter_product_candidates_for_query,
    tokenize_search_text,
)
from app.services.response_helpers import (
    canonical_special_diet_name,
    evaluate_special_diets,
    find_cross_contamination_aliases,
    find_matching_aliases,
    scan_disliked_ingredients,
)


_default_repository: ProductRepository = InMemoryProductRepository()
logger = logging.getLogger(__name__)


def _product_with_valid_public_contract(
    product: Optional[Product],
    *,
    view: Literal["detail", "candidate"] = "detail",
) -> Optional[Product]:
    """Fail one malformed catalog row closed without exposing its contents."""

    if product is None:
        return None
    try:
        public_product_payload(product, view=view)
    except (TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "Repository product rejected by bounded public contract: %s",
            type(exc).__name__,
        )
        return None
    return product


def get_product_repository() -> ProductRepository:
    return _default_repository


def set_product_repository(repository: ProductRepository) -> None:
    global _default_repository
    _default_repository = repository


def find_product_by_barcode(barcode: Optional[str], repository: Optional[ProductRepository] = None) -> Optional[Product]:
    if not barcode:
        return None

    repo = repository or get_product_repository()
    return _product_with_valid_public_contract(repo.get_by_barcode(barcode))


def find_product_by_report_number(
    report_number: Optional[str],
    repository: Optional[ProductRepository] = None,
) -> Optional[Product]:
    if not report_number:
        return None

    repo = repository or get_product_repository()
    return _product_with_valid_public_contract(
        repo.get_by_report_number(report_number)
    )


def find_product_by_product_id(
    product_id: Optional[int],
    repository: Optional[ProductRepository] = None,
) -> Optional[Product]:
    if product_id is None or isinstance(product_id, bool) or product_id < 0:
        return None

    repo = repository or get_product_repository()
    return _product_with_valid_public_contract(repo.get_by_product_id(product_id))


def find_product_by_text(
    text: Optional[str],
    repository: Optional[ProductRepository] = None,
) -> Optional[Product]:
    if not text:
        return None

    repo = repository or get_product_repository()
    return _product_with_valid_public_contract(repo.search_by_text(text))


def find_product_candidates_by_text(
    text: Optional[str],
    limit: int = 3,
    repository: Optional[ProductRepository] = None,
) -> List[Product]:
    if not text:
        return []

    repo = repository or get_product_repository()
    return [
        product
        for product in repo.search_candidates(text, limit=limit)
        if _product_with_valid_public_contract(product, view="candidate") is not None
    ][:limit]


def find_product_candidates_from_message(
    message: Optional[str],
    limit: int = 3,
    repository: Optional[ProductRepository] = None,
) -> List[Product]:
    if not message:
        return []

    search_query = extract_product_search_query(message)
    if not search_query:
        return []

    repo = repository or get_product_repository()
    fetch_limit = max(limit * 4, 12)
    candidates = [
        product
        for product in repo.search_candidates(search_query, limit=fetch_limit)
        if _product_with_valid_public_contract(product, view="candidate") is not None
    ]
    filtered = filter_product_candidates_for_query(search_query, candidates)[:limit]
    if filtered:
        return filtered
    from app.services.product_search import recipe_query_without_instrumental_particle
    fallback_query = recipe_query_without_instrumental_particle(message, search_query)
    if not fallback_query:
        return []
    candidates = [
        product
        for product in repo.search_candidates(fallback_query, limit=fetch_limit)
        if _product_with_valid_public_contract(product, view="candidate") is not None
    ]
    return filter_product_candidates_for_query(fallback_query, candidates)[:limit]


def _history_product_queries(text: str) -> List[str]:
    cleaned_text = re.sub(r"^\s*(그럼|그러면|근데|그리고|그건|그거|이건|이거)\s+", "", text).strip()
    queries = [cleaned_text or text]
    for pattern in [
        r"(알레르기|알러지|원재료|성분|영양|나트륨|당류|칼로리|열량|들어|포함|확인)",
        r"(다른|추가|관련|있는지|없는지)",
        r"(먹어도|괜찮|어때|설명)",
    ]:
        head = re.split(pattern, text, maxsplit=1)[0].strip()
        if len(head) >= 2:
            queries.append(head)
    unique = []
    for query in queries:
        query = re.sub(r"^\s*(그럼|그러면|근데|그리고|그건|그거|이건|이거)\s+", "", query).strip()
        if query in {"그럼", "그러면", "근데", "그리고", "그건", "그거", "이건", "이거"}:
            continue
        if query not in unique:
            unique.append(query)
    return unique


def find_product_from_history(
    chat_history: Optional[List[ChatHistoryItem]],
    repository: Optional[ProductRepository] = None,
) -> Optional[Product]:
    if not chat_history:
        return None

    repo = repository or get_product_repository()
    # The request's history is client supplied. Assistant-role text cannot be
    # authenticated without a server-owned conversation, so never let it select
    # or restore a product. User mentions remain search hints and still resolve
    # through the authoritative repository before any facts are consumed.
    recent_user_items = [
        item
        for item in reversed(chat_history[-6:])
        if item.role == "user"
    ]
    for item in recent_user_items:
        for query in _history_product_queries(item.content):
            product = _product_with_valid_public_contract(
                repo.search_by_text(query)
            )
            if product is not None:
                if product.match_confidence is not None and product.match_confidence <= 0.5:
                    continue
                # 후속 질문은 여러 제품이 오간 대화에서도 가장 최근에 언급된 제품만 사용한다.
                return product
    return None


def _product_has_profile_allergy(product: Product, profile: Optional[Profile]) -> bool:
    return bool(_matched_profile_allergies(product, profile))


def _matched_profile_allergies(product: Product, profile: Optional[Profile]) -> List[str]:
    if not profile:
        return []

    product_with_name_evidence = product.model_copy(update={
        "ingredients": [
            *product.ingredients,
            *[
                name
                for name in [product.product_name, product.clean_product_name]
                if name
            ],
        ],
    })
    matched = []
    for allergy in profile.resolved_allergies():
        if (
            find_matching_aliases(product_with_name_evidence, allergy)
            or find_cross_contamination_aliases(product_with_name_evidence, allergy)
        ):
            matched.append(allergy)
    return matched


def _matched_profile_dislikes(
    product: Product,
    profile: Optional[Profile],
) -> List[str]:
    if not profile:
        return []

    matched = scan_disliked_ingredients(profile, product)
    name_evidence = " ".join(filter(None, [
        product.product_name,
        product.clean_product_name,
    ])).lower()
    for dislike in profile.disliked_ingredients:
        for term in re.split(r"\s*(?:,|/|및|그리고)\s*", dislike):
            normalized = term.strip().lower()
            if normalized and normalized in name_evidence:
                matched.append(term.strip())
    return list(dict.fromkeys(matched))


def recommend_alternative_products(
    product: Optional[Product],
    profile: Optional[Profile] = None,
    limit: int = 3,
    repository: Optional[ProductRepository] = None,
) -> List[Dict[str, Any]]:
    """Compatibility wrapper around the single allergy-first recommender."""

    if product is None or not product.facts_verified or not product.product_found:
        return []
    profiles = [profile] if profile is not None else []
    return recommend_catalog_products(
        "",
        profiles,
        source_product=product,
        limit=limit,
        repository=repository,
    )



_GENERAL_RECOMMENDATION_SEARCH_TERMS = (
    ("과자", "과자"),
    ("스낵", "과자"),
    ("간식", "과자"),
    ("단백질바", "단백질바"),
    ("빵", "빵"),
    ("음료", "음료"),
    ("우유", "우유"),
    ("두유", "두유"),
    ("면", "면류"),
)

_DIET_RECOMMENDATION_SEARCH_TERMS = {
    "채식": "채식",
    "저염식": "저염",
    "저당식": "저당",
    "글루텐프리": "글루텐프리",
    "저칼로리": "저칼로리",
    "저지방": "저지방",
    "고단백": "고단백",
}


def _recommendation_search_term(
    message: str,
    source_product: Optional[Product],
    profiles: Sequence[Profile],
) -> str:
    if source_product is not None:
        return source_product.prdlst_dcnm or (
            source_product.clean_product_name or source_product.product_name or ""
        )
    normalized = message.replace(" ", "").lower()
    requested_diets = list(dict.fromkeys([
        *(
            canonical_special_diet_name(diet) or diet
            for profile in profiles
            for diet in profile.resolved_special_diets()
        ),
        *(
            canonical
            for canonical, aliases in SPECIAL_DIET_KEYWORDS.items()
            if any(alias.replace(" ", "").lower() in normalized for alias in aliases)
        ),
    ]))
    diet_terms = [
        _DIET_RECOMMENDATION_SEARCH_TERMS[diet]
        for diet in requested_diets
        if diet in _DIET_RECOMMENDATION_SEARCH_TERMS
    ]
    for token, search_term in _GENERAL_RECOMMENDATION_SEARCH_TERMS:
        if token in normalized:
            return " ".join(dict.fromkeys([*diet_terms, search_term]))
    return " ".join(dict.fromkeys(diet_terms))


def _same_catalog_product(left: Product, right: Product) -> bool:
    return bool(
        (left.barcode and right.barcode and left.barcode == right.barcode)
        or (
            left.report_number
            and right.report_number
            and left.report_number == right.report_number
        )
        or (
            left.product_name
            and right.product_name
            and left.product_name == right.product_name
        )
    )


def recommend_catalog_product_records(
    message: str,
    profiles: Sequence[Profile],
    *,
    source_product: Optional[Product] = None,
    limit: int = 3,
    repository: Optional[ProductRepository] = None,
) -> List[Product]:
    """Find DB products after applying every active profile's allergen filter.

    This intentionally does not invent nutrition cutoffs. Dietary claims that
    require certification or a package claim are handled separately from this
    catalog/category search.
    """

    search_term = _recommendation_search_term(message, source_product, profiles)
    if not search_term:
        return []
    repo = repository or get_product_repository()
    candidates = (
        repo.recommend_alternatives(
            source_product,
            limit=max(limit * 20, 60),
        )
        if source_product is not None
        else repo.search_candidates(search_term, limit=max(limit * 20, 60))
    )
    accepted: List[Product] = []
    seen: set[tuple[Optional[str], Optional[str], Optional[str]]] = set()
    for candidate in candidates:
        if (
            not candidate.facts_verified
            or candidate.product_found is False
            or _product_with_valid_public_contract(candidate, view="candidate") is None
        ):
            continue
        if source_product is not None and _same_catalog_product(source_product, candidate):
            continue
        if profiles and any(
            not profile.allergy_context_provided
            for profile in profiles
        ):
            # An omitted allergy list is different from a confirmed empty list.
            return []
        if profiles and any(
            _matched_profile_allergies(candidate, profile)
            for profile in profiles
        ):
            continue
        if any(profile.resolved_allergies() for profile in profiles) and not (
            label_provenance_supports(candidate, ("ingredients", "allergens"))
        ):
            # "No match found" is only useful when the exact candidate label
            # behind that absence is traceable.  An untraced row must never be
            # promoted into a positive alternative for an allergic user.
            continue
        if any(
            _matched_profile_dislikes(candidate, profile)
            for profile in profiles
        ):
            continue
        diet_results = [
            evaluate_special_diets(profile, candidate, message)
            for profile in profiles
        ] or [evaluate_special_diets(None, candidate, message)]
        if any(
            conflicts or cautions or limitations
            for conflicts, cautions, limitations in diet_results
        ):
            # A recommendation is a positive selection. Missing a required
            # package claim (for example low-sodium or gluten-free) therefore
            # excludes the candidate instead of merely attaching a caveat.
            continue
        if any(profile.resolved_allergies() for profile in profiles) and not (
            candidate.rawmtrl_nm or candidate.allergy or candidate.allergy_warning
        ):
            continue
        identity = (
            candidate.barcode,
            candidate.report_number,
            candidate.product_name,
        )
        if identity in seen:
            continue
        seen.add(identity)
        accepted.append(candidate)
        if len(accepted) >= limit:
            break
    return accepted


def recommend_catalog_products(
    message: str,
    profiles: Sequence[Profile],
    *,
    source_product: Optional[Product] = None,
    limit: int = 3,
    repository: Optional[ProductRepository] = None,
) -> List[Dict[str, Any]]:
    records = recommend_catalog_product_records(
        message,
        profiles,
        source_product=source_product,
        limit=limit,
        repository=repository,
    )
    checked_names = [profile.profile_name for profile in profiles]
    checked_diets = list(dict.fromkeys(
        diet
        for profile in profiles
        for diet in profile.resolved_special_diets()
    ))
    checked_dislikes = list(dict.fromkeys(
        ingredient
        for profile in profiles
        for ingredient in profile.disliked_ingredients
    ))
    return [
        {
            "product_name": candidate.product_name,
            "barcode": candidate.barcode,
            "report_number": candidate.report_number,
            "reason": (
                "선택된 프로필의 알레르기·비선호 성분과 DB 표시를 "
                "대조해 충돌 후보를 제외했어요."
            ),
            "reasons": [
                *(
                    [f"적용 프로필: {', '.join(checked_names)}"]
                    if checked_names
                    else ["프로필 조건 없음"]
                ),
                "최신 포장지의 원재료·알레르기 표시 재확인 필요",
            ],
            "caution": "추천 제품도 섭취 전에 실제 포장지 표시를 다시 확인해 주세요.",
            "cautions": [
                "DB에 없는 교차접촉·제조 변경은 반영되지 않을 수 있어요."
            ],
            "nutrition_comparison": {},
            "recommendation_basis": {
                "profile_allergy_checked": bool(checked_names) and all(
                    profile.allergy_context_provided for profile in profiles
                ),
                "allergy_absence_supported": label_provenance_supports(
                    candidate,
                    ("ingredients", "allergens"),
                ),
                "profile_special_diets": checked_diets,
                "profile_special_diets_checked": bool(checked_diets),
                "profile_disliked_ingredients": checked_dislikes,
                "profile_disliked_ingredients_checked": bool(checked_dislikes),
                "comparison_basis": ["알레르기·식이·비선호 필터"],
                "nutrition_basis_comparable": False,
                "current_nutrition_basis": "영양 수치 비교 안 함",
                "improved_nutrition_count": 0,
                "applied_rule_ids": [],
                "recommendation_available": True,
                "candidate_label_trust": public_product_label_trust(
                    candidate
                ).model_dump(mode="json"),
            },
        }
        for candidate in records
    ]
