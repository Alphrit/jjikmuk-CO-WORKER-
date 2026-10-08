"""Small, revision-pinned COOKRCP01 subset, independent of raw local imports.

This is a recipe candidate library, not RAG text or nutrition certification.
Every returned candidate still needs the caller's current profile checks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Sequence

from app.core.models import ChatResponse, Profile, RecipeResult
from app.infrastructure import knowledge_review
from app.infrastructure.knowledge_sources import SOURCES, read_source
from app.services.recipe_composition import composition_issues
from app.services.response_helpers import canonical_allergen_name


RECIPE_DIR = Path(__file__).resolve().parents[1] / "data" / "reviewed_recipes"
RELEASED_RECIPES = {
    "108": "5856887d31bc27ebac9616f1d8a33514ef4fb6e95b6404bc950b8ebb2aeb5c59",
    "3010": "15d825d2fdf16169598bb138996d793b1692bada293cf23dc9097888528b4e1f",
}
RIGHTS_CAPTURE_SHA256 = "450289ed165986fe4cf54b59d734310f396d0f1be30219dcd84caa075f9bee76"
PORTION_NOTE = "원문 재료 분량을 유지했으며 인분은 확인되지 않았어요. 개인별 권장 섭취량이나 한 끼 분량을 뜻하지 않아요."


def reviewed_recipe_variants(diets: Sequence[str] = ()) -> list[dict]:
    """Fail closed per recipe; missing releases do not break fallback recipes."""
    spec = next(source for source in SOURCES if source.source_id == "foodsafety-cookrcp01")
    try:
        evidence = read_source(knowledge_review.REFERENCE_DIR, replace(
            spec, filename="foodsafety-cookrcp01.rights.json",
        ))
        if hashlib.sha256(evidence).hexdigest() != RIGHTS_CAPTURE_SHA256:
            return []
    except (OSError, ValueError):
        return []
    variants = []
    for recipe_id, expected_hash in RELEASED_RECIPES.items():
        try:
            content = read_source(RECIPE_DIR, replace(spec, filename=f"cookrcp01-{recipe_id}.recipe.json"))
            if len(content) > 16_384 or hashlib.sha256(content).hexdigest() != expected_hash:
                continue
            record = json.loads(content)
            status = knowledge_review.source_release_status(spec.source_id, record["source_sha256"])
            if (
                record["format"] != "food-reviewed-recipe-v1"
                or record["source_id"] != spec.source_id
                or record["recipe_id"] != recipe_id
                or record["content_review"] != "approved"
                or status["rights"] != "cleared"
                or status["applicability"] not in {"recipe_review_required", "reviewed"}
                or not date.fromisoformat(record["reviewed_at"]) <= date.today() <= date.fromisoformat(record["review_due"])
                or not set(diets).issubset(record["reviewed_diets"])
            ):
                continue
            # Reuse the public field bounds and shared ingredient/step checks.
            recipe = RecipeResult(
                title=record["title"], servings=record["servings"], product_name="기본 식재료",
                ingredients=record["ingredients"], steps=record["steps"],
                allergen_check=["프로필별 검사 전"],
            )
            if composition_issues(record["ingredients"], recipe.steps):
                continue
            variants.append({
                "title": recipe.title, "servings": recipe.servings,
                "ingredients": record["ingredients"], "steps": recipe.steps,
                "source_recipe_id": recipe_id, "source_recipe_revision": expected_hash,
                "category": record["category"],
                "label_check_ingredients": record["label_check_ingredients"],
                "unverified_allergens": record["unverified_allergens"],
                "source_citation": (
                    f"{spec.publisher} | {spec.title} | RCP_SEQ {recipe_id} · {recipe.title} "
                    f"(재료·조리 원문 기반, 표기 정리) | {spec.url}"
                ),
            })
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return variants


def reviewed_recipe_condition_conflicts(variant: dict, profiles: Sequence[Profile]) -> list[str]:
    """Recipe-specific uncertainty, NOT a global ingredient/allergen alias."""
    unverified = set(variant.get("unverified_allergens", []))
    conflicts = []
    for profile in profiles:
        if not profile.is_active:
            continue
        excluded = unverified & {canonical_allergen_name(a) for a in profile.resolved_allergies()}
        if "우유" in unverified and profile.vegetarian_dairy_allowed is False:
            excluded.add("우유")
        if excluded:
            conflicts.append(
                f"{profile.profile_name}: {', '.join(sorted(excluded))} 성분을 배제할 표시가 없어 이 레시피 후보 보류"
            )
    return conflicts


def finalize_reviewed_recipe(response: ChatResponse, variant: dict, diets: Sequence[str]) -> ChatResponse:
    """Keep the reviewed composition and its citation together, without LLM rewriting."""
    if variant not in reviewed_recipe_variants(diets):
        # Check again at publication; a removed/expired approval cannot be used.
        return response.model_copy(update={
            "recipe": None, "sources": [], "answer_source": "rules",
            "answer": "레시피 자료의 검토 상태가 바뀌어 제공을 보류했어요. 다시 요청해 주세요.",
            "risk_level": "unknown", "overall_decision": "check_required",
            "allergy_status": "unknown", "assessment_status": "incomplete",
            "per_profile_results": [], "missing_information": ["레시피 자료 재확인 필요"],
            "reasons": ["레시피 승인 변경"],
        })
    recipe = response.recipe
    assert recipe is not None
    missing = [*response.missing_information, PORTION_NOTE]
    profile_note = (
        "선택한 프로필이 없어 개인 알레르기는 적용하지 못했어요."
        if not response.per_profile_results
        else "알레르기 정보가 확인된 프로필에 한해서 원문 재료명을 대조했어요."
        if response.allergy_status == "unknown"
        else "원문 재료명과 현재 알레르기·제외 조건을 대조했어요."
    )
    updates = {
        "answer_source": "rules", "sources": [variant["source_citation"]],
        "answer": (
            f"{recipe.title}의 원문 기반 레시피를 안내해요. {PORTION_NOTE} "
            f"{profile_note} 실제 식재료의 성분과 교차접촉까지 보장하지는 않아요."
        ),
        "missing_information": missing,
    }
    if variant["category"] == "후식":
        updates["answer"] += " 후식용이며 식사 대용으로 선정한 것은 아니에요."
    label_items = variant["label_check_ingredients"]
    if label_items:
        note = f"{', '.join(label_items)}의 실제 제품 원재료·알레르기·교차접촉 표시를 확인해야 해요."
        missing.append(note)
        updates["answer"] += " " + note
        updates["recipe"] = recipe.model_copy(update={
            "packaged_item_cautions": [note, *recipe.packaged_item_cautions],
            "allergen_check": [
                "원문 재료명은 대조했지만 포장 재료의 실제 성분이 미확인이라 개인별 적합성은 판단 불가",
            ],
        })
        results = []
        for original in response.per_profile_results:
            result = dict(original)
            if result["overall_decision"] not in {"avoid", "caution"}:
                result.update(
                    allergy_status="unknown", overall_decision="check_required",
                    risk_level="unknown", assessment_status="incomplete", answer=note,
                    reasons=[*result["reasons"], note],
                )
            results.append(result)
        updates["per_profile_results"] = results
        if response.overall_decision not in {"avoid", "caution"}:
            updates.update(
                allergy_status="unknown", overall_decision="check_required",
                risk_level="unknown", assessment_status="incomplete",
            )
    return response.model_copy(update=updates)
