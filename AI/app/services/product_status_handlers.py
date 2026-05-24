from typing import Optional

from app.core.models import ChatResponse, Product, Profile
from app.services.recommended_questions import pick_recommended_questions
from app.services.response_helpers import build_response, format_structured_answer, format_summary_answer


def handle_missing_product(profile: Optional[Profile], product: Optional[Product], message: str) -> ChatResponse:
    identifier_reason = "DB에 등록된 제품 정보나 스캔 식별자 조회 결과가 확인되지 않았어요."
    next_steps = [
        "스캔한 품목보고번호가 맞는지 다시 확인해 주세요.",
        "제품명이나 품목보고번호를 함께 보내주면 등록 여부를 다시 찾아볼 수 있어요.",
        "원재료명과 영양성분표가 보이게 사진을 찍어 보내주면 확인에 도움이 돼요.",
    ]

    if product and product.barcode and not product.report_number:
        next_steps.insert(0, "바코드가 보이도록 다시 스캔해 주세요.")

    return build_response(
        intent="missing_product",
        answer=format_structured_answer(
            "현재는 이 제품 정보를 찾지 못해서 정확한 판단을 바로 드리기 어려워요.",
            [identifier_reason],
            ["제품 정보가 없으면 알레르기 성분, 원재료명, 영양성분을 기준으로 판단할 수 없어요."],
            next_steps,
        ),
        risk_level="unknown",
        reasons=["DB 미등록 제품"],
        profile=profile,
        recommended_questions=[
            "품목보고번호가 맞는지 다시 확인해줘",
            "제품명으로 먼저 찾아줘",
            "원재료명이나 영양성분표 사진 기준으로 확인해줘",
        ],
    )


def handle_product_summary(profile: Optional[Profile], product: Product, message: str) -> ChatResponse:
    name = product.product_name or "이 제품"
    ingredient_texts = product.ingredient_text_sources()
    ingredient_preview = ", ".join(ingredient_texts[:3]) if ingredient_texts else "원재료 정보 없음"

    extra_sentences = []

    if product.allergy_info:
        allergy_preview = ", ".join(product.allergy_info[:2])
        extra_sentences.append(f"알레르기 유발 성분으로는 {allergy_preview}가 확인돼요.")

    if "kcal" in product.nutrition:
        extra_sentences.append(f"열량은 {product.nutrition['kcal']}로 확인돼요.")

    extra_text = " ".join(extra_sentences)

    if not message.strip():
        return build_response(
            intent="product_summary",
            answer=format_summary_answer(
                f"{name} 스캔이 완료됐어요.",
                [f"먼저 보이는 핵심 정보는 {ingredient_preview}예요.", extra_text] if extra_text else [f"먼저 보이는 핵심 정보는 {ingredient_preview}예요."],
                ["원하시는 순서대로 먹어도 되는지, 알레르기 성분, 영양성분까지 이어서 바로 볼 수 있어요."],
            ),
            risk_level="low",
            reasons=["스캔 직후 제품 요약 제공"],
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
            f"{name} 정보를 먼저 간단히 정리해드릴게요.",
            [f"주요 원재료는 {ingredient_preview}예요.", extra_text] if extra_text else [f"주요 원재료는 {ingredient_preview}예요."],
            ["알레르기나 식이 기준으로 다시 보고 싶다면 '먹어도 괜찮은지 확인해 주세요'처럼 이어서 물어봐 주세요."],
        ),
        risk_level="low",
        reasons=["제품 요약 제공"],
        profile=profile,
        recommended_questions=pick_recommended_questions("product_summary", "low", product),
    )
