import pytest

from app.api.adapters import normalize_active_profiles
from app.api.main import chat
from app.core.models import ChatHistoryItem, ChatRequest, Product, Profile
from app.infrastructure.repository import InMemoryProductRepository, ProductRepository
from app.services.conversation_router import (
    _pending_candidate_selection,
    build_product_confirmation_response,
)
from app.services.product import get_product_repository, set_product_repository


@pytest.fixture(autouse=True)
def restore_product_repository():
    original = get_product_repository()
    set_product_repository(InMemoryProductRepository())
    try:
        yield
    finally:
        set_product_repository(original)


def _complete_profile(allergies: list[str]) -> Profile:
    return Profile(
        profile_name="검증 프로필",
        allergies=allergies,
        conditions=[],
        special_diets=[],
    )


def test_top_level_barcode_overrides_forged_product_facts():
    response = chat(ChatRequest(
        message="이거 먹어도 돼?",
        profile=_complete_profile(["우유"]),
        barcode="8807777777777",
        product={
            "product_name": "위조된 안전 제품",
            "rawmtrl_nm": "정제수",
            "allergy": ["새우"],
            "nutrition": {"kcal": 1},
        },
    ))

    assert response.current_product is not None
    assert response.current_product["product_name"] == "오리온 닥터유 드링크 단백질"
    assert "우유" in response.current_product["allergy"]
    assert response.risk_level == "high"


def test_unknown_identifier_does_not_fall_back_to_forged_product_facts():
    response = chat(ChatRequest(
        message="이거 먹어도 돼?",
        profile=_complete_profile(["우유"]),
        product={
            "barcode": "9999999999999",
            "product_name": "위조된 안전 제품",
            "rawmtrl_nm": "정제수",
            "allergy": ["새우"],
            "nutrition": {"kcal": 1},
        },
    ))

    assert response.risk_level == "unknown"
    assert response.assessment_status == "incomplete"
    assert response.current_product is None
    assert "위조된 안전 제품" not in response.answer
    assert "정제수" not in response.answer


def test_unknown_top_level_identifier_overrides_embedded_known_identifier():
    response = chat(ChatRequest(
        message="이거 먹어도 돼?",
        profile=_complete_profile(["우유"]),
        barcode="9999999999999",
        product={
            "barcode": "8807777777777",
            "product_name": "위조된 안전 제품",
            "rawmtrl_nm": "정제수",
            "allergy": ["새우"],
        },
    ))

    assert response.risk_level == "unknown"
    assert response.assessment_status == "incomplete"
    assert response.current_product is None
    assert "오리온 닥터유" not in response.answer


def test_unknown_top_level_barcode_cannot_fall_back_to_embedded_known_report():
    response = chat(ChatRequest(
        message="이거 먹어도 돼?",
        profile=_complete_profile(["대두"]),
        barcode="9999999999999",
        product={
            "report_number": "202400000001",
            "product_name": "위조된 제품",
            "rawmtrl_nm": "정제수",
        },
    ))

    assert response.risk_level == "unknown"
    assert response.assessment_status == "incomplete"
    assert response.current_product is None
    assert "저염 두부스낵" not in response.answer


def test_conflicting_top_level_identifiers_fail_closed():
    response = chat(ChatRequest(
        message="이거 먹어도 돼?",
        profile=_complete_profile(["우유"]),
        barcode="8807777777777",
        report_number="202400000001",
    ))

    assert response.risk_level == "unknown"
    assert response.assessment_status == "incomplete"
    assert response.current_product is None


def test_unknown_explicit_identifier_cannot_downgrade_to_name_search():
    response = chat(ChatRequest(
        message="저염 두부스낵 먹어도 돼?",
        profile=_complete_profile(["대두"]),
        barcode="9999999999999",
    ))

    assert response.risk_level == "unknown"
    assert response.assessment_status == "incomplete"
    assert response.current_product is None
    assert "저염 두부스낵" not in response.answer


def test_fresh_explicit_identifier_cannot_be_overridden_by_old_history():
    response = chat(ChatRequest(
        message="이거 먹어도 돼?",
        profile=_complete_profile(["우유"]),
        barcode="8807777777777",
        chat_history=[
            ChatHistoryItem(
                role="user",
                content="저염 두부스낵 원재료 알려줘",
            ),
        ],
    ))

    assert response.current_product is not None
    assert response.current_product["product_name"] == "오리온 닥터유 드링크 단백질"
    assert response.risk_level == "high"


def test_explicit_barcode_is_not_discarded_as_a_general_topic_shift():
    response = chat(ChatRequest(
        message="원재료와 알레르기 표시 알려줘",
        profile=_complete_profile(["우유"]),
        barcode="8807777777777",
    ))

    assert response.intent == "ingredient_check"
    assert response.current_product is not None
    assert response.current_product["product_name"] == "오리온 닥터유 드링크 단백질"
    assert "우유" in response.answer


def test_fresh_embedded_identifier_cannot_be_overridden_by_old_history():
    response = chat(ChatRequest(
        message="이거 먹어도 돼?",
        profile=_complete_profile(["우유"]),
        product={"barcode": "8807777777777"},
        chat_history=[
            ChatHistoryItem(
                role="user",
                content="저염 두부스낵 원재료 알려줘",
            ),
        ],
    ))

    assert response.current_product is not None
    assert response.current_product["product_name"] == "오리온 닥터유 드링크 단백질"
    assert response.risk_level == "high"


def test_one_unknown_embedded_identifier_invalidates_other_embedded_identifiers():
    response = chat(ChatRequest(
        message="이거 먹어도 돼?",
        profile=_complete_profile(["대두"]),
        product={
            "barcode": "9999999999999",
            "report_number": "202400000001",
            "product_name": "위조된 제품",
            "rawmtrl_nm": "정제수",
        },
    ))

    assert response.risk_level == "unknown"
    assert response.assessment_status == "incomplete"
    assert response.current_product is None


def test_identifierless_request_product_is_marked_unverified():
    response = chat(ChatRequest(
        message="이거 먹어도 돼?",
        profile=_complete_profile(["우유"]),
        product={
            "product_name": "요청에만 있는 제품",
            "rawmtrl_nm": "정제수",
            "allergy": ["새우"],
            "nutrition": {"kcal": 1},
        },
    ))

    assert response.risk_level == "unknown"
    assert response.assessment_status == "incomplete"
    assert "DB" in response.answer or "검증" in response.answer


def test_malformed_allergy_alias_cannot_be_hidden_by_an_empty_alias():
    response = chat(ChatRequest(
        message="이거 먹어도 돼?",
        barcode="8807777777777",
        profile={
            "profile_name": "잘못된 프로필",
            "allergies": {"unexpected": "우유"},
            "user_allergies": [],
            "conditions": [],
            "special_diets": [],
        },
    ))

    assert response.risk_level == "unknown"
    assert response.assessment_status == "incomplete"
    assert "알레르기 입력 여부를 확인할 수 없어요" in response.answer


def test_blank_allergy_list_element_is_not_treated_as_explicit_no_allergy():
    profile = Profile.model_validate({
        "profile_name": "빈 원소 프로필",
        "allergies": ["   "],
        "conditions": [],
        "special_diets": [],
    })

    assert profile.allergies == []
    assert profile.allergy_context_provided is False


def test_shared_user_id_is_not_inferred_as_a_family_profile_id():
    request = ChatRequest(
        message="우리 가족 먹어도 돼?",
        raw_profiles=[
            {
                "user_id": 42,
                "nickname": "나",
                "allergies": ["우유"],
                "conditions": [],
                "special_diets": [],
            },
            {
                "user_id": 42,
                "nickname": "아이",
                "allergies": ["땅콩"],
                "conditions": [],
                "special_diets": [],
            },
        ],
    )

    profiles = normalize_active_profiles(request)

    assert len(profiles) == 2
    assert [profile.profile_id for profile in profiles] == [0, 0]
    assert [profile.profile_name for profile in profiles] == ["나", "아이"]


class _IdAwareRepository(ProductRepository):
    def __init__(self, products: list[Product]):
        self.products = products

    def get_by_product_id(self, product_id: int):
        return next(
            (product for product in self.products if product.product_id == product_id),
            None,
        )

    def get_by_barcode(self, barcode: str):
        return next((product for product in self.products if product.barcode == barcode), None)

    def get_by_report_number(self, report_number: str):
        return next(
            (product for product in self.products if product.report_number == report_number),
            None,
        )

    def search_by_text(self, text: str):
        return None

    def search_candidates(self, text: str, limit: int = 3):
        return []

    def recommend_alternatives(self, product: Product, limit: int = 3):
        return []


def test_conflicting_embedded_barcode_and_product_id_fail_closed():
    first = Product(
        product_id=705,
        barcode="TRUST-A",
        product_name="식별자 A",
        rawmtrl_nm="감자",
        allergy=[],
    )
    second = Product(
        product_id=706,
        barcode="TRUST-B",
        product_name="식별자 B",
        rawmtrl_nm="땅콩",
        allergy=["땅콩"],
    )
    set_product_repository(_IdAwareRepository([first, second]))

    response = chat(ChatRequest(
        message="이거 먹어도 돼?",
        profile=_complete_profile(["땅콩"]),
        product={
            "product_id": 706,
            "barcode": "TRUST-A",
            "product_name": "위조된 제품",
            "rawmtrl_nm": "정제수",
        },
    ))

    assert response.risk_level == "unknown"
    assert response.assessment_status == "incomplete"
    assert response.current_product is None


def test_explicit_top_level_barcode_ignores_conflicting_embedded_identifiers():
    first = Product(
        product_id=707,
        barcode="TRUST-TOP",
        product_name="상위 식별자 제품",
        rawmtrl_nm="땅콩",
        allergy=["땅콩"],
    )
    second = Product(
        product_id=708,
        barcode="TRUST-EMBEDDED",
        product_name="내장 식별자 제품",
        rawmtrl_nm="감자",
        allergy=[],
    )
    set_product_repository(_IdAwareRepository([first, second]))

    response = chat(ChatRequest(
        message="이거 먹어도 돼?",
        profile=_complete_profile(["땅콩"]),
        barcode="TRUST-TOP",
        product={
            "product_id": 708,
            "barcode": "TRUST-EMBEDDED",
            "product_name": "위조된 제품",
            "rawmtrl_nm": "정제수",
        },
    ))

    assert response.current_product is not None
    assert response.current_product["product_name"] == "상위 식별자 제품"
    assert response.risk_level == "high"


def test_anonymous_same_name_profiles_keep_distinct_allergy_contexts():
    product = Product(
        product_id=699,
        product_name="땅콩 테스트 제품",
        rawmtrl_nm="땅콩, 설탕",
        allergy=["땅콩"],
    )
    set_product_repository(_IdAwareRepository([product]))

    response = chat(ChatRequest(
        message="우리 둘 다 먹어도 돼?",
        product=product,
        profiles=[
            Profile(
                profile_name="아이",
                allergies=["우유"],
                conditions=[],
                special_diets=[],
            ),
            Profile(
                profile_name="아이",
                allergies=["땅콩"],
                conditions=[],
                special_diets=[],
            ),
        ],
    ))

    assert len(response.per_profile_results) == 2
    assert response.risk_level == "high"
    assert {result["risk_level"] for result in response.per_profile_results} == {
        "unknown",
        "high",
    }


def test_conflicting_duplicate_profile_ids_do_not_drop_safety_information():
    request = ChatRequest(
        message="우리 둘 다 먹어도 돼?",
        profiles=[
            Profile(
                profile_id=55,
                profile_name="중복 ID 프로필",
                allergies=["우유"],
                conditions=[],
                special_diets=[],
            ),
            Profile(
                profile_id=55,
                profile_name="중복 ID 프로필",
                allergies=["땅콩"],
                conditions=[],
                special_diets=[],
            ),
        ],
    )

    profiles = normalize_active_profiles(request)

    assert len(profiles) == 2
    assert {allergy for profile in profiles for allergy in profile.allergies} == {
        "우유",
        "땅콩",
    }


def test_product_id_only_reference_stays_server_private_in_stateless_state():
    product = Product(
        product_id=700,
        product_name="ID 상태 제품",
        rawmtrl_nm="감자, 정제소금",
        allergy=[],
    )
    set_product_repository(_IdAwareRepository([product]))

    first = chat(ChatRequest(
        message="이 제품 원재료 알려줘",
        product=product,
    ))

    assert first.current_product is not None
    assert "last_confirmed_product" not in first.conversation_state
    assert first.internal_conversation_state["last_confirmed_product"]["product_id"] == 700
    assert "product_id" not in first.model_dump(mode="json").get(
        "conversation_state", {}
    ).get("last_confirmed_product", {})

    followup = chat(ChatRequest(
        message="그 제품 원재료 다시 알려줘",
        conversation_state=first.conversation_state,
    ))

    assert followup.current_product is None


def test_product_id_only_candidate_is_not_offered_as_stateless_selection():
    first = Product(
        product_id=701,
        product_name="ID 전용 첫 제품",
        rawmtrl_nm="쌀",
        allergy=["밀"],
    )
    second = Product(
        product_id=702,
        product_name="바코드 둘째 제품",
        barcode="STATE-B",
        rawmtrl_nm="감자",
        allergy=["대두"],
    )
    set_product_repository(_IdAwareRepository([first, second]))

    confirmation = build_product_confirmation_response([first, second])

    assert [item["product_name"] for item in confirmation.product_candidates] == [
        "바코드 둘째 제품",
    ]
    assert confirmation.conversation_state["pending_product_candidates"] == [
        {
            "product_name": "바코드 둘째 제품",
            "barcode": "STATE-B",
        }
    ]
    assert "product_id" not in confirmation.model_dump(mode="json")[
        "conversation_state"
    ]["pending_product_candidates"][0]

    selected = chat(ChatRequest(
        message="첫 번째",
        conversation_state=confirmation.conversation_state,
    ))
    assert selected.current_product is not None
    assert selected.current_product["product_name"] == "바코드 둘째 제품"


def test_candidate_without_public_identifier_is_not_offered():
    name_only = Product(product_name="선택할 수 없는 첫 후보")
    resolvable = Product(
        product_id=703,
        product_name="선택 가능한 둘째 후보",
        rawmtrl_nm="감자",
    )

    confirmation = build_product_confirmation_response([name_only, resolvable])

    assert confirmation.product_candidates == []
    assert confirmation.intent == "missing_product"
    assert "pending_product_candidates" not in confirmation.conversation_state


def test_duplicate_name_with_only_internal_ids_is_not_publicly_selectable():
    name_only = Product(
        product_name="같은 이름 제품",
        match_confidence=0.99,
    )
    resolvable = Product(
        product_id=704,
        product_name="같은 이름 제품",
        match_confidence=0.5,
    )

    confirmation = build_product_confirmation_response([name_only, resolvable])

    assert confirmation.product_candidates == []
    assert confirmation.intent == "missing_product"


def test_same_display_name_with_distinct_identifiers_keeps_both_variants():
    small = Product(
        barcode="VARIANT-SMALL",
        product_name="같은 이름 제품 30g",
        rawmtrl_nm="감자",
    )
    large = Product(
        barcode="VARIANT-LARGE",
        product_name="같은 이름 제품 90g",
        rawmtrl_nm="감자, 땅콩",
        allergy=["땅콩"],
    )

    confirmation = build_product_confirmation_response([small, large])

    assert [item["barcode"] for item in confirmation.product_candidates] == [
        "VARIANT-SMALL",
        "VARIANT-LARGE",
    ]
    assert [
        item["barcode"]
        for item in confirmation.conversation_state["pending_product_candidates"]
    ] == ["VARIANT-SMALL", "VARIANT-LARGE"]


def test_negated_ordinal_selects_the_non_negated_candidate():
    state = {
        "pending_product_candidates": [
            {"barcode": "8801234567890", "product_name": "첫 제품"},
            {"barcode": "8800000000000", "product_name": "둘째 제품"},
            {"barcode": "8801111111111", "product_name": "셋째 제품"},
        ]
    }

    response = chat(ChatRequest(
        message="첫 번째 말고 두 번째로 할게",
        conversation_state=state,
    ))

    assert response.current_product is not None
    assert response.current_product["product_name"] == "고단백 두유바"


def test_pending_candidate_selection_cannot_be_overridden_by_old_history():
    state = {
        "pending_product_candidates": [
            {"barcode": "8807777777777"},
            {"barcode": "8801234567890"},
        ]
    }

    response = chat(ChatRequest(
        message="첫 번째 제품 먹어도 돼?",
        profile=_complete_profile(["우유"]),
        conversation_state=state,
        chat_history=[
            ChatHistoryItem(
                role="user",
                content="저염 두부스낵 원재료 알려줘",
            ),
        ],
    ))

    assert response.current_product is not None
    assert response.current_product["product_name"] == "오리온 닥터유 드링크 단백질"
    assert response.risk_level == "high"


def test_multiple_positive_ordinals_do_not_select_arbitrarily():
    state = {
        "pending_product_candidates": [
            {"barcode": "8801234567890"},
            {"barcode": "8800000000000"},
        ]
    }

    product, _, _ = _pending_candidate_selection("첫 번째랑 두 번째 비교", state)

    assert product is None


def test_conflicting_candidate_identifiers_do_not_select_either_product():
    state = {
        "pending_product_candidates": [
            {
                "barcode": "8807777777777",
                "report_number": "202400000001",
            },
        ]
    }

    product, _, _ = _pending_candidate_selection("첫 번째", state)

    assert product is None


def test_comparative_ordinals_do_not_implicitly_select_a_candidate():
    state = {
        "pending_product_candidates": [
            {"barcode": "8801234567890"},
            {"barcode": "8800000000000"},
        ]
    }

    product, _, _ = _pending_candidate_selection("첫 번째보다 두 번째가 나아?", state)

    assert product is None


@pytest.mark.parametrize(
    "message",
    [
        "11번",
        "12번",
        "31번",
        "11번째",
        "12 번째",
        "열두 번째",
        "열 두 번째",
        "스물두 번째",
        "스물 두 번째",
        "열세 번째",
        "1.1번",
        "-1번",
        "+1번",
        "1-2번",
        "1,3번",
        "1/2번",
        "1 / 2번",
        "A1번",
        "제품1번",
    ],
)
def test_multi_digit_numbers_do_not_match_single_digit_candidate_ordinals(message):
    state = {
        "pending_product_candidates": [
            {"barcode": "8801234567890"},
            {"barcode": "8800000000000"},
            {"barcode": "8801111111111"},
        ]
    }

    product, _, _ = _pending_candidate_selection(message, state)

    assert product is None


@pytest.mark.parametrize(
    "message",
    [
        "첫 번째보다 나은 걸로",
        "첫 번째는 싫어",
        "첫 번째 빼줘",
        "1번은 별로야",
        "1번 대신 다른 거",
        "첫 번째가 아니야",
        "첫 번째는 아니에요",
        "첫 번째 아님",
        "첫 번째는, 싫어",
        "첫 번째도 싫어",
        "첫 번째만 빼줘",
        "첫 번째는요, 제외해줘",
        "첫 번째 패스",
        "첫 번째에 비해",
        "첫 번째와의 비교",
        "첫 번째, 비교해줘",
        "첫 번째랑 비교해줘",
        "비교해 보니 첫 번째",
        "첫 번째가 나아?",
    ],
)
def test_rejected_or_compared_candidate_is_not_selected(message):
    state = {
        "pending_product_candidates": [
            {"barcode": "8801234567890"},
            {"barcode": "8800000000000"},
        ]
    }

    product, _, _ = _pending_candidate_selection(message, state)

    assert product is None


@pytest.mark.parametrize(
    "message",
    [
        "첫 번째는 안 돼",
        "첫 번째는 안 되겠어",
        "첫 번째는 못 먹어",
        "첫 번째는 먹지 않아",
        "첫 번째는 괜찮지 않아",
        "첫 번째는 하지 않을래",
        "첫 번째는 안 좋아",
        "첫 번째는 안 괜찮아",
        "첫 번째는 마음에 안 들어",
        "첫 번째는 별론데",
        "첫 번째는 선택하지 않을래",
        "첫 번째 먹어도 돼? 안 위험해?",
        "싫은 건 첫 번째",
        "안 먹을 건 첫 번째",
        "제외할 건 첫 번째",
        "고르지 않을 건 첫 번째",
        "마음에 안 드는 건 첫 번째",
    ],
)
def test_natural_single_ordinal_rejection_is_not_selected(message):
    state = {
        "pending_product_candidates": [
            {"barcode": "8801234567890"},
            {"barcode": "8800000000000"},
        ]
    }

    product, _, _ = _pending_candidate_selection(message, state)

    assert product is None


@pytest.mark.parametrize(
    "message",
    [
        "싫은 건 첫 번째가 아니라 두 번째야",
        "제외할 건 첫 번째 말고 두 번째야",
        "안 먹을 건 첫 번째 말고 두 번째로 할게",
        "고르지 않을 건 첫 번째가 아니라 두 번째야",
        "첫 번째는 괜찮고 싫은 건 두 번째로 할게",
        "첫 번째는 먹고 안 먹을 건 두 번째로 할게",
        "첫 번째는 남기고 제외할 건 두 번째로 할게",
    ],
)
def test_leading_rejection_with_multiple_ordinals_never_selects_another_candidate(
    message,
):
    state = {
        "pending_product_candidates": [
            {"barcode": "8801234567890"},
            {"barcode": "8800000000000"},
        ]
    }

    product, _, _ = _pending_candidate_selection(message, state)

    assert product is None


@pytest.mark.parametrize(
    "message",
    [
        "다른 건 싫고 첫 번째로 할게",
        "나머지는 제외하고 첫 번째 선택해",
    ],
)
def test_explicit_single_selection_after_rejecting_other_candidates_still_works(
    message,
):
    state = {
        "pending_product_candidates": [
            {"barcode": "8801234567890"},
            {"barcode": "8800000000000"},
        ]
    }

    product, barcode, _ = _pending_candidate_selection(message, state)

    assert product is not None
    assert barcode == "8801234567890"


@pytest.mark.parametrize(
    "message",
    [
        "첫 번째 싫어, 두 번째도 싫어",
        "첫 번째 제외하고 두 번째도 제외해줘",
        "첫 번째가 아니야. 두 번째는 패스",
    ],
)
def test_multiple_rejected_ordinals_never_leave_one_implicitly_selected(message):
    state = {
        "pending_product_candidates": [
            {"barcode": "8801234567890"},
            {"barcode": "8800000000000"},
            {"barcode": "8801111111111"},
        ]
    }

    product, _, _ = _pending_candidate_selection(message, state)

    assert product is None


@pytest.mark.parametrize(
    "message",
    [
        "1번",
        "1 번",
        "1번째",
        "첫 번째",
        "첫 번째!",
        "후보는 1번",
        "고를게, 1번",
        "(1번)",
    ],
)
def test_exact_first_candidate_ordinals_still_select_normally(message):
    state = {
        "pending_product_candidates": [
            {"barcode": "8801234567890"},
            {"barcode": "8800000000000"},
        ]
    }

    product, barcode, _ = _pending_candidate_selection(message, state)

    assert product is not None
    assert barcode == "8801234567890"
    assert product.product_name == "저염 두부스낵"


@pytest.mark.parametrize(
    "message",
    [
        "11번 제품 먹어도 돼?",
        "열두 번째 제품 먹어도 돼?",
        "1.1번 제품 먹어도 돼?",
        "1-2번 제품 먹어도 돼?",
        "첫 번째가 아니야. 그래도 먹어도 돼?",
        "첫 번째는, 싫어. 그래도 먹어도 돼?",
        "첫 번째만 빼줘. 먹어도 돼?",
        "첫 번째와의 비교 후 먹어도 되는지 알려줘",
        "첫 번째 싫어, 두 번째도 싫어. 먹어도 돼?",
        "첫 번째는 안 돼. 그래도 먹어도 돼?",
        "첫 번째는 못 먹어. 다른 건 먹어도 돼?",
        "싫은 건 첫 번째야. 뭘 먹어도 돼?",
        "안 먹을 건 첫 번째야. 다른 건 먹어도 돼?",
        "첫 번째는 괜찮지 않아. 먹어도 돼?",
    ],
)
def test_ambiguous_candidate_reply_does_not_ground_a_safety_answer(message):
    state = {
        "pending_product_candidates": [
            {"barcode": "8801234567890"},
            {"barcode": "8807777777777"},
            {"barcode": "8801111111111"},
        ]
    }

    response = chat(ChatRequest(
        message=message,
        profile=_complete_profile(["새우"]),
        conversation_state=state,
    ))

    assert response.current_product is None
    assert response.risk_level == "unknown"
    assert response.assessment_status == "incomplete"
