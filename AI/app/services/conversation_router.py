import re
from typing import Optional

from app.core.models import ChatResponse, Product, Profile
from app.core.public_product import internal_product_reference, public_product_payload, public_conversation_state
from app.data.dictionaries import (
    EATABILITY_KEYWORDS,
    INCLUSION_KEYWORDS,
    SPECIAL_DIET_KEYWORDS,
)
from app.intent.common import FOOD_DOMAIN_KEYWORDS, OUT_OF_DOMAIN_KEYWORDS, PRODUCT_INTENTS
from app.intent.rules import is_nutrition_word_part_of_product_name, rule_based_classify_intent
from app.intent.text import (
    contains_any,
    detect_ingredient_keyword,
    detect_nutrition_keyword,
    is_context_dependent_followup,
    normalize_message,
)
from app.services.product import (
    find_product_by_barcode,
    find_product_by_product_id,
    find_product_by_report_number,
    find_product_candidates_from_message,
    find_product_from_history,
)
from app.services.product_search import (
    GENERIC_PRODUCT_TOKENS,
    PRODUCT_REFERENCE_ONLY_TOKENS,
    PRODUCT_CONFIRMATION_THRESHOLD,
    compact_search_text,
    dedupe_products_by_display_name,
    extract_product_search_query,
    explicitly_mentions_product,
    mentions_distinct_product_token,
    product_match_score,
    same_product,
    same_display_product_group,
    tokenize_search_text,
    recipe_source_query,
)
from app.services.candidate_prompts import (
    CHOOSE_FROM_CANDIDATES, NEW_PRODUCT_NOT_FOUND, NONE_SELECTED_YET, NOT_CONFIRMED_YET, SELECTION_CONFIRMED,
    confirms_choice, leaves_choice_open,
)
from app.services.comparison_language import comparison_pick_parts
from app.services.request_language import refers_only_to_current_product

GENERAL_TOPIC_SHIFT_KEYWORDS = [
    "고를 때",
    "골라",
    "고르는",
    "기준",
    "뭘 봐",
    "뭐 봐",
    "어떻게",
    "추천",
    "일반적으로",
    "간식",
    "식단",
    "가이드",
]

PRODUCT_REFERENCE_KEYWORDS = [
    "이 제품",
    "그 제품",
    "현재 제품",
    "방금 제품",
    "스캔",
    "바코드",
    "품목보고",
    "이거",
    "이건",
    "그거",
    "그건",
]

PRODUCT_DETAIL_FOLLOWUP_KEYWORDS = [
    "어떤 성분",
    "어떤 수치",
    "무슨 성분",
    "무슨 수치",
    "왜 조심",
    "왜 주의",
    "때문에 조심",
    "때문에 주의",
    "자세히 알려",
    "자세히 설명",
    "더 자세히",
    "이유",
]


def _is_diet_only_recipe_query(message: str) -> bool:
    query = compact_search_text(extract_product_search_query(message))
    if not query:
        return False
    diet_terms = {
        compact_search_text(term)
        for canonical, aliases in SPECIAL_DIET_KEYWORDS.items()
        for term in (canonical, *aliases)
    }
    return query in diet_terms

GENERAL_PERSON_CONDITION_PATTERNS = [
    "알레르기가 있는 사람",
    "알러지가 있는 사람",
    "알레르기 있는 사람",
    "알러지 있는 사람",
    "있는 사람",
    "환자",
]

GENERAL_GUIDANCE_PATTERNS = [
    "당뇨",
    "혈당",
    "고혈압",
    "나트륨",
    "저염",
    "저당",
    "저칼로리",
    "저지방",
    "영양성분",
    "영양 성분",
    "당류",
    "탄수화물",
    "열량",
    "칼로리",
    "지방",
]

GENERAL_GUIDANCE_QUESTION_CUES = [
    "어떻게",
    "어떤",
    "무엇",
    "뭐",
    "먼저",
    "확인",
    "봐야",
    "보면",
    "고를 때",
    "기준",
    "관리",
    "주의",
    "가이드",
    "알려",
    "설명",
]

PRODUCT_SEARCH_REQUEST_KEYWORDS = [
    "찾아줘",
    "찾아 줘",
    "찾아",
    "검색",
    "검색해줘",
    "검색해 줘",
    "제품 찾아",
    "제품 검색",
]


def _product_state_reference(product: Product) -> dict:
    return {
        key: value
        for key, value in {
            "product_name": product.product_name,
            "barcode": product.barcode,
            "report_number": product.report_number,
        }.items()
        if value is not None
    }


def _has_authoritative_product_reference(product: Product) -> bool:
    # Stateless client state may carry only public label identifiers. A raw DB
    # surrogate ID is neither a display field nor a safe public selection token.
    return bool(product.barcode or product.report_number)


def build_product_confirmation_response(
    products: list[Product],
    search_query: str = "",
    reason: str = "low_confidence",
    original_message: str = "",
    pending_intent: str = "",
) -> ChatResponse:
    products = dedupe_products_by_display_name([
        product
        for product in products
        if _has_authoritative_product_reference(product)
    ])
    if not products:
        return ChatResponse(
            task_type="product_chat",
            answer_source="rules",
            pipeline_stage="rules",
            intent="missing_product",
            answer=(
                "제품명과 비슷한 후보는 찾았지만 다시 확인할 수 있는 제품 식별자가 없어요. "
                "바코드를 스캔하거나 제품명을 더 정확히 입력해 주세요."
            ),
            risk_level="unknown",
            assessment_status="incomplete",
            reasons=["제품 후보 식별자 부족"],
            conversation_state={
                "version": 1,
                "last_intent": "missing_product",
                "last_task_type": "product_chat",
            },
        )
    primary = products[0]
    name = primary.product_name or "이 제품"
    multiple_matches = reason == "multiple_matches"
    candidate_reason = (
        "같은 브랜드나 제품군에 여러 제품이 있어 세부 제품 확인이 필요해요."
        if multiple_matches
        else "입력한 문장과 제품명이 일부 비슷하지만 확정하기에는 유사도가 낮아요."
    )
    candidate_reasons = (
        ["제품명 일치", "세부 제품 확인 필요"]
        if multiple_matches
        else ["제품명 일부 일치", "확인 필요"]
    )
    product_candidates = [
        public_product_payload(product, view="candidate")
        | {
            "reason": candidate_reason,
            "reasons": candidate_reasons,
            "selection_type": "product_candidate",
        }
        for product in products[:3]
    ]
    if multiple_matches:
        requested_name = search_query or name
        answer = (
            f"질문하신 '{requested_name}'에 해당하는 제품이 여러 개예요. "
            f"{CHOOSE_FROM_CANDIDATES}."
        )
    else:
        answer = f"질문하신 제품이 '{name}'인지 확실하지 않아요. {CHOOSE_FROM_CANDIDATES}."
    pending_context = {
        **(
            {"pending_user_message": original_message.strip()}
            if original_message.strip()
            else {}
        ),
        **({"pending_intent": pending_intent} if pending_intent else {}),
    }
    return ChatResponse(
        task_type="product_chat",
        answer_source="rules",
        pipeline_stage="rules",
        intent="unknown",
        answer=answer,
        risk_level="unknown",
        reasons=["제품명 확인 필요"],
        assessment_status="incomplete",
        product_candidates=product_candidates,
        recommended_products=product_candidates,
        conversation_state={
            "version": 1,
            "last_intent": "product_confirmation",
            "last_task_type": "product_chat",
            "pending_product_candidates": [
                _product_state_reference(product)
                for product in products[:3]
            ],
            **pending_context,
        },
        internal_conversation_state={
            "version": 1,
            "last_intent": "product_confirmation",
            "last_task_type": "product_chat",
            "pending_product_candidates": [
                internal_product_reference(product)
                for product in products[:3]
            ],
            **pending_context,
        },
    )


def has_ambiguous_product_candidates(candidates: list[Product]) -> bool:
    candidates = dedupe_products_by_display_name(candidates)
    if len(candidates) < 2:
        return False

    if same_display_product_group(candidates[:3]):
        return False

    top_score = candidates[0].match_confidence or 0
    second_score = candidates[1].match_confidence or 0
    if top_score < PRODUCT_CONFIRMATION_THRESHOLD:
        return True
    return second_score >= 0.75 and (top_score - second_score) <= 0.15


def is_broad_brand_query(search_query: str, candidates: list[Product]) -> bool:
    query_tokens = tokenize_search_text(search_query)
    if len(query_tokens) != 1 or query_tokens[0] not in GENERIC_PRODUCT_TOKENS:
        return False

    related_candidates = [
        candidate
        for candidate in candidates
        if compact_search_text(search_query) in compact_search_text(
            " ".join(filter(None, [
                candidate.product_name,
                candidate.clean_product_name,
                candidate.manufacturer_name,
            ]))
        )
    ]
    return len(related_candidates) >= 2


def is_shared_product_family_query(search_query: str, candidates: list[Product]) -> bool:
    query_key = compact_search_text(search_query)
    if len(query_key) < 2:
        return False

    matching_names = {
        compact_search_text(candidate.clean_product_name or candidate.product_name or "")
        for candidate in candidates
        if query_key in compact_search_text(candidate.clean_product_name or candidate.product_name or "")
    }
    return len(matching_names) >= 2


def build_confirmation_if_needed(
    candidates: list[Product],
    search_query: str = "",
    original_message: str = "",
    pending_intent: str = "",
) -> Optional[ChatResponse]:
    candidates = dedupe_products_by_display_name(candidates)
    if not candidates:
        return None

    top_score = candidates[0].match_confidence or 0
    broad_brand_query = is_broad_brand_query(search_query, candidates)
    shared_family_query = is_shared_product_family_query(search_query, candidates)
    ambiguous_candidates = has_ambiguous_product_candidates(candidates)
    if (
        top_score < PRODUCT_CONFIRMATION_THRESHOLD
        or ambiguous_candidates
        or broad_brand_query
        or shared_family_query
    ):
        reason = (
            "multiple_matches"
            if broad_brand_query
            or shared_family_query
            or (ambiguous_candidates and top_score >= PRODUCT_CONFIRMATION_THRESHOLD)
            else "low_confidence"
        )
        return build_product_confirmation_response(
            candidates,
            search_query=search_query,
            reason=reason,
            original_message=original_message,
            pending_intent=pending_intent,
        )

    return None


def looks_like_same_named_product_variants(message: str, candidates: list[Product]) -> bool:
    if not candidates:
        return False

    primary_name = candidates[0].clean_product_name or candidates[0].product_name or ""
    primary_key = compact_search_text(primary_name)
    message_key = compact_search_text(message)
    if len(primary_key) < 8 or primary_key not in message_key:
        return False

    for candidate in candidates[:3]:
        candidate_name = candidate.clean_product_name or candidate.product_name or ""
        candidate_key = compact_search_text(candidate_name)
        if not candidate_key:
            return False
        if primary_key not in candidate_key and candidate_key not in primary_key:
            return False

    return True


def find_product_from_request_text(
    message: str,
    chat_history,
    use_history: bool = True,
) -> tuple[Optional[Product], Optional[ChatResponse]]:
    text = normalize_message(message)
    from app.services.general_handlers import _message_product_segments
    if (rule_based_classify_intent(message=text, product=None, profile=None, chat_history=[]) == "food_compare"
            and _message_product_segments(message)):
        # '새우깡이랑 쌀새우깡 비교해줘' (or '진라면이랑 비교해줘' beside the
        # selected product): the comparison handler resolves each target.
        # Searching the phrase as one product offered a single-product list.
        return None, None
    history_restore_allowed = use_history and not _has_unresolved_product_confirmation(
        chat_history
    )
    product_detail_followup = (
        chat_history
        and contains_any(text, ["알레르기", "알러지", "원재료", "성분", "영양", "나트륨", "당류", "칼로리", "열량"])
        and contains_any(text, ["다른", "더", "또", "없어", "있어", "같이", "추가", "이거", "이건", "그거", "그건", "그럼", "그러면"])
        and not has_general_topic_shift_cue(text)
    )
    if history_restore_allowed and (
        is_context_dependent_followup(message, chat_history)
        or product_detail_followup
    ):
        history_product = find_product_from_history(chat_history)
        if history_product is not None:
            return history_product, None
    reoffer = (_reoffer_history_candidates(message, chat_history)
               if use_history and not history_restore_allowed else None)
    if reoffer is not None:
        return None, reoffer

    if not should_search_products_from_message(message):
        if history_restore_allowed:
            return find_product_from_history(chat_history), None
        return None, None

    search_query = extract_product_search_query(message)
    candidates = find_product_candidates_from_message(message, limit=3)
    request_intent_without_product = rule_based_classify_intent(
        message=text,
        product=None,
        profile=None,
        chat_history=[],
    )
    if (
        request_intent_without_product == "recipe_recommend"
        and candidates
        and _is_diet_only_recipe_query(message)
    ):
        # A dietary adjective can also occur in a catalog product name.  For
        # example, "고단백 레시피" used to select "고단백 두유바"
        # even though the user never named that product.  Recipe requests stay
        # productless unless an actual candidate name is explicitly present.
        return None, None
    if (
        request_intent_without_product == "missing_product"
        and candidates
        and not explicitly_mentions_product(message, candidates[0])
        and not mentions_distinct_product_token(message, candidates[0])
        and (
            detect_ingredient_keyword(text) is not None
            or (
                detect_nutrition_keyword(text) is not None
                and not is_nutrition_word_part_of_product_name(text, detect_nutrition_keyword(text) or "")
            )
            or contains_any(
                text,
                [
                    "이 성분",
                    "그 성분",
                    "이거 성분",
                    "이건 성분",
                    "이 제품 성분",
                    "그 제품 성분",
                    "현재 제품 성분",
                    "성분 있어",
                    "성분 있",
                    "성분 들어",
                    "원재료 뭐",
                    "원재료 알려",
                    "성분표",
                    "알레르기 성분",
                    "알러지 성분",
                ],
            )
        )
    ):
        return None, None
    if (
        candidates
        and request_intent_without_product != "recipe_recommend"
        and looks_like_same_named_product_variants(message, candidates)
    ):
        return candidates[0], None
    if (
        candidates
        and (
            request_intent_without_product == "alternative_food_recommend"
            or contains_any(text, ["대체", "대체할", "대신", "대안", "비슷한 제품", "다른 거"])
        )
        and (candidates[0].match_confidence or 0) >= 0.75
        and explicitly_mentions_product(message, candidates[0])
    ):
        return candidates[0], None
    pending_intent = (
        rule_based_classify_intent(
            message=message,
            product=candidates[0],
            profile=None,
            chat_history=[],
        )
        if candidates
        else ""
    )
    confirmation_response = build_confirmation_if_needed(
        candidates,
        search_query=search_query,
        original_message=message,
        pending_intent=pending_intent,
    )
    if confirmation_response is not None:
        return None, confirmation_response
    if candidates and (candidates[0].match_confidence or 0) >= 0.99 and explicitly_mentions_product(message, candidates[0]):
        return candidates[0], None
    if candidates:
        return candidates[0], None

    if not history_restore_allowed:
        return None, None

    return find_product_from_history(chat_history), None


# A recheck needs the saved selection, so it never reopens a candidate list.
_PRODUCT_FOLLOWUP_INTENTS = (PRODUCT_INTENTS - {"profile_based_recheck"}) | {"missing_product"}


def _reoffer_history_candidates(message: str, chat_history) -> Optional[ChatResponse]:
    """Ask again instead of guessing after an unresolved candidate list.

    The app resends the original question with the chosen barcode, so the
    choice never appears as user text. A later productless follow-up gets the
    same candidates again, with this follow-up pending.
    """
    if not chat_history or _explicit_named_product_query(message, None):
        return None
    if rule_based_classify_intent(message=message, product=None, profile=None, chat_history=[]) not in _PRODUCT_FOLLOWUP_INTENTS:
        return None
    for item in reversed(chat_history[-6:]):
        if getattr(item, "role", None) != "user":
            continue
        candidates = find_product_candidates_from_message(item.content, limit=3)
        if not candidates:
            continue
        return build_confirmation_if_needed(
            candidates,
            search_query=extract_product_search_query(item.content),
            original_message=message,
            pending_intent=rule_based_classify_intent(message=message, product=candidates[0], profile=None, chat_history=[]),
        )
    return None


def _has_unresolved_product_confirmation(chat_history) -> bool:
    """Use assistant text only as a fail-closed barrier to implicit selection.

    Client-supplied assistant history is not trusted to establish a product, but
    allowing it to suppress an implicit history lookup cannot create product
    facts. Saved-room history is server owned and benefits from the same guard.
    """
    unresolved = False
    for item in chat_history or []:
        if getattr(item, "role", None) != "assistant":
            continue
        content = normalize_message(getattr(item, "content", ""))
        if leaves_choice_open(content):
            unresolved = True
        elif confirms_choice(content):
            unresolved = False
    return unresolved


def should_search_products_from_message(message: str) -> bool:
    text = normalize_message(message)
    if not text:
        return False

    if contains_any(text, OUT_OF_DOMAIN_KEYWORDS) and not contains_any(text, FOOD_DOMAIN_KEYWORDS):
        return False

    if (
        contains_any(text, GENERAL_GUIDANCE_PATTERNS)
        and contains_any(text, GENERAL_GUIDANCE_QUESTION_CUES)
        and not contains_any(text, PRODUCT_REFERENCE_KEYWORDS)
    ):
        candidates = find_product_candidates_from_message(text, limit=1)
        if (
            candidates
            and (candidates[0].match_confidence or 0) >= 0.85
            and explicitly_mentions_product(message, candidates[0])
        ):
            return True
        return False

    if contains_any(text, GENERAL_PERSON_CONDITION_PATTERNS):
        return False

    if (
        contains_any(text, ["알레르기", "알러지", "질환", "당뇨", "고혈압"])
        and contains_any(text, ["사람", "환자", "경우", "있으면"])
        and not contains_any(text, PRODUCT_REFERENCE_KEYWORDS)
    ):
        return False

    return True


def find_explicit_product_override(message: str, current_product: Optional[Product]) -> tuple[Optional[Product], Optional[ChatResponse]]:
    if current_product is None:
        return None, None

    text = normalize_message(message)
    if rule_based_classify_intent(message=text, product=None, profile=None, chat_history=[]) == "food_compare":
        # '진라면이랑 비교해줘' adds a comparison target; it does not replace
        # the selected product (the comparison handler resolves both).
        return None, None
    if contains_any(text, ["이 제품", "그 제품", "현재 제품", "방금 제품", "이거", "이건", "그거", "그건"]) and contains_any(
        text,
        ["대체 제품", "대체품", "대신", "비슷한 제품", "더 나은", "나은 것도"],
    ):
        return None, None

    if not should_search_products_from_message(message):
        return None, None

    search_query = extract_product_search_query(message)
    candidates = find_product_candidates_from_message(message, limit=3)
    if not candidates:
        return None, None

    current_product_is_candidate = any(same_product(current_product, candidate) for candidate in candidates)
    top_candidate = candidates[0]
    top_score = top_candidate.match_confidence or 0
    if (
        not explicitly_mentions_product(message, top_candidate)
        and not mentions_distinct_product_token(message, top_candidate)
        and (
            is_attribute_followup_for_current_product(message)
            or contains_any(text, EATABILITY_KEYWORDS)
            or contains_any(text, PRODUCT_REFERENCE_KEYWORDS)
        )
    ):
        return None, None
    if top_score >= 0.99 and explicitly_mentions_product(message, top_candidate):
        return top_candidate, None
    if is_attribute_followup_for_current_product(message) and not explicitly_mentions_product(message, top_candidate):
        return None, None

    if current_product_is_candidate:
        current_score = product_match_score(current_product, candidates)
        if (
            not same_product(current_product, top_candidate)
            and explicitly_mentions_product(message, top_candidate)
            and top_score >= PRODUCT_CONFIRMATION_THRESHOLD
            and top_score - current_score >= 0.05
        ):
            return top_candidate, None
        return None, None

    pending_intent = rule_based_classify_intent(
        message=message,
        product=candidates[0],
        profile=None,
        chat_history=[],
    )
    confirmation_response = build_confirmation_if_needed(
        candidates,
        search_query=search_query,
        original_message=message,
        pending_intent=pending_intent,
    )
    if confirmation_response is not None:
        return None, confirmation_response

    if top_score >= PRODUCT_CONFIRMATION_THRESHOLD:
        return top_candidate, None

    return None, None


def is_attribute_followup_for_current_product(message: str) -> bool:
    text = normalize_message(message)
    return (
        (
            detect_ingredient_keyword(text) is not None
            and contains_any(text, INCLUSION_KEYWORDS + ["없어", "있어"])
        )
        or detect_nutrition_keyword(text) is not None
    )


def find_recent_history_product_for_followup(
    message: str,
    current_product: Optional[Product],
    chat_history,
) -> Optional[Product]:
    if current_product is None or not chat_history:
        return None

    if not has_product_reference(message, current_product, chat_history):
        return None

    if is_attribute_followup_for_current_product(message):
        return None

    # If the user explicitly names a product in this turn, explicit override/confirmation owns the decision.
    candidates = find_product_candidates_from_message(message, limit=1)
    if candidates and (candidates[0].match_confidence or 0) >= 0.75:
        return None

    history_product = find_product_from_history(chat_history)
    if history_product is None or same_product(history_product, current_product):
        return None

    return history_product


def has_general_topic_shift_cue(text: str) -> bool:
    return contains_any(text, GENERAL_TOPIC_SHIFT_KEYWORDS)


def has_general_condition_frame(text: str) -> bool:
    return contains_any(text, ["있으면", "있는 사람", "환자", "경우", "일반적으로", "제품 고를"])


def has_product_reference(message: str, product: Optional[Product], chat_history) -> bool:
    text = normalize_message(message)
    if not text:
        return False

    if product is None and not chat_history:
        return False

    if contains_any(text, PRODUCT_REFERENCE_KEYWORDS):
        return True

    if product and product.product_name and product.product_name.strip().lower() in text:
        return True

    if product and re.fullmatch(r"\d+(?:\.\d+)?\s*(?:g|ml)\s*기준으로\s*(?:정리|알려|보여)\s*(?:해)?\s*줘[.!?]*", text):
        return True

    if product and should_search_products_from_message(text):
        candidates = find_product_candidates_from_message(text, limit=3)
        if any(same_product(product, candidate) for candidate in candidates):
            return True

    if product and chat_history and contains_any(text, PRODUCT_DETAIL_FOLLOWUP_KEYWORDS):
        return not has_general_condition_frame(text)

    if has_general_condition_frame(text) and not contains_any(text, PRODUCT_REFERENCE_KEYWORDS):
        return False

    if is_context_dependent_followup(text, chat_history):
        return True

    if contains_any(text, EATABILITY_KEYWORDS):
        return True

    if detect_ingredient_keyword(text) is not None and contains_any(text, INCLUSION_KEYWORDS + ["없어", "있어"]):
        return True

    if detect_nutrition_keyword(text) is not None and (
        len(text) <= 32 or contains_any(text, ["설명", "알려", "기준", "같은", "수치", "함량"])
    ):
        return True

    if (
        product
        and chat_history
        and contains_any(text, ["영양", "당류", "나트륨", "탄수화물", "단백질", "지방", "칼로리", "열량", "식단 조건"])
        and contains_any(text, ["설명", "알려", "봐줘", "정리", "기준"])
    ):
        return True

    if (
        contains_any(text, ["알레르기", "알러지", "원재료", "성분"])
        and len(text) <= 24
        and chat_history
        and not has_general_topic_shift_cue(text)
    ):
        return True

    return False


def looks_like_general_topic_shift(message: str, profile: Optional[Profile], product: Optional[Product], chat_history) -> bool:
    text = normalize_message(message)
    if not text:
        return False
    from app.intent.request_semantics import package_amount, missing_nutrient_check
    if product is not None and (package_amount(message) or missing_nutrient_check(message)):
        return False
    from app.intent.explanation_topics import resolve_explanation_question, explanation_intent
    if explanation_intent(message) and re.search(r"뜻|의미|개념|정의|차이|무엇|이란", message):
        if product is None or not explicitly_mentions_product(message, product):
            return True
    if re.fullmatch(r"(?:(?:그럼|그러면|이번에는|이제)\s*)?(?:(?:두|세|네|모든|선택한)\s*(?:사람|명|프로필)|둘\s*다|모두|가족)(?:의)?\s*(?:기준|조건)(?:으로)?\s*(?:알려줘|봐줘|부탁해(?:요)?|다시\s*확인해줘)[.!?\s]*", message):
        return False
    if resolve_explanation_question(message, chat_history) != message:
        # Catalog names can themselves contain general topic words, such as
        # "저염 두부스낵". A real user product mention still owns its follow-up.
        return find_product_from_history(chat_history) is None

    current_context_intent = rule_based_classify_intent(
        message=text,
        product=product,
        profile=profile,
        chat_history=chat_history,
    )
    if current_context_intent == "profile_based_recheck":
        return False

    if product is not None and current_context_intent == "alternative_food_recommend":
        return False

    if current_context_intent == "recipe_recommend":
        if re.search(r"(?<![가-힣])(?:제품\s*(?:없이|없는)|일반\s*레시피|기본\s*식재료)", text):
            return True
        if _is_diet_only_recipe_query(message) and not contains_any(text, PRODUCT_REFERENCE_KEYWORDS):
            return True
        if product is not None:
            return False
        recipe_candidates = find_product_candidates_from_message(text, limit=1)
        if (
            recipe_candidates
            and not _is_diet_only_recipe_query(message)
            and (
                explicitly_mentions_product(message, recipe_candidates[0])
                or mentions_distinct_product_token(message, recipe_candidates[0])
            )
        ):
            return False
        return True

    if has_product_reference(text, product, chat_history):
        return False

    if product is None and contains_any(text, PRODUCT_SEARCH_REQUEST_KEYWORDS):
        candidates = find_product_candidates_from_message(text, limit=1)
        if candidates:
            return False

    general_intent = rule_based_classify_intent(
        message=text,
        product=None,
        profile=profile,
        chat_history=[],
    )
    if general_intent in {"alternative_food_recommend", "food_compare"}:
        return True

    if general_intent.startswith("general_"):
        if product is None and not chat_history:
            candidates = find_product_candidates_from_message(text, limit=1)
            if (
                candidates
                and (
                    explicitly_mentions_product(message, candidates[0])
                    or mentions_distinct_product_token(message, candidates[0])
                )
            ):
                return False
        return True

    if has_general_topic_shift_cue(text) and len(text) >= 8:
        return True

    return False


def enrich_product_from_repository(
    product: Optional[Product],
    *,
    barcode: Optional[str] = None,
    report_number: Optional[str] = None,
) -> Optional[Product]:
    if product is None:
        return None

    explicit_references = [
        ("barcode", barcode),
        ("report_number", report_number),
    ]
    explicit_references = [
        (kind, value)
        for kind, value in explicit_references
        if value
    ]
    references = explicit_references or [
        ("barcode", product.barcode),
        ("report_number", product.report_number),
        ("product_id", product.product_id),
    ]
    references = [
        (kind, value)
        for kind, value in references
        if value is not None and value != ""
    ]

    enriched_product = _resolve_product_identifiers(dict(references))

    if enriched_product is not None:
        return enriched_product.model_copy(update={"facts_verified": True})
    if references:
        explicit_reference_supplied = bool(explicit_references)
        return Product(
            product_id=None if explicit_reference_supplied else product.product_id,
            product_name=product.product_name,
            barcode=barcode if explicit_reference_supplied else product.barcode,
            report_number=(
                report_number
                if explicit_reference_supplied
                else product.report_number
            ),
            product_found=False,
            facts_verified=False,
        )
    return product.model_copy(update={"facts_verified": False})


def _resolve_product_identifiers(references: dict) -> Optional[Product]:
    """Resolve a precise identity, then verify every supplied ID on that row.

    A report number may be shared by multiple package sizes/barcodes. Looking
    each identifier up separately would compare an arbitrary package returned
    by report lookup with the explicitly selected barcode. Missing or conflicting
    identifiers still fail closed; a failed barcode never falls back to a report.
    """
    lookups = (
        ("barcode", find_product_by_barcode),
        ("product_id", find_product_by_product_id),
        ("report_number", find_product_by_report_number),
    )
    for kind, lookup in lookups:
        if kind not in references:
            continue
        product = lookup(references[kind])
        if product is None:
            return None
        return product if all(
            getattr(product, field) == value for field, value in references.items()
        ) else None
    return None


def resolve_clarification_product(
    *, message: str, product: Optional[Product], barcode: Optional[str],
    report_number: Optional[str], conversation_state: Optional[dict],
) -> Optional[Product]:
    """Resolve known identifiers only; never search/select while conditions await.

    Request label fields are not evidence. A failed new identifier must not fall
    back to an old product; saved references are also reloaded from the catalog.
    """
    reference = {
        "barcode": barcode, "report_number": report_number,
        "product_id": product.product_id if product is not None else None,
    }
    if not any(value is not None for value in reference.values()):
        state = conversation_state or {}
        # Only an unambiguous deictic follow-up may reuse the previous target.
        # New names, comparisons and unresolved candidate lists stay unresolved.
        if product is not None or state.get("pending_product_candidates") or not refers_only_to_current_product(message):
            return None
        reference = state.get("last_confirmed_product")
    resolved, _, _ = _resolve_authoritative_product_reference(reference)
    return resolved if resolved is not None and resolved.facts_verified else None


def _resolve_authoritative_product_reference(
    reference: object,
) -> tuple[Optional[Product], Optional[str], Optional[str]]:
    if not isinstance(reference, dict):
        return None, None, None

    barcode_value = reference.get("barcode")
    report_value = reference.get("report_number")
    product_id_value = reference.get("product_id")
    barcode = str(barcode_value).strip() if barcode_value is not None else None
    report_number = str(report_value).strip() if report_value is not None else None
    barcode = barcode or None
    report_number = report_number or None
    product_id = (
        product_id_value
        if isinstance(product_id_value, int)
        and not isinstance(product_id_value, bool)
        and product_id_value >= 0
        else None
    )

    references = [
        ("barcode", barcode),
        ("report_number", report_number),
        ("product_id", product_id),
    ]
    references = [
        (kind, value)
        for kind, value in references
        if value is not None
    ]
    product = _resolve_product_identifiers(dict(references))
    if product is None:
        return None, None, None
    return product, product.barcode or barcode, product.report_number or report_number


_CANDIDATE_SELECTION_PATTERNS = [
    (re.compile(r"(?:(?<![가-힣A-Za-z0-9])첫\s*번째|(?<![가-힣A-Za-z0-9])첫째|(?<!\d)1\s*(?:번째|번)(?!\d))"), 0),
    (re.compile(r"(?:(?<![가-힣A-Za-z0-9])두\s*번째|(?<![가-힣A-Za-z0-9])둘째|(?<!\d)2\s*(?:번째|번)(?!\d))"), 1),
    (re.compile(r"(?:(?<![가-힣A-Za-z0-9])세\s*번째|(?<![가-힣A-Za-z0-9])셋째|(?<!\d)3\s*(?:번째|번)(?!\d))"), 2),
]

_NUMERIC_COMPOUND_SEPARATORS = frozenset(
    ".,，、+-−±–—/／~～:：;；·"
)
_NUMERIC_SIGN_CHARACTERS = frozenset("+-−±")
_KOREAN_COMPOUND_NUMBER_PREFIXES = frozenset({
    "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔",
    "백", "천", "만",
})
_CANDIDATE_REJECTION_CUES = (
    "말고", "말구", "아니", "아냐", "아녀", "아님",
    "싫", "빼", "제외", "패스", "별로", "대신", "취소", "필요없",
    "원하지", "원치", "선택하지", "고르지", "안고르", "안할", "안먹",
    "안돼", "안되", "안된", "안될", "못먹", "먹지않", "괜찮지않",
    "하지않", "지않을", "않아", "안좋", "안괜찮", "안들", "안드", "안맞",
    "안위험", "별론",
)
_CANDIDATE_TRANSFER_CUES = ("말고", "아니고", "아니라", "대신")
_CANDIDATE_COMPARISON_CUES = (
    "보다", "비교", "비해", "비하면", "대비", "차이",
    "나아", "나은", "낫", "더좋", "더안전", "우위",
)
_CANDIDATE_EXPLICIT_SELECTION_CUES = (
    "선택할게", "선택해", "고를게", "골라", "로할게", "으로할게", "가맞아", "가맞아요",
)
_CANDIDATE_OTHER_TARGET_CUES = ("다른", "나머지", "그외", "그밖")
_CANDIDATE_REJECTION_TARGET_MARKERS = (
    "건", "것은", "거는", "후보는", "제품은", "항목은",
)


def _compact_candidate_clause(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", text.lower())


def _numeric_ordinal_has_compound_context(text: str, start: int) -> bool:
    raw_prefix = text[:start]
    separated_by_space = bool(raw_prefix and raw_prefix[-1].isspace())
    prefix = raw_prefix.rstrip()
    if not prefix:
        return False

    previous = prefix[-1]
    if previous.isalnum():
        return previous.isnumeric() or not separated_by_space
    if previous not in _NUMERIC_COMPOUND_SEPARATORS:
        return False
    if previous in _NUMERIC_SIGN_CHARACTERS:
        return True

    before_separator = prefix[:-1].rstrip()
    return bool(before_separator and before_separator[-1].isnumeric())


def _korean_ordinal_has_compound_context(text: str, start: int) -> bool:
    prefix = text[:start].rstrip()
    if not prefix:
        return False
    previous_token = re.split(r"\s+", prefix)[-1]
    return previous_token in _KOREAN_COMPOUND_NUMBER_PREFIXES


def _ordinal_has_compound_context(text: str, start: int, matched_text: str) -> bool:
    if matched_text and matched_text[0].isdigit():
        return _numeric_ordinal_has_compound_context(text, start)
    return _korean_ordinal_has_compound_context(text, start)


def _candidate_clause_flags(clause: str) -> tuple[bool, bool, bool, bool]:
    compact = _compact_candidate_clause(clause)
    rejected = any(cue in compact for cue in _CANDIDATE_REJECTION_CUES)
    transfer = any(cue in compact for cue in _CANDIDATE_TRANSFER_CUES)
    compared = any(cue in compact for cue in _CANDIDATE_COMPARISON_CUES)
    explicitly_selected = any(
        cue in compact
        for cue in _CANDIDATE_EXPLICIT_SELECTION_CUES
    )
    return rejected, transfer, compared, explicitly_selected


def _candidate_clause_introduces_rejected_target(clause: str) -> bool:
    compact = _compact_candidate_clause(clause)
    for cue in _CANDIDATE_REJECTION_CUES:
        cue_index = compact.rfind(cue)
        if cue_index < 0:
            continue
        suffix = compact[cue_index + len(cue):]
        if any(marker in suffix for marker in _CANDIDATE_REJECTION_TARGET_MARKERS):
            return True
    return False


def _leading_rejection_blocks_candidate_selection(
    text: str,
    raw_occurrences: list[tuple[int, int, int, str]],
) -> bool:
    if not raw_occurrences:
        return False

    first_start = raw_occurrences[0][0]
    leading_clause = text[:first_start]
    leading_rejected, _, leading_compared, _ = _candidate_clause_flags(
        leading_clause
    )
    if leading_compared:
        return True
    if leading_rejected:
        # When a rejection predicate precedes multiple ordinals, its target is
        # ambiguous (for example, "싫은 건 첫 번째가 아니라 두 번째"). Never
        # turn the remaining ordinal into an implicit positive safety selection.
        if len(raw_occurrences) != 1:
            return True

        _, ordinal_end, _, _ = raw_occurrences[0]
        trailing_rejected, _, trailing_compared, trailing_explicitly_selected = (
            _candidate_clause_flags(text[ordinal_end:])
        )
        leading_compact = _compact_candidate_clause(leading_clause)
        clearly_rejects_other_candidates = any(
            cue in leading_compact
            for cue in _CANDIDATE_OTHER_TARGET_CUES
        )

        # Preserve an explicit positive choice such as
        # "다른 건 싫고 첫 번째로 할게". Without both the other-candidate
        # target and an explicit selection verb, ambiguity stays fail-closed.
        return not (
            clearly_rejects_other_candidates
            and trailing_explicitly_selected
            and not trailing_rejected
            and not trailing_compared
        )

    # A later ordinal can have its own leading rejection predicate in the text
    # between two ordinals. Do not attribute that predicate only to the previous
    # ordinal and then treat the rejected next ordinal as the positive choice.
    for previous, current in zip(raw_occurrences, raw_occurrences[1:]):
        local_leading_clause = text[previous[1]:current[0]]
        if _candidate_clause_introduces_rejected_target(local_leading_clause):
            return True

    return False


def _pending_candidate_reference(
    message: str,
    conversation_state: Optional[dict],
) -> Optional[dict]:
    """Interpret a pick locally. Catalog reads happen only after conditions resolve."""
    if not conversation_state:
        return None

    candidates = conversation_state.get("pending_product_candidates")
    if not isinstance(candidates, list):
        return None

    selection_message, _ = comparison_pick_parts(message, conversation_state)
    text = normalize_message(selection_message)
    if re.search(r"(?:마이너스|음수|[-−])\s*\d+\s*번", text):
        return None
    position = _positional_candidate_index(text, len(candidates))
    if position is not None:
        reference = candidates[position]
        return reference if isinstance(reference, dict) else None
    # A standalone correction followed by one positive pick is unambiguous.
    # Rejection predicates such as '싫은 건 두 번째' remain fail-closed.
    text = re.sub(r"^아니[,，\s]+(?=(?:첫|두|세)\s*번째\s*(?:걸로|거로)(?:요)?[.!?\s]*$)", "", text)
    raw_occurrences = sorted(
        (
            match.start(),
            match.end(),
            index,
            match.group(0),
        )
        for pattern, index in _CANDIDATE_SELECTION_PATTERNS
        for match in pattern.finditer(text)
    )
    if not raw_occurrences:
        # A full candidate name plus an explicit selection verb is also a pick.
        matches = [item for item in candidates if isinstance(item, dict)
                   and item.get("product_name") and re.fullmatch(
                       re.escape(item["product_name"]) + r"\s*(?:으로|로)?\s*(?:할게|선택할게|선택해줘|선택)(?:요)?[.!\s]*",
                       selection_message.strip(),
                   )]
        if len(matches) == 1:
            return matches[0]
        return None

    message_compact = _compact_candidate_clause(text)
    if any(cue in message_compact for cue in _CANDIDATE_COMPARISON_CUES):
        return None

    if any(
        _ordinal_has_compound_context(text, start, matched_text)
        for start, _, _, matched_text in raw_occurrences
    ):
        return None

    if _leading_rejection_blocks_candidate_selection(text, raw_occurrences):
        return None

    occurrences = []
    for position, (start, end, index, _) in enumerate(raw_occurrences):
        next_start = (
            raw_occurrences[position + 1][0]
            if position + 1 < len(raw_occurrences)
            else len(text)
        )
        rejected, transfer, compared, explicitly_selected = (
            _candidate_clause_flags(text[end:next_start])
        )
        occurrences.append({
            "index": index,
            "rejected": rejected,
            "transfer": transfer,
            "compared": compared,
            "explicitly_selected": explicitly_selected,
        })

    if any(occurrence["compared"] for occurrence in occurrences):
        return None

    positive_occurrences = [
        occurrence
        for occurrence in occurrences
        if not occurrence["rejected"]
    ]
    if len(positive_occurrences) != 1:
        return None

    selected_occurrence = positive_occurrences[0]
    selected_index = selected_occurrence["index"]
    if len(occurrences) > 1:
        rejected_occurrences = [
            occurrence
            for occurrence in occurrences
            if occurrence["rejected"]
        ]
        rejected_indexes = {
            occurrence["index"]
            for occurrence in rejected_occurrences
        }
        has_clear_transfer = (
            len(occurrences) == 2
            and any(occurrence["transfer"] for occurrence in rejected_occurrences)
        )
        if (
            len(rejected_occurrences) != len(occurrences) - 1
            or selected_index in rejected_indexes
            or not (
                has_clear_transfer
                or selected_occurrence["explicitly_selected"]
            )
        ):
            return None

    if selected_index is None or selected_index >= len(candidates):
        return None

    reference = candidates[selected_index]
    return reference if isinstance(reference, dict) else None


def _pending_candidate_selection(message, conversation_state):
    return _resolve_authoritative_product_reference(_pending_candidate_reference(message, conversation_state))


def _resolved_pending_candidate_products(
    conversation_state: Optional[dict],
) -> list[Product]:
    if not conversation_state:
        return []

    references = conversation_state.get("pending_product_candidates")
    if not isinstance(references, list):
        return []

    products: list[Product] = []
    for reference in references:
        product, _, _ = _resolve_authoritative_product_reference(reference)
        if product is None or any(same_product(product, item) for item in products):
            continue
        products.append(product)
    return products


def _is_pending_candidate_comparison(message: str) -> bool:
    text = normalize_message(message)
    compact = _compact_candidate_clause(text)
    if not any(cue in compact for cue in _CANDIDATE_COMPARISON_CUES):
        return False

    ordinal_indexes = {
        index
        for pattern, index in _CANDIDATE_SELECTION_PATTERNS
        if pattern.search(text)
    }
    if len(ordinal_indexes) >= 2:
        return True

    return any(
        cue in compact
        for cue in [
            "둘",
            "두제품",
            "두후보",
            "후보들",
            "각각",
            "서로",
            "뭐가더",
            "어느게",
            "어떤게",
        ]
    )


def _pending_candidate_cards(products: list[Product]) -> list[dict]:
    return [
        public_product_payload(product, view="candidate")
        | {
            "reason": "아직 선택되지 않은 제품 후보예요.",
            "reasons": ["제품 식별 정보 확인", "사용자 선택 필요"],
            "selection_type": "product_candidate",
        }
        for product in products[:3]
    ]


def _pending_context(conversation_state: Optional[dict]) -> dict:
    if not isinstance(conversation_state, dict):
        return {}
    context = {
        key: conversation_state[key]
        for key in ("pending_user_message", "pending_intent")
        if isinstance(conversation_state.get(key), str)
        and conversation_state[key].strip()
    }
    from app.core.conversation_context_fields import task_context_fields
    context.update(task_context_fields(conversation_state, lambda value: value))
    return context


def _pending_candidate_state(
    products: list[Product],
    conversation_state: Optional[dict] = None,
) -> dict:
    return {
        "version": 1,
        "last_intent": "product_confirmation",
        "last_task_type": "product_chat",
        "pending_product_candidates": [
            _product_state_reference(product)
            for product in products[:3]
        ],
        **_pending_context(conversation_state),
    }


def _pending_candidate_internal_state(
    products: list[Product],
    conversation_state: Optional[dict] = None,
) -> dict:
    return {
        "version": 1,
        "last_intent": "product_confirmation",
        "last_task_type": "product_chat",
        "pending_product_candidates": [
            internal_product_reference(product)
            for product in products[:3]
        ],
        **_pending_context(conversation_state),
    }


def _pending_candidate_display_line(index: int, product: Product) -> str:
    details = []
    manufacturer = product.manufacturer_name or product.company_name
    if manufacturer:
        details.append(f"제조사 {manufacturer}")
    weight = product.weight or product.total_weight
    if weight:
        details.append(f"용량 {weight}")
    category = product.category_detail or product.category_small or product.prdlst_dcnm
    if category:
        details.append(f"유형 {category}")
    detail_text = f" ({', '.join(details)})" if details else ""
    return f"{index}. {product.product_name or '제품명 미확인'}{detail_text}"


def _build_pending_candidate_response(
    products: list[Product],
    *,
    comparison_requested: bool,
    conversation_state: Optional[dict] = None,
) -> ChatResponse:
    cards = _pending_candidate_cards(products)
    if comparison_requested:
        identity_lines = [
            _pending_candidate_display_line(index, product)
            for index, product in enumerate(products[:3], start=1)
        ]
        answer = (
            "후보를 구분할 수 있도록 DB에서 확인된 제품 식별 정보만 정리했어요.\n"
            + "\n".join(identity_lines)
            + f"\n{NONE_SELECTED_YET}고, 섭취 안전성도 평가하지 않았어요. "
            "맞는 제품 하나를 선택해 주세요."
        )
        reasons = ["제품 후보 식별 정보 비교", "섭취 안전성 미평가"]
    else:
        answer = (
            f"{NOT_CONFIRMED_YET}. 아래 후보 중 맞는 제품 하나를 "
            "선택해 주세요. 제품을 고르기 전에는 첫 번째 후보를 임의로 사용하거나 "
            "섭취 안전성을 평가하지 않아요."
        )
        reasons = ["제품 후보 선택 대기", "섭취 안전성 미평가"]

    ordinal_labels = ["첫 번째", "두 번째", "세 번째"]
    return ChatResponse(
        task_type="product_chat",
        answer_source="rules",
        pipeline_stage="rules",
        intent="unknown",
        answer=answer,
        risk_level="unknown",
        assessment_status="incomplete",
        reasons=reasons,
        recommended_questions=[
            f"{ordinal_labels[index]} 선택할게"
            for index in range(min(len(products), len(ordinal_labels)))
        ],
        product_candidates=cards,
        recommended_products=cards,
        conversation_state=_pending_candidate_state(products, conversation_state),
        internal_conversation_state=_pending_candidate_internal_state(
            products,
            conversation_state,
        ),
    )


def _positional_candidate_index(message: str, count: int):
    from app.intent.request_semantics import command_text
    message = command_text(message)
    message = re.sub(r"걸로\s*할게(?:요)?[.!?\s]*$", "거로", message)
    ending = r"(?:로|으로|요)?\s*(?:(?:선택|골라)해?줘|골랐어(?:요)?|선택할게(?:요)?|할게(?:요)?)?[.!?\s]*"
    if count and re.fullmatch(r"\s*(?:(?:맨\s*)?마지막\s*(?:거|것|후보|상품|제품)|맨\s*아래(?:에)?\s*(?:있는\s*)?(?:거|것))" + ending, message):
        return count - 1
    if count and re.fullmatch(r"\s*맨\s*위(?:에)?\s*(?:있는\s*)?(?:거|것|상품|제품)" + ending, message):
        return 0
    return None


def _is_candidate_selection_only(message: str) -> bool:
    if _positional_candidate_index(message, 3) is not None:
        return True
    message = re.sub(r"^아니[,，\s]+(?=(?:첫|두|세)\s*번째\s*(?:걸로|거로)(?:요)?[.!?\s]*$)", "", message.strip())
    compact = re.sub(r"[\s?!.,]", "", normalize_message(message))
    selection_phrases = {
        "첫번째", "첫째", "1번", "1번째",
        "두번째", "둘째", "2번", "2번째",
        "세번째", "셋째", "3번", "3번째",
    }
    suffixes = ["", "요", "걸로", "거로", "걸로요", "이거", "맞아", "맞아요", "선택", "선택할게", "로할게", "으로할게", "제품으로할게", "제품으로할게요"]
    if any(
        compact == f"{phrase}{suffix}"
        for phrase in selection_phrases
        for suffix in suffixes
    ):
        return True

    remainder = compact
    for phrase in sorted(selection_phrases, key=len, reverse=True):
        remainder = remainder.replace(phrase, "")
    for connector in ["가아니라", "이아니라", "말고", "아니고", "빼고", "제외하고"]:
        remainder = remainder.replace(connector, "")
    return remainder in suffixes


def pending_original_message_for_selection(
    message: str,
    conversation_state: Optional[dict],
) -> Optional[str]:
    """Return the bounded original question only for one clear candidate pick."""

    selection_message, axis = comparison_pick_parts(message, conversation_state)
    selected_product, _, _ = _pending_candidate_selection(
        message,
        conversation_state,
    )
    if selected_product is None or not isinstance(conversation_state, dict):
        return None
    if not is_pending_candidate_reply(message, conversation_state):
        return None
    original_message = conversation_state.get("pending_user_message")
    pending_intent = conversation_state.get("pending_intent")
    if not isinstance(original_message, str) or not original_message.strip():
        return None
    if not isinstance(pending_intent, str) or not pending_intent.strip():
        return None
    return original_message.strip() + (f" {axis}으로 비교해줘" if axis else "")


def is_pending_candidate_reply(message: str, conversation_state: Optional[dict]) -> bool:
    """Shared, I/O-free language check; never trusts a candidate's label facts."""
    reference = _pending_candidate_reference(message, conversation_state)
    if reference is None:
        return False
    selection_message, _ = comparison_pick_parts(message, conversation_state)
    named_pick = bool(reference.get("product_name") and re.fullmatch(
        re.escape(reference["product_name"]) + r"\s*(?:으로|로)?\s*(?:할게|선택할게|선택해줘|선택)(?:요)?[.!\s]*",
        selection_message.strip(),
    ))
    return _is_candidate_selection_only(selection_message) or named_pick


def _build_candidate_selected_response(product: Product) -> ChatResponse:
    product_payload = public_product_payload(product)
    reference = _product_state_reference(product)
    return ChatResponse(
        task_type="product_chat",
        answer_source="rules",
        pipeline_stage="rules",
        intent="product_summary",
        answer=(
            f"'{product.product_name or '선택한 제품'}'{SELECTION_CONFIRMED}. "
            "이제 먹어도 되는지, 알레르기 성분이나 영양정보를 이어서 물어봐 주세요."
        ),
        risk_level="unknown",
        assessment_status="incomplete",
        reasons=["제품 후보 선택 완료", "섭취 안전성은 아직 평가하지 않음"],
        current_product=product_payload,
        product_reference=internal_product_reference(product),
        conversation_state={
            "version": 1,
            "last_intent": "product_summary",
            "last_task_type": "product_chat",
            "last_confirmed_product": reference,
        },
        internal_conversation_state={
            "version": 1,
            "last_intent": "product_summary",
            "last_task_type": "product_chat",
            "last_confirmed_product": internal_product_reference(product),
        },
    )


def _explicit_named_product_query(message, profile):
    """Separate a named target from an attribute, profile or deictic reply.

    A lookup miss is a result of this target, never permission to restore an
    older target. Reuse the catalog query boundary rather than catalog success
    to decide whether the user actually supplied a name.
    """
    from app.intent.request_semantics import command_text
    from app.services.condition_subject import profile_is_named
    from app.intent.rules import is_out_of_domain_question
    text = command_text(message)
    if is_out_of_domain_question(text, None):
        return None
    if _is_candidate_selection_only(text):
        return None
    clauses = [clause.strip() for clause in re.split(r"[.!?;](?!\d)\s*", text) if clause.strip()]
    if len(clauses) > 1:
        # '아이 거야. 이 제품 먹어도 돼?' contains an owner reply and a
        # deictic task, not a new catalog name spanning both sentences.
        named = [_explicit_named_product_query(clause, profile) for clause in clauses]
        return next((query for query in reversed(named) if query), None)
    # '유당불내증인데 새우깡 먹어도 돼?': the leading condition clause is context,
    # so look for the catalog name only in what follows it.
    condition_clause = re.match(
        r"\s*\S*(?:불내증|셀리악\S*)(?:이|가)?\s*(?:있(?:는데|어서|으니까)|(?:이)?(?:라서|인데|이니까))\s+(\S.*)$", text,
    )
    if condition_clause:
        return _explicit_named_product_query(condition_clause[1], profile)
    query = extract_product_search_query(text)
    if not query or compact_search_text(query) == compact_search_text(text):
        return None
    if re.search(r"(?:첫|두|세)\s*번째|(?:맨\s*)?(?:위|아래|마지막|처음)\s*(?:제품|상품|거|것)", query):
        return None
    # '이 제품에 특정 성분이 들어 있어?' refers to a product; it names none.
    if re.match(r"(?:이|그|저|해당|현재|방금)\s*(?:제품|상품)", query):
        return None
    from app.services.comparison_language import comparison_axis_reply
    if comparison_axis_reply(text):
        return None
    from app.intent.request_semantics import food_recommendation_request
    if food_recommendation_request(text):
        return None
    if re.match(r"(?:나트륨|단백질|당류|당분|칼로리|열량|지방|탄수화물|콜레스테롤)(?:\s+|하고|과|와)", query):
        return None
    # '그럼 원재료에 들어갔다는 건 아니지?' asks about a label field of the
    # current topic; a field word with a particle cannot start a catalog name.
    if re.match(r"(?:원재료|원료|성분|표시|라벨)(?:명)?(?:에는|에서|에|이|가|은|는|을|를|도|으로|로)(?:\s|$)", query):
        return None
    if detect_nutrition_keyword(query) and re.fullmatch(
        r"(?:(?:나트륨|단백질|당류|당분|칼로리|열량|포화지방|지방|탄수화물|콜레스테롤)(?:과|와|하고|이랑|랑|[,/·\s])?\s*)+", query,
    ):
        return None
    if re.match(r"(?:한|두|세|네|반|\d+(?:\.\d+)?)\s*(?:팩|봉지|통|병|개|g|ml|kg|l)(?:\s|[을를의은는도만]|$)", query, re.I):
        return None
    # A condition stated elsewhere in the sentence is not part of a catalog
    # name either; intolerance wording is handled like allergy wording.
    if re.search(r"비교|레시피|요리|알레르기|알러지|불내증|셀리악", text):
        return None
    if profile and profile_is_named(profile, query):
        return None
    if re.search(r"(?:^|\s)(?:엄마|아빠|어머니|아버지)(?:를|을|는|은)?\s*(?:빼면|빼고|말고|제외)", query):
        return None
    if re.search(r"(?:^|\s)(?:엄마|아빠|어머니|아버지|그럼|그러면|이번|두|세|모든|선택한)(?:\s|한테|에게|기준|조건|$)", query):
        return None
    if re.search(r"기준|조건|대상|사람|프로필|둘|현재|이전", query):
        return None
    if re.search(r"(?:같이|함께|순서|선택된|먹은\s*양|마신\s*양)", query):
        return None
    if re.fullmatch(r"(?:(?:이|그|해당)\s*)?(?:제품|상품|거|것|성분|원재료|영양|나트륨|당류|단백질|칼로리|열량)(?:은|는|도)?", query):
        return None
    if re.search(r"뜻|의미|개념|정의|차이|단위|총\s*당류.*첨가당|첨가당.*총\s*당류|비건.*표시(?:돼|되어|라고)", text):
        return None
    from app.intent.explanation_topics import requested_topics
    # '제로라면 괜찮아?' is a condition on a label claim; the noun in '진라면'
    # is a product, so the conditional must follow a claim word.
    claim_condition = r"(?:제로|무설탕|무당|무가당|저당|저칼로리|저열량|저지방|저염|저나트륨|고단백|라이트)\S*(?:이면|라면|면)"
    if requested_topics(text) and re.search(rf"영양\s*표시|{claim_condition}|과자면|짠\s*맛|안\s*짠", text):
        return None
    if requested_topics(text) and re.fullmatch(
        r"(?:제로|무설탕|무당|무가당|저당|저칼로리|저열량|저지방|저염|저나트륨|고단백|라이트)\s*"
        r"(?:음료|음료수|탄산음료|탄산|음식|식품|제품|과자|간식|커피|아이스크림)?(?:은|는|이|가|도)?", query,
    ):
        # '제로 음료는 칼로리가 0이야?' asks what a claim means for a kind of
        # food, not about a catalog item ('펩시 제로' still names one).
        return None
    if requested_topics(query) and query.endswith("면"):
        # A conditional about a recognized label claim ('제로칼로리면')
        # is not a catalog name. Unknown named products still retain their
        # own unresolved target rather than falling back to the old product.
        return None
    from app.data.dictionaries import INGREDIENT_KEYWORDS
    short_switch = (re.fullmatch(rf"\s*{re.escape(query)}\s*(?:은|는)\s*[?？]*\s*", text)
                    and compact_search_text(query) not in {compact_search_text(word) for word in INGREDIENT_KEYWORDS}
                    and not detect_nutrition_keyword(query)
                    and not re.fullmatch(r"(?:엄마|아빠|어머니|아버지|언니|오빠|누나|형|동생|아이|아기|할머니|할아버지)", query))
    if short_switch:
        # '새우깡은?' after another product names the next product; '우유는?'
        # and '칼로리는?' stay questions about the current one.
        return query
    if not re.search(r"먹어도|먹어두|머거도|먹을\s*수|마셔도|마셔두|섭취해도|어때|괜찮|바꿨어|바꿨|변경했|선택했|골랐|찾아|검색|원재료|성분|나트륨|당류|단백질|열량|칼로리|콜레스테롤", text):
        return None
    return query


def _missing_named_product_response(query, state):
    from app.core.conversation_context_fields import carry_task_context
    context = carry_task_context(state, {
        "version": 1, "last_intent": "missing_product", "last_task_type": "product_chat",
        "unresolved_product_query": query[:256],
    })
    return ChatResponse(
        intent="missing_product", task_type="product_chat", answer_source="rules",
        answer=f"{NEW_PRODUCT_NOT_FOUND}: {query[:256]}. 정확한 제품명이나 바코드를 알려 주세요. 제품을 확인한 뒤 성분과 영양정보를 안내할게요.",
        risk_level="unknown", assessment_status="incomplete", overall_decision="check_required",
        missing_information=["요청 제품의 정확한 제품명 또는 바코드"],
        conversation_state=public_conversation_state(context), internal_conversation_state=context,
    )


def resolve_product_context(
    message: str,
    profile: Optional[Profile],
    product: Optional[Product],
    barcode: Optional[str],
    report_number: Optional[str],
    chat_history,
    conversation_state: Optional[dict] = None,
    explicit_barcode_reference: Optional[str] = None,
    explicit_report_number_reference: Optional[str] = None,
) -> tuple[Optional[Product], Optional[str], Optional[str], Optional[ChatResponse]]:
    embedded_request_reference_provided = bool(
        product is not None
        and _has_authoritative_product_reference(product)
    )
    authoritative_request_reference_provided = bool(
        explicit_barcode_reference
        or explicit_report_number_reference
        or (product is None and (barcode or report_number))
    )
    pending_references = (
        conversation_state.get("pending_product_candidates")
        if isinstance(conversation_state, dict)
        else None
    )
    independent_target = not authoritative_request_reference_provided and not embedded_request_reference_provided
    if independent_target:
        from app.intent.rules import is_out_of_domain_question
        if is_out_of_domain_question(message, None):
            return None, None, None, None
        query = _explicit_named_product_query(message, profile)
        if query:
            # An explicit object wins over a broad nutrition/general topic,
            # even while a previous product candidate is awaiting a choice.
            found, confirmation = find_product_from_request_text(query + " 찾아줘", [], use_history=False)
            if confirmation is not None and confirmation.product_candidates:
                # Lookup uses the isolated name; the pending task retains the
                # user's actual question, including its requested attributes.
                pending = dict(confirmation.internal_conversation_state or {})
                pending["pending_user_message"] = message
                public = confirmation.conversation_state.model_dump(mode="json", exclude_none=True)
                public["pending_user_message"] = message
                confirmation = confirmation.model_copy(update={
                    "internal_conversation_state": pending,
                    "conversation_state": public_conversation_state(public),
                })
            if found is not None or confirmation is not None:
                return found, found.barcode if found else None, found.report_number if found else None, confirmation
            return None, None, None, _missing_named_product_response(query, conversation_state)
        unresolved = (conversation_state or {}).get("unresolved_product_query")
        if unresolved:
            if not looks_like_general_topic_shift(message, profile, None, chat_history):
                return None, None, None, _missing_named_product_response(unresolved, conversation_state)
            # An independent general question may proceed without recovering
            # any catalog target from the older transcript.
            return None, None, None, None
    source_query = recipe_source_query(message)
    from app.services.comparison_language import comparison_axis_reply, comparison_relative_reply
    comparison_owns_axis = (conversation_state or {}).get("last_intent") in (None, "food_compare", "unknown")
    comparison_followup = (comparison_owns_axis and comparison_axis_reply(message)) or comparison_relative_reply(message)
    if ((conversation_state or {}).get("comparison_status") in {"cleared", "active"}
            and not authoritative_request_reference_provided and not embedded_request_reference_provided
            and (comparison_followup or rule_based_classify_intent(message, None, profile, []) == "food_compare")):
        # Comparison targets are resolved by the comparison handler. A cleared
        # task must not restore a single old product from assistant history.
        return None, None, None, None
    if (source_query and compact_search_text(source_query) not in PRODUCT_REFERENCE_ONLY_TOKENS
            and not authoritative_request_reference_provided and not embedded_request_reference_provided
            and product is None and not pending_references):
        # An explicit new source owns this request, including a zero-result
        # lookup. Never restore a previous product or silently use generic food.
        source, confirmation = find_product_from_request_text(message, [], use_history=False)
        if source is not None or confirmation is not None:
            return source, source.barcode if source else None, source.report_number if source else None, confirmation
        from app.services.recipe_request_context import missing_recipe_source_response
        return None, None, None, missing_recipe_source_response(message, conversation_state)
    from app.services.recipe_request_context import pending_recipe_source, missing_recipe_source_response
    from app.services.request_language import requests_continuation
    if (pending_recipe_source(conversation_state)
            and not authoritative_request_reference_provided and not embedded_request_reference_provided
            and (requests_continuation(message) or refers_only_to_current_product(message))):
        return None, None, None, missing_recipe_source_response(message, conversation_state)
    if (
        product is None
        and not barcode
        and not report_number
        and isinstance(pending_references, list)
        and pending_references
    ):
        (
            selected_product,
            selected_barcode,
            selected_report_number,
        ) = _pending_candidate_selection(message, conversation_state)
        if selected_product is not None:
            authoritative_request_reference_provided = True
            product = selected_product
            barcode = selected_barcode
            report_number = selected_report_number
            if pending_original_message_for_selection(message, conversation_state) is not None:
                return product, barcode, report_number, None
            if _is_candidate_selection_only(message):
                if pending_original_message_for_selection(
                    message,
                    conversation_state,
                ) is not None:
                    # The API resumes the saved question with the now-grounded
                    # product. Legacy state without that context keeps the old
                    # explicit "selected" acknowledgement below.
                    return product, barcode, report_number, None
                return (
                    product,
                    barcode,
                    report_number,
                    _build_candidate_selected_response(product),
                )

        if selected_product is None:
            pending_products = _resolved_pending_candidate_products(conversation_state)
            if pending_products and _is_pending_candidate_comparison(message):
                return (
                    None,
                    None,
                    None,
                        _build_pending_candidate_response(
                            pending_products,
                            comparison_requested=True,
                            conversation_state=conversation_state,
                    ),
                )

            # Pending product confirmation is a dedicated state. Unless this
            # turn is a clear general-topic shift, never let generic history
            # search silently turn the first prior match into a selection.
            if not looks_like_general_topic_shift(
                message,
                profile,
                None,
                chat_history,
            ):
                if pending_products:
                    return (
                        None,
                        None,
                        None,
                        _build_pending_candidate_response(
                            pending_products,
                            comparison_requested=False,
                            conversation_state=conversation_state,
                        ),
                    )
                return (
                    None,
                    None,
                    None,
                    ChatResponse(
                        task_type="product_chat",
                        answer_source="rules",
                        pipeline_stage="rules",
                        intent="missing_product",
                        answer=(
                            "이전에 제시한 제품 후보를 현재 DB에서 다시 확인하지 못했어요. "
                            "제품명이나 바코드로 다시 검색해 주세요."
                        ),
                        risk_level="unknown",
                        assessment_status="incomplete",
                        reasons=["저장된 제품 후보 재확인 실패"],
                        conversation_state={
                            "version": 1,
                            "last_intent": "missing_product",
                            "last_task_type": "product_chat",
                        },
                    ),
                )

    if product is None:
        if barcode or report_number:
            resolved_product, resolved_barcode, resolved_report_number = (
                _resolve_authoritative_product_reference({
                    "barcode": barcode,
                    "report_number": report_number,
                })
            )
            if resolved_product is None:
                return (
                    Product(
                        barcode=barcode,
                        report_number=report_number,
                        product_found=False,
                        facts_verified=False,
                    ),
                    barcode,
                    report_number,
                    None,
                )
            product = resolved_product
            barcode = resolved_barcode
            report_number = resolved_report_number
        elif conversation_state:
            recipe_followup = "레시피" in message and any(cue in message for cue in ("다른", "더", "또"))
            product, barcode, report_number = _resolve_authoritative_product_reference(
                conversation_state.get("recipe_product") if recipe_followup
                else conversation_state.get("pending_target_product") or conversation_state.get("last_confirmed_product")
            )
            if recipe_followup and product is not None:
                authoritative_request_reference_provided = True
    else:
        product = enrich_product_from_repository(
            product,
            barcode=explicit_barcode_reference,
            report_number=explicit_report_number_reference,
        )
        barcode = product.barcode or barcode
        report_number = product.report_number or report_number
        if product.product_found is False:
            return product, barcode, report_number, None

    # A barcode/report number supplied on this turn is stronger evidence than
    # wording that also resembles a general food question (for example,
    # "원재료와 알레르기 표시 알려줘"). Only inherited context may be
    # discarded by topic-shift detection.
    ignore_product_context = (
        not authoritative_request_reference_provided
        and not embedded_request_reference_provided
        and looks_like_general_topic_shift(
            message,
            profile,
            product,
            chat_history,
        )
    )

    if ignore_product_context:
        product = None
        barcode = None
        report_number = None
    else:
        explicit_product, confirmation_response = find_explicit_product_override(message, product)
        if confirmation_response is not None:
            return product, barcode, report_number, confirmation_response
        if explicit_product is not None:
            product = explicit_product
            barcode = product.barcode
            report_number = product.report_number
        elif (
            not authoritative_request_reference_provided
            and not embedded_request_reference_provided
        ):
            recent_history_product = find_recent_history_product_for_followup(message, product, chat_history)
            if recent_history_product is not None:
                product = recent_history_product
                barcode = product.barcode
                report_number = product.report_number

    if product is None:
        if product is None and not ignore_product_context:
            product, confirmation_response = find_product_from_request_text(
                message,
                chat_history,
                use_history=not looks_like_general_topic_shift(message, profile, None, chat_history),
            )
            if confirmation_response is not None:
                return product, barcode, report_number, confirmation_response

    if product is None and (report_number or barcode):
        product = Product(
            barcode=barcode,
            report_number=report_number,
            product_found=False,
        )

    if product is None and source_query:
        return None, None, None, missing_recipe_source_response(message, conversation_state)

    return product, barcode, report_number, None
