"""Changes, withdrawals, failed searches and persistence, not just happy paths."""

import json
from uuid import uuid4

import pytest

from app.core.models import ChatRequest, Profile, Product
from app.main import chat
from app.infrastructure.llm import set_llm_client
from app.services.diet_conversation_context import stated_vegetarian_allowance, apply_vegetarian_context
from app.services.recipe_composition import composition_issues
from app.services.recipe_templates import _general_recipe_variants, _product_recipe_variants


def state(response):
    return response.conversation_state.model_dump(mode="json", exclude_none=True)


@pytest.fixture(params=["memory", "sqlite"])
def saved_chat(request, monkeypatch, tmp_path):
    from test_conversation_core import SigningClient, _headers, API_TOKEN, IDENTITY_SECRET
    from app.api.main import app
    from app.infrastructure.conversations import InMemoryConversationRepository, SQLiteConversationRepository
    from app.infrastructure.request_identity import reset_conversation_identity_nonce_registry
    from app.services.conversations import get_conversation_repository, set_conversation_repository

    old = get_conversation_repository()
    path = str(tmp_path / "chat.sqlite3")
    repository = SQLiteConversationRepository(path) if request.param == "sqlite" else InMemoryConversationRepository()
    set_conversation_repository(repository)
    reset_conversation_identity_nonce_registry()
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_CONVERSATIONS", "true")
    monkeypatch.setenv("FOOD_CHATBOT_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("FOOD_CHATBOT_USER_IDENTITY_SECRET", IDENTITY_SECRET)
    client = SigningClient(app)
    room = repository.create(101)

    def send(message, profile=None, **extra):
        response = client.post("/chat", json={
            "conversation_id": room.conversation_id, "turn_id": str(uuid4()),
            "message": message, "profile": profile or {"allergies": []}, **extra,
        }, headers=_headers("POST", "/chat"))
        assert response.status_code == 200, response.text
        return response.json()

    def detail():
        if request.param == "sqlite":
            set_conversation_repository(SQLiteConversationRepository(path))
        url = f"/conversations/{room.conversation_id}"
        response = client.get(url, headers=_headers("GET", url))
        assert response.status_code == 200, response.text
        return response.json()

    send.detail = detail
    try:
        yield send
    finally:
        client.close()
        set_conversation_repository(old)


@pytest.mark.parametrize("message", [
    "유제품은 먹는 게 안 돼", "유제품은 먹는 것은 불가능해", "유제품은 먹으면 안 돼",
    "이제 유제품은 빼줘", "유제품은 제외해줘", "유제품은 못 먹어",
])
def test_dairy_prohibition_is_never_permission(message):
    assert stated_vegetarian_allowance(message) == {"dairy": False}
    response = chat(ChatRequest(
        message=message + ". 이 제품으로 채식 레시피 만들어줘", barcode="P00156",
        profile=Profile(allergies=[], special_diets=["채식"], vegetarian_egg_allowed=True),
    ))
    assert response.recipe is None or response.recipe.source_product_blocked


def test_coordinated_positive_and_negative_predicates():
    assert stated_vegetarian_allowance("유제품은 먹고 달걀은 안 먹는 채식이야.") == {"dairy": True, "egg": False}
    assert stated_vegetarian_allowance("유제품과 달걀은 먹는 채식이야. 레시피 만들어줄래?") == {"dairy": True, "egg": True}
    assert stated_vegetarian_allowance("유제품은 먹어도 돼?") == {}


@pytest.mark.parametrize("message", ["유제품은 안 먹는 건 아니야", "유제품을 먹는 게 가능한지 모르겠어"])
def test_uncertain_predicate_requests_clarification(message):
    profiles = [Profile(profile_id=1, allergies=[], special_diets=["채식"])]
    previous = {"vegetarian_allowances": [{"profile_key": "id:1", "dairy": True}]}
    _, _, ambiguous = apply_vegetarian_context(profiles, message, previous)
    assert ambiguous
    response = chat(ChatRequest(message=message, profile=profiles[0], conversation_state=previous))
    assert response.recipe is None
    assert "확인" in response.answer


@pytest.mark.parametrize("change", ["withdraw", "profile"])
def test_saved_chat_respects_withdrawal_and_updated_profile(saved_chat, change):
    profile = {"profile_id": 1, "profile_name": "나", "allergies": [], "special_diets": ["채식"]}
    first = saved_chat("유제품과 달걀은 먹는 채식이야. 이 제품으로 레시피 만들어줘", profile, barcode="P00156")
    assert first["recipe"] and not first["recipe"]["source_product_blocked"]
    if change == "profile":
        profile = {**profile, "vegetarian_dairy_allowed": False, "vegetarian_egg_allowed": True}
        message = "다른 레시피 더 있어?"
    else:
        message = "이제 유제품은 빼줘. 다른 레시피 추천해줘"
    response = saved_chat(message, profile)
    assert response["recipe"] is None or all(
        "매일우유" not in item["name"] for item in response["recipe"]["ingredients"]
    )
    saved_chat.detail()  # Reopen the SQLite repository before the next turn.
    followup = saved_chat("다른 레시피 더 있어?", profile)
    assert followup["recipe"] is None or followup["recipe"]["product_name"] != "매일우유 오리지널 200ml"


def test_profile_change_invalidates_previous_allowance_without_mutating_profiles():
    original = Profile(profile_id=1, allergies=[], special_diets=["채식"])
    _, allowance, _ = apply_vegetarian_context([original], "유제품과 달걀은 먹는 채식이야.", {})
    changed = original.model_copy(update={"vegetarian_dairy_allowed": False, "vegetarian_egg_allowed": True})
    effective, updated, _ = apply_vegetarian_context([changed], "다른 레시피", {"vegetarian_allowances": allowance})
    assert effective[0].vegetarian_dairy_allowed is False
    assert original.vegetarian_dairy_allowed is None
    assert not any(item.get("dairy") is True for item in updated)
    # A current prohibition is also stronger than an explicit conversational grant.
    effective, _, _ = apply_vegetarian_context([changed], "유제품은 먹어.", {})
    assert effective[0].vegetarian_dairy_allowed is False


def _start_comparison(send):
    send("새우깡과 저염 두부스낵 비교해줘")
    result = send("첫 번째")
    assert len(result["comparison_products"]) == 2


def test_saved_failed_comparison_never_errors_or_restores_old_targets(saved_chat):
    _start_comparison(saved_chat)
    failed = saved_chat("없는제품XYZ와 저염 두부스낵 비교해줘")
    assert not failed["comparison_products"]
    assert "없는제품XYZ" in failed["answer"]
    saved_chat.detail()
    followup = saved_chat("나트륨이요")
    assert followup["intent"] == "food_compare"
    assert not followup["comparison_products"]
    assert "없는제품XYZ" in followup["answer"]
    assert "농심 새우깡" not in followup["answer"]


def test_saved_replacement_confirms_new_product_and_preserves_other_target(saved_chat):
    _start_comparison(saved_chat)
    response = saved_chat("새우깡은 빼고 서울우유와 비교해줘")
    assert response["product_candidates"]
    assert "서울우유" in response["product_candidates"][0]["product_name"]
    saved_chat.detail()
    selected = saved_chat("첫 번째, 나트륨으로 비교해줘")
    names = [p["product_name"] for p in selected["comparison_products"]]
    assert len(names) == 2 and "저염 두부스낵" in names
    assert any("서울우유" in name for name in names)
    assert not any("새우깡" in name for name in names)
    again = saved_chat("단백질로 부탁해")
    assert [p["product_name"] for p in again["comparison_products"]] == names


def test_stateless_failed_comparison_clears_the_old_product_set():
    profile = Profile(allergies=["새우"])
    a = chat(ChatRequest(message="새우깡과 저염 두부스낵 비교해줘", profile=profile))
    b = chat(ChatRequest(message="첫 번째", profile=profile, conversation_state=state(a)))
    c = chat(ChatRequest(message="없는제품XYZ와 저염 두부스낵 비교해줘", profile=profile, conversation_state=state(b)))
    d = chat(ChatRequest(message="나트륨", profile=profile, conversation_state=state(c)))
    assert not d.comparison_products
    assert state(d)["comparison_queries"][0] == "없는제품XYZ"
    assert "농심 새우깡" not in d.answer


def test_rule_recipe_never_reintroduces_disliked_unlisted_seasonings():
    response = chat(ChatRequest(message="저염 레시피 추천해줘", profile=Profile(allergies=[], disliked_ingredients=["레몬", "허브"])))
    assert response.recipe is not None
    assert "레몬" not in " ".join(response.recipe.steps)
    assert "허브" not in " ".join(response.recipe.steps)
    assert response.preference_match == "matched"


@pytest.mark.parametrize("diet", [[], ["저염식"], ["저당식"], ["저지방"], ["저칼로리"], ["채식"], ["고단백"], ["글루텐프리"]])
def test_every_general_template_passes_shared_composition_checks(diet):
    for variant in _general_recipe_variants(diet):
        assert not composition_issues(variant["ingredients"], variant["steps"]), variant["title"]


@pytest.mark.parametrize("category", ["우유", "과자", "면류", "소스"])
def test_product_templates_pass_shared_checks(category):
    product = Product(product_name=f"검증용 {category}", category_detail=category)
    for variant in _product_recipe_variants(product):
        assert not composition_issues(variant["ingredients"], variant["steps"], product=product), variant["title"]


@pytest.mark.parametrize("step", ["재료를 볶아요.", "모든 재료를 구워요.", "제품을 썰어요.", "볶아요.", "채소를 볶아요."])
def test_llm_generic_references_cannot_bypass_liquid_method_guard(step):
    name = "매일우유 오리지널 200ml"
    draft = {"title": name + " 컵", "servings": "1인분", "product_name": name,
             "ingredients": [{"name": name, "amount": "150ml"}], "steps": [step], "substitutions": []}
    set_llm_client(lambda _: json.dumps(draft, ensure_ascii=False))
    try:
        response = chat(ChatRequest(message="이 제품으로 레시피 추천해줘", barcode="P00156", profile=Profile(allergies=[])))
    finally:
        set_llm_client(None)
    assert response.answer_source == "rules"
    assert response.fallback_reason == "recipe_llm_product_method_conflict"
    assert step not in response.recipe.steps


def test_rule_template_mutation_is_checked_before_return(monkeypatch):
    import app.services.recipe as recipe
    bad = {"title": "검증용 수프", "ingredients": [{"name": "토마토", "amount": "1개"}],
           "steps": ["레몬과 허브로 맛을 보완해요."]}
    monkeypatch.setattr(recipe, "_general_recipe_variants", lambda _: [bad])
    response = chat(ChatRequest(message="저염 레시피 추천해줘", profile=Profile(allergies=[])))
    assert response.recipe is None


def test_current_profile_flags_survive_request_round_trip_and_change_idempotency_key():
    from app.api.main import _request_fingerprint
    request = ChatRequest(message="레시피 추천해줘", profile=Profile(
        allergies=[], special_diets=["채식"], vegetarian_dairy_allowed=False, vegetarian_egg_allowed=True,
    ))
    restored = ChatRequest.model_validate(request.model_dump(mode="json"))
    assert restored.profile.vegetarian_dairy_allowed is False
    assert restored.profile.vegetarian_egg_allowed is True
    changed = request.model_copy(update={"profile": request.profile.model_copy(update={"vegetarian_dairy_allowed": True})})
    assert _request_fingerprint(request) != _request_fingerprint(changed)


def test_method_check_allows_cutting_a_declared_fruit_but_not_the_liquid():
    product = Product(product_name="검증용 우유", category_detail="우유")
    ingredients = [{"name": "검증용 우유", "amount": "150ml"}, {"name": "바나나", "amount": "1개"}]
    assert not composition_issues(ingredients, ["바나나를 썰어요.", "우유를 부어요."], product=product)
    assert composition_issues(ingredients, ["모든 재료를 썰어요."], product=product)


def test_permission_withdrawal_is_scoped_to_the_named_profile():
    profiles = [Profile(profile_id=1, profile_name="나", allergies=[]), Profile(profile_id=2, profile_name="아이", allergies=[])]
    previous = {"vegetarian_allowances": [
        {"profile_key": "id:1", "dairy": True}, {"profile_key": "id:2", "dairy": True},
    ]}
    effective, _, ambiguous = apply_vegetarian_context(profiles, "아이는 유제품을 빼줘.", previous)
    assert not ambiguous
    assert effective[0].vegetarian_dairy_allowed is True
    assert effective[1].vegetarian_dairy_allowed is False
