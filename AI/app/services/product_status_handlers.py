from typing import Optional

from app.core.models import ChatResponse, Product, Profile
from app.core.nutrition_basis import nutrition_basis_label
from app.core.product_provenance import (
    label_provenance_limitation_reason,
    label_provenance_supports,
)
from app.data.dictionaries import EATABILITY_KEYWORDS
from app.intent.text import contains_any, detect_ingredient_keyword, detect_nutrition_keyword, normalize_message
from app.services.recommended_questions import pick_recommended_questions
from app.services.response_helpers import build_response, format_structured_answer, format_summary_answer


BROAD_PRODUCT_CATEGORY_TERMS = [
    "우유",
    "두유",
    "과자",
    "음료",
    "간식",
    "라면",
    "치즈",
    "요거트",
    "빵",
]


def _broad_product_category_term(message: str) -> Optional[str]:
    text = normalize_message(message)
    if not contains_any(text, EATABILITY_KEYWORDS):
        return None

    for term in BROAD_PRODUCT_CATEGORY_TERMS:
        if term not in text:
            continue
        compact = text.replace(term, "")
        compact = compact.replace("먹어도", "").replace("돼", "").replace("되", "")
        compact = compact.replace("괜찮아", "").replace("괜찮을까", "")
        compact = compact.replace("?", "").strip()
        if not compact:
            return term

    return None


def _missing_product_guidance(message: str, product: Optional[Product]) -> tuple[str, list[str], list[str], list[str], list[str]]:
    text = normalize_message(message)
    broad_term = _broad_product_category_term(message)
    if broad_term:
        return (
            f"{broad_term} 제품은 종류가 많아서 특정 제품을 먼저 선택해야 해요.",
            ["제품군만으로는 원재료나 알레르기 표시가 제품마다 달라 정확히 판단하기 어려워요."],
            ["특정 제품이 정해지면 원재료, 알레르기 표시, 영양성분 기준으로 확인할 수 있어요."],
            [
                "제품명 전체나 바코드를 알려주세요. 예: 매일우유 오리지널 200ml",
                "품목보고번호가 있으면 함께 보내주면 더 정확하게 찾을 수 있어요.",
                f"일반 기준이 궁금하면 '{broad_term} 알레르기에서 어떤 성분을 봐야 해?'처럼 물어봐 주세요.",
            ],
            [
                "제품명으로 먼저 찾아줘",
                "바코드 기준으로 확인해줘",
                f"{broad_term} 알레르기 기준으로 어떤 성분을 봐야 해?",
            ],
        )

    if product and (product.barcode or product.report_number):
        identifier = "바코드" if product.barcode else "품목보고번호"
        return (
            f"입력된 {identifier}로는 제품 정보를 찾지 못했어요.",
            [f"DB에서 해당 {identifier}와 일치하는 제품이 확인되지 않았어요."],
            ["제품 정보가 없으면 원재료, 알레르기 표시, 영양성분을 기준으로 판단할 수 없어요."],
            [
                f"{identifier}가 정확한지 다시 확인해 주세요.",
                "제품명을 함께 보내주면 이름 기준으로 다시 찾아볼 수 있어요.",
                "현재 채팅은 사진을 직접 읽을 수 없으므로 제품명, 바코드 또는 품목보고번호로 DB 제품을 찾아주세요.",
            ],
            [
                "제품명으로 다시 찾아줘",
                f"{identifier}가 맞는지 다시 확인해줘",
                "제품명과 바코드로 다시 찾아줘",
            ],
        )

    nutrient = detect_nutrition_keyword(text)
    if nutrient is not None:
        return (
            f"{nutrient} 수치를 확인하려면 먼저 특정 제품이 필요해요.",
            ["제품마다 영양성분 수치가 달라 제품 없이 수치를 말하면 근거가 부족해요."],
            ["제품이 확인되면 DB에 있는 영양성분 값만 기준으로 설명할 수 있어요."],
            [
                f"제품명과 함께 '{nutrient} 함량 알려줘'처럼 물어봐 주세요.",
                "바코드를 스캔하면 해당 제품 기준으로 바로 확인할 수 있어요.",
            ],
            [
                f"제품명 기준으로 {nutrient} 함량 알려줘",
                "바코드 기준으로 영양성분 확인해줘",
                "다른 제품 식별자로 영양성분 확인해줘",
            ],
        )

    ingredient = detect_ingredient_keyword(text)
    if ingredient is not None or contains_any(text, ["성분", "원재료", "알레르기", "알러지"]):
        target = ingredient or "해당 성분"
        return (
            f"{target} 포함 여부를 확인하려면 먼저 특정 제품이 필요해요.",
            ["성분과 알레르기 표시는 제품마다 달라 제품 없이 포함 여부를 단정할 수 없어요."],
            ["제품이 확인되면 원재료명과 알레르기 표시 기준으로만 답변할 수 있어요."],
            [
                f"제품명과 함께 '{target} 들어 있어?'처럼 물어봐 주세요.",
                "바코드를 스캔하면 해당 제품 기준으로 바로 확인할 수 있어요.",
            ],
            [
                f"제품명 기준으로 {target} 들어 있는지 확인해줘",
                "바코드 기준으로 알레르기 성분 확인해줘",
                "다른 제품 식별자로 성분 확인해줘",
            ],
        )

    if contains_any(text, ["이거", "이건", "그거", "그건", "이 제품", "그 제품"]):
        return (
            "지금 말한 제품이 어떤 제품인지 먼저 확인해야 해요.",
            ["대화에 연결된 제품 정보나 스캔 정보가 없어 '이 제품'이 무엇인지 알 수 없어요."],
            ["제품이 확인되면 원재료, 알레르기 표시, 영양성분 기준으로 판단할 수 있어요."],
            [
                "제품명을 입력하거나 바코드를 스캔해 주세요.",
                "방금 본 제품이라면 후보에서 제품을 선택한 뒤 다시 질문해 주세요.",
            ],
            [
                "제품명으로 먼저 찾아줘",
                "바코드 기준으로 확인해줘",
                "이 제품 원재료명 기준으로 확인해줘",
            ],
        )

    return (
        "현재는 어떤 제품을 기준으로 봐야 하는지 확인이 필요해요.",
        ["제품명, 바코드, 품목보고번호 중 확인 가능한 정보가 아직 부족해요."],
        ["제품 정보가 없으면 알레르기 성분, 원재료명, 영양성분을 기준으로 판단할 수 없어요."],
        [
            "제품명을 조금 더 구체적으로 알려주세요.",
            "바코드나 품목보고번호를 함께 보내주면 더 정확하게 찾을 수 있어요.",
            "현재 채팅은 사진을 직접 읽을 수 없으므로 제품명, 바코드 또는 품목보고번호로 DB 제품을 찾아주세요.",
        ],
        [
            "제품명으로 먼저 찾아줘",
            "바코드 기준으로 확인해줘",
            "제품명이나 품목보고번호로 다시 찾아줘",
        ],
    )


def handle_missing_product(profile: Optional[Profile], product: Optional[Product], message: str) -> ChatResponse:
    conclusion, reasons, limitations, next_steps, recommended_questions = _missing_product_guidance(message, product)

    return build_response(
        intent="missing_product",
        answer=format_structured_answer(
            conclusion,
            reasons,
            limitations,
            next_steps,
        ),
        risk_level="unknown",
        reasons=reasons,
        profile=profile,
        recommended_questions=recommended_questions,
    )


def build_unverified_product_response(
    intent: str,
    profile: Optional[Profile],
) -> ChatResponse:
    """Return a fact-free response for client-supplied, unverified products.

    Product fields supplied in the request are useful only as lookup hints.  They
    must not become evidence for ingredients, allergens, nutrition, comparisons,
    or recommendations until the repository resolves an authoritative record.
    """
    return build_response(
        intent=intent,
        answer=format_structured_answer(
            "현재 전달된 정보만으로는 이 제품의 성분이나 영양정보를 확인해드릴 수 없어요.",
            ["요청에 포함된 제품 정보가 DB의 제품 기록으로 확인되지 않았어요."],
            [
                "전달된 원재료, 알레르기 표시, 영양 수치는 검증 전 정보라 "
                "판단이나 요약 근거로 사용하지 않았어요."
            ],
            [
                "바코드나 품목보고번호를 확인해 다시 보내주거나, "
                "DB에서 제품을 검색해 선택한 뒤 질문해 주세요."
            ],
        ),
        risk_level="unknown",
        reasons=["요청 제공 제품 정보(DB 미검증)"],
        profile=profile,
        assessment_status="incomplete",
        recommended_questions=[
            "바코드 기준으로 제품을 다시 확인해줘",
            "품목보고번호 기준으로 제품을 다시 찾아줘",
            "제품명으로 DB에서 검색해줘",
        ],
    )


def handle_product_summary(profile: Optional[Profile], product: Product, message: str) -> ChatResponse:
    if not product.facts_verified:
        return build_unverified_product_response("product_summary", profile)

    name = product.product_name or "이 제품"
    ingredient_texts = product.ingredient_text_sources()
    ingredient_preview = ", ".join(ingredient_texts[:3]) if ingredient_texts else "원재료 정보 없음"

    extra_sentences = []

    resolved_allergies = product.resolved_allergies()
    if resolved_allergies:
        allergy_preview = ", ".join(resolved_allergies[:2])
        extra_sentences.append(
            f"DB에 등록된 알레르기 표시에는 {allergy_preview}가 확인돼요."
        )

    if "kcal" in product.nutrition:
        extra_sentences.append(
            f"열량은 {product.nutrition['kcal']}kcal"
            f"({nutrition_basis_label(product)})로 확인돼요."
        )

    extra_text = " ".join(extra_sentences)
    trace_limitations = []
    if (ingredient_texts or product.resolved_allergies()) and not (
        label_provenance_supports(product, ("ingredients", "allergens"))
    ):
        trace_limitations.append(label_provenance_limitation_reason(
            product,
            ("ingredients", "allergens"),
        ))
    if product.nutrition and not label_provenance_supports(
        product,
        ("nutrition", "nutrition_basis"),
    ):
        trace_limitations.append(label_provenance_limitation_reason(
            product,
            ("nutrition", "nutrition_basis"),
        ))
    trace_limitations = list(dict.fromkeys(trace_limitations))
    observation_note = (
        "표시 관찰 기록은 "
        + product.label_provenance.label_observed_at.isoformat()
        + "이며 현재 판매 포장과 같다는 보장은 아니에요."
        if product.label_provenance.label_observed_at is not None
        else (
            "표시 관찰일은 확인되지 않았고, 현재 판매 포장과 "
            "같다고 볼 수 없어요."
        )
    )
    trace_note = " ".join([*trace_limitations, observation_note])
    closing_note = (
        "출처 추적 제한을 해결하기 전에는 성분이 없다거나 식이 조건에 맞는다고 판단하지 않아요."
        if trace_limitations
        else "이 요약만으로 개인별 섭취 안전성을 판단한 것은 아니에요."
    )

    if not message.strip():
        return build_response(
            intent="product_summary",
            answer=format_summary_answer(
                f"{name} 스캔이 완료됐어요.",
                [f"DB에 등록된 표시 정보의 핵심 내용은 {ingredient_preview}예요.", extra_text, trace_note],
                [closing_note],
            ),
            risk_level="unknown",
            assessment_status="incomplete",
            reasons=["스캔 직후 제품 요약 제공", "섭취 안전성 미평가"],
            profile=profile,
            recommended_questions=[
                "이 제품이 먹어도 괜찮은지 확인해줘",
                "알레르기 유발 성분만 따로 정리해줘",
                "영양성분은 어떤 편인지 설명해줘",
            ],
        )

    return build_response(
        intent="product_summary",
        answer=format_summary_answer(
            f"DB에 등록된 {name} 표시 정보를 간단히 정리해드릴게요.",
            [f"등록된 주요 원재료는 {ingredient_preview}예요.", extra_text, trace_note],
            [closing_note],
        ),
        risk_level="unknown",
        assessment_status="incomplete",
        reasons=["제품 요약 제공", "섭취 안전성 미평가"],
        profile=profile,
        recommended_questions=pick_recommended_questions("product_summary", "unknown", product),
    )
