"""The real shipped recipe subset, no private CSV or external model required."""

import hashlib
import json
from dataclasses import replace
from datetime import date

import pytest

from app import knowledge_import as cli
from app.api.response_contract import PublicChatResponse
from app.core.models import ChatRequest, ChatResponse, Product, Profile, RecipeResult
from app.infrastructure import knowledge_review
from app.main import chat
from app.services import recipe, reviewed_recipes as released
from app.services.recipe_templates import _general_recipe_variants
from test_food_chat_condition_lifecycle import saved_chat


DATA_DIR = released.RECIPE_DIR


@pytest.fixture(autouse=True)
def offline_review_date(monkeypatch):
    class ReviewDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 1)

    monkeypatch.setattr(released, "date", ReviewDate)
    monkeypatch.setattr(recipe, "generate_text", lambda _prompt, **_kwargs: None)


def fingerprints(variants):
    return [recipe._recipe_fingerprint(RecipeResult(
        title=v["title"], servings=v.get("servings", "1인분"), product_name="기본 식재료",
        ingredients=v["ingredients"], steps=v["steps"], allergen_check=["테스트"],
    )) for v in variants]


def after_templates(*, skip_juice=False, diets=()):
    variants = _general_recipe_variants(diets)
    if skip_juice:
        variants += [v for v in released.reviewed_recipe_variants() if v["source_recipe_id"] == "108"]
    return {"version": 1, "recipe_fingerprints": fingerprints(variants)}


def request_recipe(*, profile=None, profiles=None, skip_juice=False, message="다른 레시피 추천해줘"):
    return recipe.build_recipe_response(
        profile or Profile(allergies=[]), None, message, profiles=profiles,
        conversation_state=after_templates(skip_juice=skip_juice),
    )


def test_only_two_registered_records_are_loaded_with_unscaled_portions():
    variants = released.reviewed_recipe_variants()
    assert [v["source_recipe_id"] for v in variants] == ["108", "3010"]
    for v in variants:
        path = DATA_DIR / f"cookrcp01-{v['source_recipe_id']}.recipe.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == released.RELEASED_RECIPES[v["source_recipe_id"]]
        record = json.loads(path.read_text())
        assert record["original"]["name"] == v["title"]
        assert len(record["original"]["steps"]) == len(v["steps"]) == 6
        assert v["servings"] == "원문 분량 · 인분 미확인"
        assert "영양" not in v and "nutrition" not in v
        assert "foodsafetykorea.go.kr" in v["source_citation"]
    assert variants[0]["ingredients"][-1] == {"name": "물", "amount": "50ml"}
    assert "물(50ml)" in json.loads((DATA_DIR / "cookrcp01-108.recipe.json").read_text())["original"]["steps"][2]


@pytest.mark.parametrize("change", ["missing", "tamper", "symlink", "unregistered"])
def test_unreviewed_or_altered_recipe_files_are_not_used(monkeypatch, tmp_path, change):
    monkeypatch.setattr(released, "RECIPE_DIR", tmp_path)
    if change == "symlink":
        for path in DATA_DIR.glob("*.recipe.json"):
            (tmp_path / path.name).symlink_to(path)
    elif change == "tamper":
        for path in DATA_DIR.glob("*.recipe.json"):
            payload = json.loads(path.read_text())
            payload["servings"] = "1인분"
            (tmp_path / path.name).write_text(json.dumps(payload))
    elif change == "unregistered":
        (tmp_path / "cookrcp01-9999.recipe.json").write_bytes((DATA_DIR / "cookrcp01-108.recipe.json").read_bytes())
    assert released.reviewed_recipe_variants() == []
    result = recipe.build_recipe_response(Profile(allergies=[]), None, "레시피 추천해줘")
    assert result.recipe is not None and result.recipe.servings == "1인분"
    assert result.sources == []


@pytest.mark.parametrize("change", ["missing", "tamper", "symlink"])
def test_recipe_rights_capture_must_be_present_unchanged(monkeypatch, tmp_path, change):
    original = knowledge_review.REFERENCE_DIR / "foodsafety-cookrcp01.rights.json"
    target = tmp_path / original.name
    if change == "tamper":
        target.write_bytes(original.read_bytes() + b" ")
    elif change == "symlink":
        target.symlink_to(original)
    monkeypatch.setattr(knowledge_review, "REFERENCE_DIR", tmp_path)
    assert released.reviewed_recipe_variants() == []


@pytest.mark.parametrize("change", ["rights", "revision", "applicability", "future", "expired"])
def test_rights_revision_and_review_period_are_rechecked(monkeypatch, change):
    old = knowledge_review.SOURCE_REVIEWS["foodsafety-cookrcp01"]
    if change in {"rights", "revision", "applicability"}:
        changes = {
            "rights": {"rights": "permission_required"},
            "revision": {"sha256": "0" * 64},
            "applicability": {"applicability": "withdrawn"},
        }[change]
        monkeypatch.setitem(knowledge_review.SOURCE_REVIEWS, "foodsafety-cookrcp01", replace(old, **changes))
    else:
        class ReviewDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 30) if change == "future" else cls(2027, 4, 2)
        monkeypatch.setattr(released, "date", ReviewDate)
    assert released.reviewed_recipe_variants() == []


@pytest.mark.parametrize("diet", ["저염식", "저당식", "고단백", "저칼로리", "저지방", "글루텐프리"])
def test_unverified_nutrition_or_certification_goal_excludes_both_records(diet):
    assert released.reviewed_recipe_variants([diet]) == []
    assert released.reviewed_recipe_variants(["채식", diet]) == []


def test_vegetarian_candidate_is_only_the_reviewed_fruit_vegetable_record():
    assert [v["source_recipe_id"] for v in released.reviewed_recipe_variants(["채식"])] == ["108"]


def test_curated_recipe_skips_llm_and_returns_public_portions_and_citation(monkeypatch):
    def forbidden(_prompt):
        pytest.fail("The reviewed recipe must not be rewritten by the LLM")
    monkeypatch.setattr(recipe, "generate_text", forbidden)
    result = request_recipe()
    assert result.recipe.title == "오렌지와 당근 만남주스"
    assert result.recipe.servings == "원문 분량 · 인분 미확인"
    assert "1인분" not in result.answer
    assert "후식" in result.answer
    assert result.sources and "RCP_SEQ 108" in result.sources[0]
    assert result.answer_source == "rules"
    public = PublicChatResponse.model_validate(result, from_attributes=True).model_dump(mode="json")
    restored = ChatResponse.model_validate_json(result.model_dump_json())
    assert public["sources"] == restored.sources
    assert public["recipe"]["servings"] == restored.recipe.servings
    assert "source_recipe_revision" not in json.dumps(public)


def test_yogurt_requires_actual_label_even_without_known_allergy():
    result = request_recipe(skip_juice=True)
    assert result.recipe and "요거트" in result.recipe.title
    assert result.overall_decision == "check_required"
    assert result.allergy_status == "unknown"
    assert result.assessment_status == "incomplete"
    assert result.per_profile_results[0]["allergy_status"] == "unknown"
    assert "실제 제품" in result.answer
    assert any("플레인요거트" in s for s in result.missing_information)
    assert all("직접 충돌 없음" not in text for text in result.recipe.allergen_check)


@pytest.mark.parametrize("profile", [
    Profile(allergies=["우유"]),
    Profile(allergies=[], vegetarian_dairy_allowed=False),
    Profile(allergies=[], disliked_ingredients=["요거트"]),
])
def test_yogurt_recipe_is_excluded_by_allergy_dairy_exclusion_and_dislike(profile):
    result = request_recipe(profile=profile, skip_juice=True)
    assert result.recipe is None
    assert result.sources == []


def test_one_active_family_conflict_excludes_recipe_without_merging_off_profile():
    profiles = [Profile(profile_id=1, profile_name="나", allergies=[]),
                Profile(profile_id=2, profile_name="아이", allergies=["우유"])]
    assert request_recipe(profiles=profiles, skip_juice=True).recipe is None
    off = profiles[1].model_copy(update={"is_active": False})
    result = request_recipe(profiles=[profiles[0], off], skip_juice=True)
    assert result.recipe and "요거트" in result.recipe.title
    assert result.recipe.applied_profiles == ["나"]
    assert [p["profile_name"] for p in result.per_profile_results] == ["나"]
    assert profiles[1].is_active is True


def test_missing_or_unsupported_allergy_is_not_certified_by_source():
    missing = request_recipe(profile=Profile())
    assert missing.recipe and missing.allergy_status == "unknown"
    assert "확인된 프로필에 한해서" in missing.answer
    unsupported = request_recipe(profile=Profile(allergies=["오렌지"]))
    assert unsupported.recipe is None and unsupported.assessment_status == "unsupported"


def test_no_active_profile_does_not_claim_personalized_recipe_check():
    result = request_recipe(profile=Profile(allergies=["우유"], is_active=False))
    assert result.recipe and result.recipe.applied_profiles == []
    assert "개인 알레르기는 적용하지 못했어요" in result.answer
    assert result.overall_decision == "check_required"


def test_disliked_carrot_excludes_both_new_recipes():
    assert request_recipe(profile=Profile(allergies=[], disliked_ingredients=["당근"])).recipe is None


def test_review_revocation_before_publication_removes_recipe_and_citation(monkeypatch):
    monkeypatch.setattr(recipe, "_general_recipe_variants", lambda _diets: [])
    # Initial load passes; revision is removed while checking the candidate.
    original = recipe.composition_issues
    def withdraw(*args, **kwargs):
        monkeypatch.setattr(released, "RELEASED_RECIPES", {})
        return original(*args, **kwargs)
    monkeypatch.setattr(recipe, "composition_issues", withdraw)
    result = recipe.build_recipe_response(Profile(allergies=[]), None, "레시피 추천해줘")
    assert result.recipe is None and result.sources == []
    assert result.overall_decision == "check_required"


def test_source_amounts_never_enter_legacy_single_serving_llm_prompt(monkeypatch):
    prompts = []
    monkeypatch.setattr(recipe, "generate_text", lambda prompt, **_kwargs: prompts.append(prompt))
    result = recipe.build_recipe_response(Profile(allergies=[]), None, "레시피 추천해줘")
    assert result.recipe.servings == "1인분" and not result.sources
    assert len(prompts) == 1
    assert "플레인요거트" not in prompts[0] and "오렌지" not in prompts[0]


def test_missing_requested_product_does_not_become_an_official_general_recipe():
    result = recipe.build_recipe_response(
        Profile(allergies=[]), Product(product_name="없는제품", product_found=False),
        "없는제품으로 레시피 추천해줘",
    )
    assert result.recipe is None and result.sources == []


def test_stateless_chat_reaches_both_records_then_stops_repeating():
    state = None
    responses = []
    for _ in range(7):
        response = chat(ChatRequest(
            message="다른 레시피 추천해줘", profile=Profile(allergies=[]), conversation_state=state,
        ))
        responses.append(response)
        state = response.conversation_state.model_dump(mode="json", exclude_none=True)
    assert "RCP_SEQ 108" in responses[4].sources[0]
    assert "RCP_SEQ 3010" in responses[5].sources[0]
    assert responses[6].recipe is None
    assert len({r.recipe.title for r in responses if r.recipe}) == 6


def test_offline_released_recipes_needs_no_private_bundle(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_bundle", lambda _path: pytest.fail("Private bundle must not be read"))
    assert cli.main(["released-recipes"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["results"]) == 2
    assert result["profile_check_required"] is True and result["llm_called"] is False
    assert all("source_recipe_revision" not in r for r in result["results"])


@pytest.mark.parametrize("change_profile", [False, True])
def test_saved_chat_reopen_preserves_source_portion_and_rechecks_current_profile(saved_chat, change_profile):
    for _ in range(5):
        juice = saved_chat("다른 레시피 추천해줘")
    assert "RCP_SEQ 108" in juice["sources"][0]
    restored = saved_chat.detail()["messages"][-1]["response"]
    assert restored == juice
    assert restored["recipe"]["servings"] == "원문 분량 · 인분 미확인"
    profile = {"allergies": ["우유"] if change_profile else []}
    followup = saved_chat("다른 레시피 추천해줘", profile)
    if change_profile:
        assert followup["recipe"] is None and followup["sources"] == []
    else:
        assert "RCP_SEQ 3010" in followup["sources"][0]
        assert followup["allergy_status"] == "unknown"
        assert followup["overall_decision"] == "check_required"
    assert saved_chat.detail()["messages"][-1]["response"] == followup


def test_recipe_specific_milk_precaution_does_not_add_a_global_yogurt_alias():
    from app.services.response_helpers import aliases_for_allergen
    assert "요거트" not in aliases_for_allergen("우유")
    yogurt = released.reviewed_recipe_variants()[1]
    conflicts = released.reviewed_recipe_condition_conflicts(yogurt, [Profile(allergies=["우유"])])
    assert len(conflicts) == 1 and "배제할 표시가 없어" in conflicts[0]
    assert released.reviewed_recipe_condition_conflicts(
        released.reviewed_recipe_variants()[0], [Profile(allergies=["우유"])],
    ) == []
