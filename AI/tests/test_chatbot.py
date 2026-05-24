import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from importlib.util import find_spec
from unittest import SkipTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.adapters import normalize_active_profiles, normalize_chat_request
from app.services.activity import list_user_activities, log_chat_activity, set_activity_repository
from app.intent.classifier import classify_intent, classify_intent_result
from app.infrastructure.llm import get_llm_model, get_llm_provider, is_llm_enabled, set_llm_client
from app.core.models import ChatHistoryItem, ChatRequest, Profile, Product
from app.main import chat
from app.prompts.builder import build_general_fallback_prompt, build_rule_explainer_prompt
from app.intent.text import is_context_dependent_followup
from app.services.product import (
    find_product_by_barcode,
    find_product_by_report_number,
    find_product_by_text,
    find_product_from_history,
    recommend_alternative_products,
    set_product_repository,
)
from app.infrastructure.rag import get_rag_context, reset_rag_cache
from app.infrastructure.rag import get_knowledge_dir
from app.infrastructure.repository import (
    InMemoryActivityRepository,
    InMemoryProductRepository,
    SQLiteActivityRepository,
    SQLiteProductRepository,
)
from app.services.chat import generate_chat_response


def build_base_product() -> Product:
    return Product(
        product_id=101,
        product_name="고단백 두유바",
        rawmtrl_nm="분리대두단백, 설탕, 아몬드, 정제소금",
        allergy=["대두", "견과류"],
        nutrition={
            "kcal": 320,
            "carbohydrate": 31,
            "sugars": 14,
            "protein": 12,
            "fat": 18,
            "sodium": 450,
        },
        barcode="8800000000000",
        report_number="202400000002",
        product_found=True,
    )


def build_base_profile() -> Profile:
    return Profile(
        profile_id=1,
        profile_name="나",
        user_id=1,
        allergies=["우유"],
        user_allergies=["대두"],
        conditions=["당뇨"],
        special_diets=["저염식"],
        disliked_ingredients=["아몬드"],
        target_type="self",
    )


def _rag_test_available() -> bool:
    return all([
        bool(os.getenv("OPENAI_API_KEY")),
        find_spec("langchain_chroma") is not None,
        find_spec("langchain_openai") is not None,
        find_spec("langchain_community") is not None,
        find_spec("langchain_text_splitters") is not None,
    ])


def _require_rag_test_env() -> None:
    if not _rag_test_available():
        raise SkipTest("RAG optional dependencies or OPENAI_API_KEY are unavailable.")


def test_default_rag_knowledge_dir_points_to_app_knowledge():
    knowledge_dir = get_knowledge_dir()

    assert knowledge_dir.name == "knowledge"
    assert knowledge_dir.parent.name == "app"
    assert knowledge_dir.exists()


def test_can_i_eat_aggregates_multiple_risks():
    result = generate_chat_response(
        profile=build_base_profile(),
        product=build_base_product(),
        message="이거 먹어도 돼?",
    )

    assert result.intent == "can_i_eat"
    assert result.task_type == "product_chat"
    assert result.pipeline_stage == "rules"
    assert result.risk_level == "high"
    assert "피하는 편" in result.answer or "안전해 보여요" in result.answer
    assert any("대두" in reason for reason in result.reasons)
    assert any("저염식" in reason or "나트륨" in reason for reason in result.reasons)
    assert any("혈당" in reason or "탄수화물" in reason or "당류" in reason for reason in result.reasons)


def test_profile_based_recheck_is_not_lost():
    result = generate_chat_response(
        profile=build_base_profile(),
        product=build_base_product(),
        message="엄마 기준으로는?",
    )

    assert result.intent == "profile_based_recheck"
    assert result.risk_level == "unknown"
    assert "다시 해석" in result.answer or "프로필" in result.answer


def test_vegan_diet_conflict_uses_db_fields():
    profile = Profile(
        profile_id=2,
        profile_name="비건 사용자",
        special_diet=["비건"],
    )
    product = Product(
        product_id=202,
        product_name="밀크 쿠키",
        rawmtrl_nm="밀가루, 유청분말, 버터, 설탕",
        allergy=["우유", "밀"],
        nutrition={"kcal": 180},
        product_found=True,
    )

    result = generate_chat_response(
        profile=profile,
        product=product,
        message="비건인데 먹어도 돼?",
    )

    assert result.intent == "can_i_eat"
    assert result.risk_level == "high"
    assert "비건" in result.answer
    assert "유청" in result.answer or "우유" in result.answer


def test_ingredient_check_reads_raw_material_text():
    product = Product(
        product_id=303,
        product_name="크림 스프",
        rawmtrl_nm="감자분말, 유청분말, 양파분말",
        product_found=True,
    )

    result = generate_chat_response(
        profile=None,
        product=product,
        message="우유 들어 있어?",
    )

    assert result.intent == "ingredient_check"
    assert result.task_type == "product_chat"
    assert result.risk_level == "medium"
    assert "유청" in result.answer or "우유" in result.answer


def test_product_summary_behaves_like_product_chat():
    result = generate_chat_response(
        profile=None,
        product=build_base_product(),
        message="설명해줘",
    )

    assert result.intent == "product_summary"
    assert result.task_type == "product_chat"
    assert "핵심 정보" in result.answer or "주요 원재료" in result.answer


def test_scan_immediate_product_summary_uses_scan_intro_and_recommended_questions():
    result = generate_chat_response(
        profile=None,
        product=build_base_product(),
        message="",
    )

    assert result.intent == "product_summary"
    assert result.task_type == "product_chat"
    assert "스캔이 완료됐어요" in result.answer
    assert len(result.recommended_questions) >= 3
    assert any("먹어도" in question for question in result.recommended_questions)


def test_general_text_question_behaves_like_general_chat():
    result = generate_chat_response(
        profile=None,
        product=None,
        message="저염식이면 뭘 먼저 봐야 해?",
    )

    assert result.intent == "general_diet_guide"
    assert result.task_type == "general_chat"
    assert result.pipeline_stage == "rules"
    assert "나트륨" in result.answer


def test_general_allergy_question_behaves_like_general_chat():
    result = generate_chat_response(
        profile=None,
        product=None,
        message="알레르기 있으면 어떤 성분을 조심해야 해?",
    )

    assert result.intent == "general_allergy_guide"
    assert result.task_type == "general_chat"
    assert "알레르기" in result.answer


def test_missing_product_guides_user_to_rescan_or_send_details():
    product = Product(
        barcode="9999999999999",
        product_found=False,
    )

    result = generate_chat_response(
        profile=None,
        product=product,
        message="이거 먹어도 돼?",
    )

    assert result.intent == "missing_product"
    assert result.risk_level == "unknown"
    assert "다시 스캔" in result.answer or "사진" in result.answer


def test_can_i_eat_does_not_overstate_safety_without_allergy_label():
    product = Product(
        product_id=404,
        product_name="성분표 일부만 있는 제품",
        rawmtrl_nm="감자전분, 설탕",
        allergy=[],
        nutrition={},
        product_found=True,
    )

    result = generate_chat_response(
        profile=None,
        product=product,
        message="이거 먹어도 돼?",
    )

    assert result.intent == "can_i_eat"
    assert result.risk_level == "unknown"
    assert "알레르기 표시 정보가 없어" in result.answer


def test_can_i_eat_rule_response_includes_recommended_questions():
    result = generate_chat_response(
        profile=None,
        product=build_base_product(),
        message="이거 먹어도 돼?",
    )

    assert result.intent == "can_i_eat"
    assert result.answer_source == "rules"
    assert len(result.recommended_questions) >= 1
    assert any("성분" in question or "영양" in question for question in result.recommended_questions)


def test_recommended_questions_are_user_utterances():
    result = generate_chat_response(
        profile=Profile(profile_id=1, profile_name="나", special_diet="저염식"),
        product=build_base_product(),
        message="이거 먹어도 돼?",
    )
    forbidden_phrases = ["볼까요", "드릴까요", "물어보실까요", "확인해볼까요", "쉬우실까요"]

    assert result.recommended_questions
    assert all(
        phrase not in question
        for question in result.recommended_questions
        for phrase in forbidden_phrases
    )
    assert any(question.endswith(("줘", "알려줘", "설명해줘", "확인해줘")) for question in result.recommended_questions)


def test_rule_response_stays_rule_based_when_llm_is_disabled():
    set_llm_client(None)

    result = generate_chat_response(
        profile=None,
        product=build_base_product(),
        message="설명해줘",
    )

    assert result.intent == "product_summary"
    assert result.answer_source == "rules"
    assert result.pipeline_stage == "rules"


def test_general_unknown_can_use_llm_fallback_safely():
    set_llm_client(lambda prompt: "일반적인 기준으로는 성분표와 영양정보를 함께 보는 것이 좋아요.")

    try:
        result = generate_chat_response(
            profile=None,
            product=None,
            message="건강하게 고르려면 어떻게 봐?",
        )

        assert result.task_type == "general_chat"
        assert result.answer_source in {"llm", "rag"}
        assert result.pipeline_stage in {"llm", "rag"}
        assert "성분표" in result.answer
    finally:
        set_llm_client(None)


def test_rule_prompt_includes_intent_specific_style_guidance():
    response = generate_chat_response(
        profile=None,
        product=build_base_product(),
        message="이거 먹어도 돼?",
    )

    prompt = build_rule_explainer_prompt(
        message="이거 먹어도 돼?",
        rule_response=response,
        profile=None,
        product=build_base_product(),
    )

    assert "intent별 말투 가이드" in prompt
    assert "첫 문장에서 바로 먹어도 되는지 결론을 말해라." in prompt


def test_general_prompt_includes_conversational_guidance():
    prompt = build_general_fallback_prompt(
        message="건강하게 고르려면 어떻게 봐?",
        profile=None,
    )

    assert "답변 말투 가이드" in prompt
    assert "확인할 기준은 2~3개 정도만 골라 쉽게 설명해라." in prompt


def test_unknown_intent_can_be_recovered_by_llm_classification():
    set_llm_client(lambda prompt: '{"intent":"general_food_health"}')

    try:
        result = generate_chat_response(
            profile=None,
            product=None,
            message="브랜드 비교 어떻게 해?",
        )

        assert result.intent == "general_food_health"
        assert result.task_type == "general_chat"
        assert result.answer_source in {"llm", "rag"}
        assert result.pipeline_stage in {"llm", "rag"}
    finally:
        set_llm_client(None)


def test_chat_history_is_included_in_llm_intent_fallback():
    def fake_llm(prompt: str) -> str:
        assert "이전 대화" in prompt
        assert "사용자: 저염식이면 뭘 먼저 봐야 해?" in prompt
        return '{"intent":"general_diet_guide"}'

    set_llm_client(fake_llm)

    try:
        intent = classify_intent(
            message="그럼?",
            product=None,
            chat_history=[
                ChatHistoryItem(role="user", content="저염식이면 뭘 먼저 봐야 해?"),
                ChatHistoryItem(role="assistant", content="나트륨을 먼저 보는 것이 좋아요."),
            ],
        )

        assert intent == "general_diet_guide"
    finally:
        set_llm_client(None)


def test_short_followup_message_is_detected_as_context_dependent():
    detected = is_context_dependent_followup(
        "그럼?",
        [
            ChatHistoryItem(role="user", content="저염식이면 뭘 먼저 봐야 해?"),
            ChatHistoryItem(role="assistant", content="나트륨을 먼저 보는 것이 좋아요."),
        ],
    )

    assert detected is True


def test_product_followup_inherits_previous_health_question_when_llm_is_disabled():
    set_llm_client(None)

    intent_result = classify_intent_result(
        message="그럼?",
        product=build_base_product(),
        profile=Profile(
            profile_id=31,
            profile_name="민지",
            conditions=["당뇨"],
        ),
        chat_history=[
            ChatHistoryItem(role="user", content="당뇨인데 이거 괜찮아?"),
            ChatHistoryItem(role="assistant", content="혈당 관리 기준으로는 조금 주의해서 보시는 게 좋아요."),
        ],
    )

    result = generate_chat_response(
        profile=Profile(
            profile_id=31,
            profile_name="민지",
            conditions=["당뇨"],
        ),
        product=build_base_product(),
        message="그럼?",
        chat_history=[
            ChatHistoryItem(role="user", content="당뇨인데 이거 괜찮아?"),
            ChatHistoryItem(role="assistant", content="혈당 관리 기준으로는 조금 주의해서 보시는 게 좋아요."),
        ],
    )

    assert intent_result.intent == "health_risk_check"
    assert intent_result.source == "followup_context"
    assert result.intent == "health_risk_check"
    assert result.task_type == "product_chat"
    assert "혈당" in result.answer or "당류" in result.answer


def test_product_followup_inherits_previous_summary_question():
    set_llm_client(None)

    intent_result = classify_intent_result(
        message="이건?",
        product=build_base_product(),
        profile=None,
        chat_history=[
            ChatHistoryItem(role="user", content="이 제품 설명해줘"),
            ChatHistoryItem(role="assistant", content="제품 정보를 먼저 간단히 정리해드릴게요."),
        ],
    )

    result = generate_chat_response(
        profile=None,
        product=build_base_product(),
        message="이건?",
        chat_history=[
            ChatHistoryItem(role="user", content="이 제품 설명해줘"),
            ChatHistoryItem(role="assistant", content="제품 정보를 먼저 간단히 정리해드릴게요."),
        ],
    )

    assert intent_result.intent == "product_summary"
    assert intent_result.source == "followup_context"
    assert result.intent == "product_summary"
    assert result.task_type == "product_chat"


def test_product_followup_can_use_previous_assistant_answer_axis():
    set_llm_client(None)

    result = classify_intent_result(
        message="그건요?",
        product=build_base_product(),
        profile=None,
        chat_history=[
            ChatHistoryItem(role="user", content="이건?"),
            ChatHistoryItem(role="assistant", content="나트륨이 특별히 높은 편으로 보이진 않아요. 현재 확인된 나트륨 수치는 420이에요."),
        ],
    )

    assert result.intent == "nutrition_explain"
    assert result.source == "followup_context"


def test_product_followup_with_specific_nutrient_uses_product_context():
    set_llm_client(None)

    result = generate_chat_response(
        profile=None,
        product=build_base_product(),
        message="나트륨은?",
        chat_history=[
            ChatHistoryItem(role="user", content="이거 먹어도 돼?"),
            ChatHistoryItem(role="assistant", content="저염식 기준으로 나트륨을 같이 확인해야 해요."),
        ],
    )

    assert result.intent == "nutrition_explain"
    assert result.task_type == "product_chat"
    assert "나트륨" in result.answer


def test_product_followup_with_specific_ingredient_uses_product_context():
    set_llm_client(None)

    result = generate_chat_response(
        profile=None,
        product=build_base_product(),
        message="대두는?",
        chat_history=[
            ChatHistoryItem(role="user", content="이 제품 알레르기 성분 알려줘"),
            ChatHistoryItem(role="assistant", content="알레르기 표시에서 대두가 확인돼요."),
        ],
    )

    assert result.intent == "ingredient_check"
    assert result.task_type == "product_chat"
    assert "대두" in result.answer


def test_product_followup_with_vague_other_uses_previous_ingredient_axis():
    set_llm_client(None)

    result = generate_chat_response(
        profile=None,
        product=build_base_product(),
        message="다른 건?",
        chat_history=[
            ChatHistoryItem(role="user", content="이 제품 알레르기 성분 알려줘"),
            ChatHistoryItem(role="assistant", content="알레르기 표시에서 대두가 확인돼요."),
        ],
    )

    assert result.intent == "ingredient_check"
    assert result.task_type == "product_chat"
    assert "알레르기" in result.answer


def test_product_followup_with_eatability_phrase_stays_product_chat():
    set_llm_client(None)

    result = generate_chat_response(
        profile=Profile(profile_id=31, profile_name="민지", conditions=["당뇨"]),
        product=build_base_product(),
        message="그럼 먹어도 괜찮아?",
        chat_history=[
            ChatHistoryItem(role="user", content="나트륨은?"),
            ChatHistoryItem(role="assistant", content="나트륨 수치는 420이에요."),
        ],
    )

    assert result.intent == "can_i_eat"
    assert result.task_type == "product_chat"


def test_clear_product_eatability_question_stays_rule_based():
    set_llm_client(lambda prompt: '{"intent":"health_risk_check"}')

    try:
        intent = classify_intent(
            message="이 제품 먹어도 돼?",
            product=build_base_product(),
            profile=None,
        )

        assert intent == "can_i_eat"
    finally:
        set_llm_client(None)


def test_classify_intent_result_exposes_rule_source():
    result = classify_intent_result(
        message="이 제품 먹어도 돼?",
        product=build_base_product(),
        profile=None,
    )

    assert result.intent == "can_i_eat"
    assert result.source == "rules"
    assert result.rule_intent == "can_i_eat"
    assert result.llm_intent == "unknown"


def test_ambiguous_product_followup_uses_llm_intent_fallback():
    set_llm_client(lambda prompt: '{"intent":"product_summary"}')

    try:
        result = generate_chat_response(
            profile=None,
            product=build_base_product(),
            message="이건 어때?",
        )

        assert result.intent == "product_summary"
        assert result.task_type == "product_chat"
        assert result.intent != "unknown"
    finally:
        set_llm_client(None)


def test_profile_or_health_dependent_product_question_uses_llm_fallback():
    def fake_llm(prompt: str) -> str:
        assert "현재 프로필명: 민지" in prompt
        assert "질환/건강 상태: 당뇨" in prompt
        return '{"intent":"health_risk_check"}'

    set_llm_client(fake_llm)

    try:
        intent = classify_intent(
            message="민지 기준으로 당뇨 있는데 이거 괜찮아?",
            product=build_base_product(),
            profile=Profile(
                profile_id=7,
                profile_name="민지",
                user_id=7,
                conditions=["당뇨"],
            ),
        )

        assert intent == "health_risk_check"
    finally:
        set_llm_client(None)


def test_classify_intent_result_exposes_llm_fallback_source():
    set_llm_client(lambda prompt: '{"intent":"profile_based_recheck"}')

    try:
        result = classify_intent_result(
            message="민지 기준으로는?",
            product=build_base_product(),
            profile=Profile(
                profile_id=8,
                profile_name="민지",
                user_id=8,
                conditions=["당뇨"],
            ),
        )

        assert result.intent == "profile_based_recheck"
        assert result.source == "llm_fallback"
        assert result.rule_intent == "ambiguous"
        assert result.llm_intent == "profile_based_recheck"
    finally:
        set_llm_client(None)


def test_llm_intent_parser_rejects_freeform_text():
    set_llm_client(lambda prompt: "아마 can_i_eat 같아요.")

    try:
        intent = classify_intent(
            message="브랜드 비교 어떻게 해?",
            product=None,
        )

        assert intent == "unknown"
    finally:
        set_llm_client(None)


def test_llm_intent_parser_rejects_disallowed_intent():
    set_llm_client(lambda prompt: '{"intent":"random_other"}')

    try:
        intent = classify_intent(
            message="브랜드 비교 어떻게 해?",
            product=None,
        )

        assert intent == "unknown"
    finally:
        set_llm_client(None)


def test_product_chat_can_be_rewritten_by_llm_without_changing_intent():
    set_llm_client(
        lambda prompt: "이 제품은 대두와 견과류가 보여서 알레르기나 식이 기준에 따라 주의가 필요해요."
    )

    try:
        result = generate_chat_response(
            profile=build_base_profile(),
            product=build_base_product(),
            message="이거 먹어도 돼?",
        )

        assert result.intent == "can_i_eat"
        assert result.task_type == "product_chat"
        assert result.answer_source in {"hybrid", "rag"}
        assert result.pipeline_stage in {"llm", "rag"}
        assert "대두" in result.answer
    finally:
        set_llm_client(None)


def test_llm_env_helpers_work():
    old_provider = os.environ.get("LLM_PROVIDER")
    old_llm_model = os.environ.get("LLM_MODEL")
    old_model = os.environ.get("OPENAI_MODEL")
    old_openai_api_key = os.environ.get("OPENAI_API_KEY")

    try:
        os.environ["LLM_PROVIDER"] = "openai"
        os.environ.pop("LLM_MODEL", None)
        os.environ["OPENAI_MODEL"] = "gpt-4o-mini"
        os.environ["OPENAI_API_KEY"] = "test-key"

        assert get_llm_provider() == "openai"
        assert get_llm_model() == "gpt-4o-mini"
        assert is_llm_enabled() is True
    finally:
        if old_provider is None:
            os.environ.pop("LLM_PROVIDER", None)
        else:
            os.environ["LLM_PROVIDER"] = old_provider

        if old_llm_model is None:
            os.environ.pop("LLM_MODEL", None)
        else:
            os.environ["LLM_MODEL"] = old_llm_model

        if old_model is None:
            os.environ.pop("OPENAI_MODEL", None)
        else:
            os.environ["OPENAI_MODEL"] = old_model

        if old_openai_api_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = old_openai_api_key


def test_rag_context_returns_relevant_diet_guidance():
    _require_rag_test_env()
    reset_rag_cache()

    context = get_rag_context(
        message="저염식이면 뭘 먼저 봐야 해?",
        profile=None,
        product=None,
    )
    if not context:
        raise SkipTest("RAG context unavailable in the current network/API environment.")

    assert context
    assert any("나트륨" in chunk for chunk in context)


def test_rag_context_uses_profile_and_product_terms():
    _require_rag_test_env()
    reset_rag_cache()

    context = get_rag_context(
        message="이거 먹어도 돼?",
        profile=build_base_profile(),
        product=build_base_product(),
    )
    if not context:
        raise SkipTest("RAG context unavailable in the current network/API environment.")

    assert context
    assert any("대두" in chunk or "당류" in chunk or "나트륨" in chunk for chunk in context)


def test_raw_db_payload_is_normalized_before_chat():
    req = ChatRequest(
        raw_profile={
            "user_id": 7,
            "nickname": "민지",
            "user_allergies": "우유, 대두",
            "special_diet": "저염식",
            "disliked_ingredients": "고수",
        },
        raw_product={
            "식품명": "저염 두부스낵",
            "식품코드": "F0001",
            "바코드": "8801234567890",
            "RAWMTRL_NM": "분리대두단백, 감자전분, 정제소금",
            "allergy": "대두",
            "나트륨": "420mg",
            "에너지": "180kcal",
        },
        message="이거 먹어도 돼?",
        report_number="202400000001",
    )

    profile, product, barcode, report_number = normalize_chat_request(req)

    assert profile is not None
    assert product is not None
    assert profile.profile_name == "민지"
    assert "대두" in profile.resolved_allergies()
    assert product.product_name == "저염 두부스낵"
    assert product.food_code == "F0001"
    assert product.nutrition["sodium"] == 420.0
    assert barcode == "8801234567890"
    assert report_number == "202400000001"

    result = generate_chat_response(profile=profile, product=product, message=req.message)
    assert result.intent == "can_i_eat"
    assert result.risk_level == "high"
    assert "대두" in result.answer


def test_string_false_found_marks_product_not_found():
    product = Product.model_validate({
        "productName": "미등록 제품",
        "found": "false",
    })
    assert product.product_found is False

    req = ChatRequest(
        raw_product={
            "productName": "미등록 제품",
            "found": "false",
        },
        message="이거 먹어도 돼?",
    )

    _, normalized_product, _, _ = normalize_chat_request(req)
    assert normalized_product is not None
    assert normalized_product.product_found is False


def test_spec_style_profile_payload_is_accepted():
    profile = Profile.model_validate({
        "id": 101,
        "email": "test@gmail.com",
        "nickname": "테스터",
        "allergies": "우유, 대두",
        "diseases": "당뇨",
        "specialDiet": "저염식",
        "dislikedIngredients": "아몬드",
    })

    assert profile.profile_id == 101
    assert profile.profile_name == "테스터"
    assert profile.user_id == 101
    assert "우유" in profile.resolved_allergies()
    assert "대두" in profile.resolved_allergies()
    assert "당뇨" in profile.conditions
    assert "저염식" in profile.resolved_special_diets()
    assert "아몬드" in profile.disliked_ingredients


def test_spec_style_product_payload_is_accepted():
    product = Product.model_validate({
        "reportNo": "20100461303137",
        "barcode": "8809027555270",
        "productName": "커널스슈퍼믹스팝콘",
        "manufacturer": "제조사",
        "allergy": "우유, 대두",
        "rawMaterials": "옥수수, 설탕, 유청분말",
        "nutrientText": "열량 250kcal, 탄수화물 30g, 당류 12g, 나트륨 300mg",
        "imageUrl": "https://example.com/popcorn.png",
        "source": "spring",
        "energyKcal": 250,
        "carbsG": 30,
        "proteinG": 5,
        "fatG": 10,
        "sugarG": 12,
        "sodiumMg": 300,
        "cholesterolMg": 1,
        "allergyWarning": "우유 함유",
    })

    assert product.report_number == "20100461303137"
    assert product.product_name == "커널스슈퍼믹스팝콘"
    assert product.barcode == "8809027555270"
    assert product.manufacturer_name == "제조사"
    assert "우유" in product.resolved_allergies()
    assert "대두" in product.resolved_allergies()
    assert "유청분말" in product.rawmtrl_nm
    assert product.nutrition["kcal"] == 250.0
    assert product.nutrition["carbohydrate"] == 30.0
    assert product.nutrition["protein"] == 5.0
    assert product.nutrition["fat"] == 10.0
    assert product.nutrition["sugars"] == 12.0
    assert product.nutrition["sodium"] == 300.0
    assert product.nutrition["cholesterol"] == 1.0
    assert product.nutrient_text.startswith("열량")
    assert product.allergy_warning == "우유 함유"


def test_chat_request_accepts_api_spec_style_profile_and_product_keys():
    req = ChatRequest(
        profile={
            "id": 1,
            "nickname": "테스터",
            "allergies": "우유, 대두",
            "diseases": "당뇨",
            "specialDiet": "저염식",
            "dislikedIngredients": "아몬드",
        },
        product={
            "reportNo": "202400000001",
            "barcode": "8801234567890",
            "productName": "저염 두부스낵",
            "allergy": "대두",
            "rawMaterialName": "분리대두단백, 감자전분, 정제소금",
            "sodium": "420mg",
            "energy": "180kcal",
        },
        message="이거 먹어도 돼?",
    )

    profile, product, barcode, report_number = normalize_chat_request(req)

    assert profile is not None
    assert product is not None
    assert profile.profile_name == "테스터"
    assert "당뇨" in profile.conditions
    assert "저염식" in profile.resolved_special_diets()
    assert product.product_name == "저염 두부스낵"
    assert product.report_number == "202400000001"
    assert product.nutrition["sodium"] == 420.0
    assert barcode == "8801234567890"
    assert report_number == "202400000001"


def test_chat_accepts_backend_product_dto_shape():
    backend_product_dto = {
        "reportNo": "202400000777",
        "barcode": "8801111222233",
        "productName": "백엔드 DTO 두유스낵",
        "allergy": "대두",
        "rawMaterialName": "분리대두단백, 현미, 정제소금",
        "energy": "195kcal",
        "sodium": "430mg",
    }

    response = chat(
        ChatRequest(
            profile=build_base_profile(),
            product=backend_product_dto,
            message="이거 먹어도 돼?",
        )
    )

    assert response.task_type == "product_chat"
    assert response.intent == "can_i_eat"
    assert response.current_product is not None
    assert response.current_product["product_name"] == "백엔드 DTO 두유스낵"
    assert response.current_product["report_number"] == "202400000777"
    assert response.current_product["barcode"] == "8801111222233"
    assert response.current_product["nutrition"]["kcal"] == 195.0
    assert response.current_product["nutrition"]["sodium"] == 430.0
    assert any("대두" in reason for reason in response.reasons)


def test_only_active_profiles_are_used_from_profile_list():
    req = ChatRequest(
        profiles=[
            Profile(
                profile_id=1,
                profile_name="코워커",
                user_id=1,
                user_allergies=["대두"],
                is_active=True,
            ),
            Profile(
                profile_id=2,
                profile_name="김철수",
                user_id=1,
                conditions=["당뇨"],
                is_active=True,
            ),
            Profile(
                profile_id=3,
                profile_name="김아기",
                user_id=1,
                user_allergies=["우유"],
                is_active=False,
            ),
        ],
        message="이거 먹어도 돼?",
    )

    active_profiles = normalize_active_profiles(req)

    assert [profile.profile_name for profile in active_profiles] == ["코워커", "김철수"]


def test_multi_profile_can_i_eat_combines_each_profile_result():
    profiles = [
        Profile(
            profile_id=1,
            profile_name="코워커",
            user_id=1,
            user_allergies=["대두"],
            is_active=True,
        ),
        Profile(
            profile_id=2,
            profile_name="김철수",
            user_id=1,
            is_active=True,
        ),
    ]

    result = generate_chat_response(
        profile=profiles[0],
        active_profiles=profiles,
        product=build_base_product(),
        message="이거 먹어도 돼?",
    )

    assert result.intent == "can_i_eat"
    assert result.risk_level == "high"
    assert result.active_profile_names == ["코워커", "김철수"]
    assert len(result.per_profile_results) == 2
    assert result.per_profile_results[0]["profile_name"] == "코워커"
    assert result.per_profile_results[0]["risk_level"] == "high"
    assert result.per_profile_results[1]["profile_name"] == "김철수"
    assert "활성화된 프로필 기준 요약:" in result.answer
    assert "[코워커]" in result.answer
    assert "[김철수]" in result.answer


def test_multi_profile_response_can_be_rewritten_by_llm():
    profiles = [
        Profile(
            profile_id=1,
            profile_name="코워커",
            user_id=1,
            user_allergies=["대두"],
            is_active=True,
        ),
        Profile(
            profile_id=2,
            profile_name="김철수",
            user_id=1,
            is_active=True,
        ),
    ]

    def fake_llm(prompt: str) -> str:
        assert "활성 프로필 목록" in prompt
        assert "코워커" in prompt
        assert "김철수" in prompt
        return (
            "코워커는 대두 관련 성분 때문에 피하는 편이 좋고, 김철수는 현재 확인된 정보 기준으로는 큰 문제는 없어 보여요. "
            "같은 제품이어도 활성화된 프로필에 따라 결론이 다를 수 있으니 함께 확인하는 것이 좋아요.\n\n"
            "원하시면 아래와 같은 내용도 도와드릴게요:\n"
            "- 두 사람 기준으로 특히 주의할 성분만 다시 정리해줄까?\n"
            "- 당류나 나트륨처럼 건강 기준도 같이 볼까?\n"
        )

    set_llm_client(fake_llm)

    try:
        result = generate_chat_response(
            profile=profiles[0],
            active_profiles=profiles,
            product=build_base_product(),
            message="이거 먹어도 돼?",
        )

        assert result.intent == "can_i_eat"
        assert result.answer_source == "hybrid"
        assert "코워커는 대두 관련 성분" in result.answer
        assert "김철수" in result.answer
        assert result.active_profile_names == ["코워커", "김철수"]
        assert len(result.per_profile_results) == 2
        assert len(result.recommended_questions) >= 1
    finally:
        set_llm_client(None)


def test_multi_profile_health_risk_check_can_be_classified_and_rewritten():
    profiles = [
        Profile(
            profile_id=11,
            profile_name="코워커",
            user_id=1,
            conditions=["당뇨"],
            is_active=True,
        ),
        Profile(
            profile_id=12,
            profile_name="김철수",
            user_id=1,
            conditions=["다이어트"],
            is_active=True,
        ),
    ]

    product = Product(
        product_id=909,
        product_name="달콤바삭 스낵",
        rawmtrl_nm="옥수수분말, 설탕, 식물성유지",
        allergy=["대두"],
        nutrition={
            "kcal": 330,
            "carbohydrate": 34,
            "sugars": 18,
            "fat": 21,
        },
        product_found=True,
    )

    def fake_llm(prompt: str) -> str:
        if "의도 분류기" in prompt:
            assert "현재 활성 프로필들: 코워커, 김철수" in prompt
            return '{"intent":"health_risk_check"}'

        assert "활성 프로필 목록" in prompt
        assert "질환/건강 상태: 당뇨" in prompt
        assert "질환/건강 상태: 다이어트" in prompt
        return (
            "코워커는 당류와 탄수화물 수치 때문에 혈당 관리 측면에서 주의가 필요하고, "
            "김철수는 열량과 지방 수치 때문에 체중 관리 관점에서 부담이 있을 수 있어요. "
            "같은 제품이라도 활성화된 프로필의 건강 기준이 다르면 확인 포인트도 달라져요.\n\n"
            "원하시면 아래와 같은 내용도 도와드릴게요:\n"
            "- 두 사람 기준으로 어떤 영양성분을 먼저 봐야 하는지 다시 정리해줄까?\n"
            "- 비슷한 제품이 있으면 더 가벼운 선택지도 비교해볼까?\n"
        )

    set_llm_client(fake_llm)

    try:
        result = generate_chat_response(
            profile=profiles[0],
            active_profiles=profiles,
            product=product,
            message="당뇨 있는데 이거 괜찮아?",
        )

        assert result.intent == "health_risk_check"
        assert result.answer_source == "hybrid"
        assert result.active_profile_names == ["코워커", "김철수"]
        assert len(result.per_profile_results) == 2
        assert result.per_profile_results[0]["profile_name"] == "코워커"
        assert result.per_profile_results[1]["profile_name"] == "김철수"
        assert "코워커는 당류와 탄수화물" in result.answer
        assert "김철수는 열량과 지방" in result.answer
        assert len(result.recommended_questions) >= 1
    finally:
        set_llm_client(None)


def test_barcode_lookup_returns_product_without_raw_payload():
    product = find_product_by_barcode("8801234567890")

    assert product is not None
    assert product.product_name == "저염 두부스낵"
    assert product.barcode == "8801234567890"
    assert product.nutrition["sodium"] == 420.0


def test_report_number_lookup_returns_product():
    product = find_product_by_report_number("202400000001")

    assert product is not None
    assert product.product_name == "저염 두부스낵"
    assert product.report_number == "202400000001"


def test_natural_language_product_name_lookup_returns_product():
    product = find_product_by_text("저염 두부 스낵 괜찮아?")

    assert product is not None
    assert product.product_name == "저염 두부스낵"
    assert product.match_confidence is not None


def test_natural_language_named_product_question_gets_product_judgement():
    product = find_product_by_text("저염 두부스낵 어때?")

    result = generate_chat_response(
        profile=Profile(profile_id=1, profile_name="나", special_diet="저염식"),
        product=product,
        message="저염 두부스낵 어때?",
    )

    assert result.intent == "can_i_eat"
    assert result.task_type == "product_chat"
    assert "저염식" in result.answer or "나트륨" in result.answer


def test_low_confidence_product_search_asks_for_confirmation():
    response = chat(ChatRequest(message="두유 스낵 어때?"))

    assert response.intent == "unknown"
    assert "후보 중 맞는 제품" in response.answer
    assert response.recommended_products
    assert response.recommended_products[0]["match_confidence"] <= 0.5


def test_confirmation_candidate_can_be_confirmed_by_barcode():
    confirmation = chat(ChatRequest(message="닥터유 단백질바 어때?"))
    selected = confirmation.recommended_products[0]

    assert selected["product_name"] == "오리온 닥터유 단백질바 50g"
    assert selected["barcode"] == "8801111111111"

    response = chat(ChatRequest(message="닥터유 단백질바 어때?", barcode=selected["barcode"]))

    assert response.intent != "unknown"
    assert "후보 중 맞는 제품" not in response.answer


def test_confirmation_candidate_can_be_confirmed_by_raw_product_without_identifier():
    confirmation = chat(ChatRequest(message="닥터유 단백질바 어때?"))
    selected = dict(confirmation.recommended_products[0])
    selected.pop("barcode", None)
    selected.pop("report_number", None)

    response = chat(ChatRequest(message="닥터유 단백질바 어때?", raw_product=selected))

    assert response.intent != "unknown"
    assert "후보 중 맞는 제품" not in response.answer
    assert "닥터유" in response.answer or "단백질" in response.answer


def test_non_top_confirmation_candidate_stays_selected():
    confirmation = chat(ChatRequest(message="단백질바 먹어도 돼?"))
    selected = next(
        product
        for product in confirmation.recommended_products
        if product["product_name"] == "오리온 닥터유 프로 단백질바 70g"
    )

    response = chat(
        ChatRequest(
            message="단백질바 먹어도 돼?",
            barcode=selected["barcode"],
            raw_product=selected,
        )
    )

    assert response.intent != "unknown"
    assert "후보 중 맞는 제품" not in response.answer
    assert response.current_product is not None
    assert response.current_product["product_name"] == "오리온 닥터유 프로 단백질바 70g"
    assert response.intent == "can_i_eat"


def test_product_name_nutrition_word_does_not_override_eatability_intent():
    confirmation = chat(ChatRequest(message="단백질바 먹어도 돼?"))
    selected = next(
        product
        for product in confirmation.recommended_products
        if product["product_name"] == "오리온 닥터유 드링크 단백질"
    )

    response = chat(
        ChatRequest(
            message="단백질바 먹어도 돼?",
            barcode=selected["barcode"],
            raw_product=selected,
        )
    )

    assert response.intent == "can_i_eat"
    assert response.current_product is not None
    assert response.current_product["product_name"] == "오리온 닥터유 드링크 단백질"


def test_explicit_selected_product_replaces_stale_candidate_context():
    drink = find_product_by_barcode("8807777777777")

    selected_response = chat(
        ChatRequest(
            message="오리온 닥터유 단백질바 50g 맞아요",
            barcode=drink.barcode,
            raw_product=drink.model_dump(),
            chat_history=[
                ChatHistoryItem(role="user", content="오리온 닥터유 드링크 단백질 맞아요"),
                ChatHistoryItem(role="assistant", content="오리온 닥터유 드링크 단백질 기준으로 답변했어요."),
            ],
        )
    )

    assert selected_response.current_product is not None
    assert selected_response.current_product["product_name"] == "오리온 닥터유 단백질바 50g"

    followup_response = chat(
        ChatRequest(
            message="다른 영양성분도 같이 설명해줘",
            barcode=selected_response.current_product["barcode"],
            raw_product=selected_response.current_product,
            chat_history=[
                ChatHistoryItem(role="user", content="오리온 닥터유 드링크 단백질 맞아요"),
                ChatHistoryItem(role="assistant", content="오리온 닥터유 드링크 단백질 기준으로 답변했어요."),
                ChatHistoryItem(role="user", content="오리온 닥터유 단백질바 50g 맞아요"),
                ChatHistoryItem(role="assistant", content=selected_response.answer),
            ],
        )
    )

    assert followup_response.current_product is not None
    assert followup_response.current_product["product_name"] == "오리온 닥터유 단백질바 50g"
    assert "오리온 닥터유 단백질바 50g" in followup_response.answer
    assert "오리온 닥터유 드링크 단백질" not in followup_response.answer


def test_conversation_state_restores_last_confirmed_product_for_followup():
    product = find_product_by_barcode("8801111111111")
    first_response = chat(
        ChatRequest(
            message="오리온 닥터유 단백질바 50g 맞아요",
            barcode=product.barcode,
            raw_product=product.model_dump(),
        )
    )

    followup_response = chat(
        ChatRequest(
            message="다른 영양성분도 같이 설명해줘",
            conversation_state=first_response.conversation_state,
            chat_history=[
                ChatHistoryItem(role="user", content="오리온 닥터유 단백질바 50g 맞아요"),
                ChatHistoryItem(role="assistant", content=first_response.answer),
            ],
        )
    )

    assert followup_response.current_product is not None
    assert followup_response.current_product["product_name"] == "오리온 닥터유 단백질바 50g"
    assert "오리온 닥터유 단백질바 50g" in followup_response.answer


def test_explicit_protein_nutrition_question_still_explains_protein():
    product = find_product_by_barcode("8801111111111")

    response = chat(
        ChatRequest(
            message="이 제품 단백질 함량은 어때?",
            barcode=product.barcode,
            raw_product=product.model_dump(),
        )
    )

    assert response.intent == "nutrition_explain"
    assert "오리온 닥터유 단백질바 50g" in response.answer


def test_explicit_new_product_question_does_not_use_previous_selected_product():
    milk = find_product_by_barcode("P00156")

    response = chat(
        ChatRequest(
            message="두부스낵은 먹어도 될까?",
            barcode=milk.barcode,
            raw_product=milk.model_dump(),
            raw_profile={"nickname": "나", "allergies": "우유", "special_diet": "저염식"},
            chat_history=[
                ChatHistoryItem(role="user", content="우유에 다른 알레르기 성분 있어?"),
                ChatHistoryItem(role="assistant", content="매일우유 오리지널 200ml에는 우유가 확인돼요."),
            ],
        )
    )

    assert response.intent == "unknown"
    assert "후보 중 맞는 제품" in response.answer
    assert response.recommended_products
    assert "두부스낵" in response.recommended_products[0]["product_name"]
    assert "우유 관련 성분" not in response.answer


def test_explicit_ambiguous_new_product_asks_confirmation_even_with_selected_product():
    milk = find_product_by_barcode("P00156")

    response = chat(
        ChatRequest(
            message="새우깡은 먹어도 돼?",
            barcode=milk.barcode,
            raw_product=milk.model_dump(),
            raw_profile={"nickname": "나", "allergies": "우유"},
        )
    )

    assert response.intent == "unknown"
    assert "후보 중 맞는 제품" in response.answer
    assert response.recommended_products
    assert any("새우깡" in product["product_name"] for product in response.recommended_products)


def test_general_topic_shift_ignores_selected_product_context():
    milk = find_product_by_barcode("P00156")

    response = chat(
        ChatRequest(
            message="당뇨 있으면 간식 고를 때 뭐 봐야 해?",
            barcode=milk.barcode,
            raw_product=milk.model_dump(),
            raw_profile={"nickname": "나", "allergies": "우유", "conditions": "당뇨"},
            chat_history=[
                ChatHistoryItem(role="user", content="우유에 다른 알레르기 성분 있어?"),
                ChatHistoryItem(role="assistant", content="매일우유 오리지널 200ml에는 우유가 확인돼요."),
            ],
        )
    )

    assert response.task_type == "general_chat"
    assert response.intent in {"general_food_health", "general_diet_guide", "general_nutrition_guide"}
    assert "매일우유" not in response.answer


def test_general_topic_shift_does_not_restore_product_from_history():
    response = chat(
        ChatRequest(
            message="알레르기 있으면 제품 고를 때 뭐 봐야 해?",
            raw_profile={"nickname": "나", "allergies": "우유"},
            chat_history=[
                ChatHistoryItem(role="user", content="매일우유 오리지널 200ml 알레르기 알려줘"),
                ChatHistoryItem(role="assistant", content="매일우유 오리지널 200ml에는 우유가 확인돼요."),
            ],
        )
    )

    assert response.task_type == "general_chat"
    assert response.intent == "general_allergy_guide"
    assert "매일우유" not in response.answer


def test_product_followup_still_uses_selected_product_after_topic_shift_guard():
    milk = find_product_by_barcode("P00156")

    response = chat(
        ChatRequest(
            message="다른 알레르기 성분은 없어?",
            barcode=milk.barcode,
            raw_product=milk.model_dump(),
            raw_profile={"nickname": "나", "allergies": "우유"},
            chat_history=[
                ChatHistoryItem(role="user", content="이 제품 알레르기 성분 알려줘"),
                ChatHistoryItem(role="assistant", content="매일우유 오리지널 200ml에는 우유가 확인돼요."),
            ],
        )
    )

    assert response.task_type == "product_chat"
    assert response.intent == "ingredient_check"
    assert "매일우유" in response.answer or "우유" in response.answer


def test_followup_prefers_recent_history_product_over_stale_selected_product():
    stale_product = find_product_by_barcode("8801043036078")

    response = chat(
        ChatRequest(
            message="다른 알레르기 성분도 같이 확인해줘",
            barcode=stale_product.barcode,
            raw_product=stale_product.model_dump(),
            raw_profile={"nickname": "나", "allergies": "우유"},
            chat_history=[
                ChatHistoryItem(role="user", content="새우깡 먹어도 돼?"),
                ChatHistoryItem(role="assistant", content="농심 매운 새우깡 90g 기준으로 새우 알레르기 성분이 확인돼요."),
                ChatHistoryItem(role="user", content="저염 두부스낵은 어때?"),
                ChatHistoryItem(role="assistant", content="저염 두부스낵은 저염식 기준으로 나트륨이 높은 편이에요. 현재 수치는 420.0이에요."),
            ],
        )
    )

    assert response.intent == "ingredient_check"
    assert "저염 두부스낵" in response.answer
    assert "새우깡" not in response.answer


def test_general_allergy_person_question_does_not_trigger_product_confirmation():
    tofu = find_product_by_barcode("8801234567890")

    response = chat(
        ChatRequest(
            message="대두 알레르기가 있는 사람은 먹어도 괜찮아?",
            barcode=tofu.barcode,
            raw_product=tofu.model_dump(),
            raw_profile={"nickname": "나"},
            chat_history=[
                ChatHistoryItem(role="user", content="저염 두부스낵은 어때?"),
                ChatHistoryItem(role="assistant", content="저염 두부스낵은 저염식 기준으로 나트륨이 높은 편이에요."),
            ],
        )
    )

    assert response.task_type == "general_chat"
    assert response.intent == "general_allergy_guide"
    assert not response.recommended_products
    assert "후보 중 맞는 제품" not in response.answer


def test_broad_nutrition_followup_summarizes_product_nutrition():
    tofu = find_product_by_barcode("8801234567890")

    response = chat(
        ChatRequest(
            message="저염 두부스낵의 다른 영양성분도 알려줘",
            barcode=tofu.barcode,
            raw_product=tofu.model_dump(),
            raw_profile={"nickname": "나", "special_diet": "저염식"},
            chat_history=[
                ChatHistoryItem(role="user", content="저염 두부스낵은 어때?"),
                ChatHistoryItem(role="assistant", content="저염 두부스낵은 저염식 기준으로 나트륨이 높은 편이에요."),
            ],
        )
    )

    assert response.intent == "nutrition_explain"
    assert "영양성분" in response.answer
    assert "나트륨" in response.answer
    assert "궁금한 영양성분을 알려주면" not in response.answer


def test_broad_overall_followup_uses_current_product_context():
    tofu = find_product_by_barcode("8801234567890")

    response = chat(
        ChatRequest(
            message="전체적으로 알려줘",
            barcode=tofu.barcode,
            raw_product=tofu.model_dump(),
            raw_profile={"nickname": "나", "special_diet": "저염식"},
            chat_history=[
                ChatHistoryItem(role="user", content="저염 두부스낵의 다른 영양성분도 알려줘"),
                ChatHistoryItem(role="assistant", content="저염 두부스낵의 확인 가능한 영양성분은 열량 180.0, 나트륨 420.0이에요."),
            ],
        )
    )

    assert response.task_type == "product_chat"
    assert response.intent in {"product_summary", "can_i_eat"}
    assert "질문 뜻을 아직 정확히 잡지 못했어요" not in response.answer


def test_product_detail_followup_keeps_selected_product_context():
    tofu = find_product_by_barcode("8801234567890")

    response = chat(
        ChatRequest(
            message="어떤 성분이나 수치 때문에 조심해야 하는지 자세히 알려줘",
            barcode=tofu.barcode,
            raw_product=tofu.model_dump(),
            raw_profile={"nickname": "나", "special_diet": "저염식"},
            chat_history=[
                ChatHistoryItem(role="user", content="저염 두부스낵은 먹어도 될까?"),
                ChatHistoryItem(role="assistant", content="저염식 기준으로는 나트륨이 높은 편이에요. 현재 수치는 420.0이에요."),
            ],
        )
    )

    assert response.task_type == "product_chat"
    assert response.intent == "can_i_eat"
    assert response.risk_level == "medium"
    assert "저염식" in response.answer
    assert "나트륨" in response.answer
    assert "조심해서 볼 포인트" in response.answer


def test_health_nutrition_followup_keeps_product_context():
    tofu = find_product_by_barcode("8801234567890")

    response = chat(
        ChatRequest(
            message="당류나 나트륨 같은 건강 기준으로도 설명해줘",
            barcode=tofu.barcode,
            raw_product=tofu.model_dump(),
            raw_profile={"nickname": "나", "special_diet": "저염식"},
            chat_history=[
                ChatHistoryItem(role="user", content="저염 두부스낵은 어때?"),
                ChatHistoryItem(role="assistant", content="저염 두부스낵은 저염식 기준으로 나트륨이 높은 편이에요."),
                ChatHistoryItem(role="user", content="어떤 성분이나 수치 때문에 조심해야 하는지 자세히 알려줘"),
                ChatHistoryItem(role="assistant", content="저염 두부스낵에서 조심해서 볼 포인트는 나트륨이에요."),
            ],
        )
    )

    assert response.task_type == "product_chat"
    assert response.intent == "nutrition_explain"
    assert "저염 두부스낵" in response.answer
    assert "나트륨" in response.answer
    assert response.intent != "general_nutrition_guide"


def test_general_caution_question_can_still_shift_away_from_selected_product():
    tofu = find_product_by_barcode("8801234567890")

    response = chat(
        ChatRequest(
            message="알레르기 있으면 어떤 성분을 조심해야 해?",
            barcode=tofu.barcode,
            raw_product=tofu.model_dump(),
            raw_profile={"nickname": "나", "special_diet": "저염식"},
            chat_history=[
                ChatHistoryItem(role="user", content="저염 두부스낵은 먹어도 될까?"),
                ChatHistoryItem(role="assistant", content="저염식 기준으로는 나트륨이 높은 편이에요."),
            ],
        )
    )

    assert response.task_type == "general_chat"
    assert response.intent == "general_allergy_guide"
    assert "저염 두부스낵" not in response.answer


def test_generic_allergy_followup_uses_previous_product_context():
    response = chat(
        ChatRequest(
            message="다른 알레르기 성분은 없어?",
            chat_history=[
                ChatHistoryItem(role="user", content="새우깡에 다른 알레르기 성분이 있는지 확인해달라고"),
                ChatHistoryItem(role="assistant", content="이 제품에는 새우 관련 성분이 포함되어 있어요."),
            ],
        )
    )

    assert response.intent == "ingredient_check"
    assert response.task_type == "product_chat"
    assert response.risk_level == "medium"
    assert "알레르기" in response.answer
    assert any(term in response.answer for term in ["밀", "대두", "우유"])


def test_followup_question_can_restore_product_from_history():
    product = find_product_from_history([
        ChatHistoryItem(role="user", content="저염 두부스낵 설명해줘"),
        ChatHistoryItem(role="assistant", content="저염 두부스낵 스캔이 완료됐어요."),
    ])

    assert product is not None
    assert product.product_name == "저염 두부스낵"


def test_followup_uses_most_recent_product_when_multiple_are_in_history():
    product = find_product_from_history([
        ChatHistoryItem(role="user", content="고단백 두유바 설명해줘"),
        ChatHistoryItem(role="assistant", content="고단백 두유바 정보를 정리했어요."),
        ChatHistoryItem(role="user", content="저염 두부스낵은 어때?"),
        ChatHistoryItem(role="assistant", content="저염 두부스낵은 나트륨을 확인해야 해요."),
    ])

    assert product is not None
    assert product.product_name == "저염 두부스낵"


def test_recommend_alternative_products_filters_profile_allergies():
    repo = InMemoryProductRepository(seed_data={
        "1": {
            "식품명": "달콤 우유 쿠키",
            "바코드": "1",
            "RAWMTRL_NM": "밀가루, 우유, 설탕",
            "allergy": "우유",
            "당류": "18g",
            "나트륨": "300mg",
            "PRDLST_DCNM": "과자",
        },
        "2": {
            "식품명": "저당 오트 쿠키",
            "바코드": "2",
            "RAWMTRL_NM": "귀리, 대체감미료",
            "allergy": "",
            "당류": "4g",
            "나트륨": "180mg",
            "PRDLST_DCNM": "과자",
        },
    })
    current = repo.get_by_barcode("1")
    profile = Profile(profile_id=1, profile_name="나", user_allergies=["우유"])

    recommendations = recommend_alternative_products(current, profile=profile, repository=repo)

    assert recommendations
    assert recommendations[0]["product_name"] == "저당 오트 쿠키"
    assert "우유" in recommendations[0]["reason"]
    assert "당류" in recommendations[0]["reason"]


def test_barcode_only_request_can_reach_chat_response():
    req = ChatRequest(
        raw_profile={
            "user_id": 11,
            "nickname": "소금관리",
            "special_diet": "저염식",
        },
        barcode="8801234567890",
        message="이거 먹어도 돼?",
    )

    profile, product, barcode, report_number = normalize_chat_request(req)
    if product is None:
        product = find_product_by_barcode(barcode)

    result = generate_chat_response(profile=profile, product=product, message=req.message)

    assert result.intent == "can_i_eat"
    assert result.risk_level == "medium"
    assert "저염식" in result.answer
    assert "420.0" in result.answer or "420" in result.answer


def test_report_number_only_request_can_reach_chat_response():
    req = ChatRequest(
        raw_profile={
            "user_id": 12,
            "nickname": "보고번호 사용자",
            "special_diet": "저염식",
        },
        report_number="202400000001",
        message="이 제품 설명해줘",
    )

    profile, product, barcode, report_number = normalize_chat_request(req)
    if product is None:
        product = find_product_by_report_number(report_number)

    result = generate_chat_response(profile=profile, product=product, message=req.message)

    assert result.intent == "product_summary"
    assert result.task_type == "product_chat"
    assert "핵심 정보" in result.answer or "주요 원재료" in result.answer


def test_sqlite_product_repository_and_activity_history():
    fd, db_path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)

    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE products (
                    barcode TEXT PRIMARY KEY,
                    product_name TEXT,
                    manufacturer TEXT,
                    report_no TEXT,
                    raw_materials TEXT,
                    allergy TEXT,
                    nutrient_text TEXT,
                    image_url TEXT,
                    source TEXT,
                    energy_kcal REAL,
                    protein_g REAL,
                    fat_g REAL,
                    carbs_g REAL,
                    sugar_g REAL,
                    sodium_mg REAL,
                    cholesterol_mg REAL,
                    allergy_warning TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO products
                (barcode, product_name, manufacturer, report_no, raw_materials, allergy, nutrient_text, image_url, source,
                 energy_kcal, protein_g, fat_g, carbs_g, sugar_g, sodium_mg, cholesterol_mg, allergy_warning)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "8807777777777",
                    "DB 저염 스낵",
                    "테스트 제조사",
                    "202400009999",
                    "감자전분, 정제소금, 분리대두단백",
                    "대두",
                    "열량 210kcal, 단백질 8g, 지방 6g, 탄수화물 28g, 당류 5g, 나트륨 410mg",
                    "https://example.com/db-snack.png",
                    "test",
                    210,
                    8,
                    6,
                    28,
                    5,
                    410,
                    0,
                    "대두 함유",
                ),
            )
            conn.commit()

        product_repo = SQLiteProductRepository(db_path=db_path)
        activity_repo = SQLiteActivityRepository(db_path=db_path)
        set_product_repository(product_repo)
        set_activity_repository(activity_repo)

        product = find_product_by_barcode("8807777777777")
        assert product is not None
        assert product.product_name == "DB 저염 스낵"
        assert product.nutrition["sodium"] == 410.0
        assert product.report_number == "202400009999"

        product_by_report = find_product_by_report_number("202400009999")
        assert product_by_report is not None
        assert product_by_report.barcode == "8807777777777"

        profile = Profile(
            profile_id=21,
            profile_name="DB 사용자",
            user_id=21,
            special_diet=["저염식"],
        )
        response = generate_chat_response(profile=profile, product=product, message="이거 먹어도 돼?")
        log_chat_activity(
            profile=profile,
            product=product,
            barcode=product.barcode,
            report_number=product.report_number,
            message="이거 먹어도 돼?",
            intent=response.intent,
        )

        history = list_user_activities(user_id=21, limit=10)
        assert len(history) == 1
        assert history[0].barcode == "8807777777777"
        assert history[0].action_type == "SCAN"
    finally:
        set_product_repository(InMemoryProductRepository())
        set_activity_repository(InMemoryActivityRepository())
        if os.path.exists(db_path):
            os.remove(db_path)


if __name__ == "__main__":
    tests = [
        value for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    ]
    tests.sort(key=lambda fn: fn.__name__)

    passed = 0
    skipped = 0

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
            print(f"PASS {test_fn.__name__}")
        except SkipTest as exc:
            skipped += 1
            print(f"SKIP {test_fn.__name__}: {exc}")

    print(f"Completed {passed} tests, skipped {skipped}.")
