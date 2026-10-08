"""Local keyword preview, not a live retriever or personalized recommendation."""

from __future__ import annotations

import re

from app.core.models import Product, Profile
from app.infrastructure.knowledge_sources import SOURCES, normalized
from app.services.response_helpers import (
    find_cross_contamination_allergies,
    find_triggered_allergies,
    unsupported_profile_allergies,
)


def preview_released_sources(query: str | None = None) -> list[dict]:
    """Inspect exactly the approved RAG input locally; never build an index."""
    from app.infrastructure import rag

    tokens = []
    if query is not None:
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError("검색어는 1~200자여야 합니다.")
        tokens = normalized(query).casefold().split()
    manifest = rag._build_document_manifest()
    results = []
    for document in rag._load_documents(expected_manifest=manifest):
        if document.metadata.get("source_kind") != "official_excerpt":
            continue
        reference = rag._official_reference_from_file(
            document.metadata["source"], document.metadata["source_sha256"],
        )
        text = reference["excerpt_content"]
        if tokens and not all(token in normalized(text).casefold() for token in tokens):
            continue
        results.append({
            "source": reference["source"], "citation": reference["citation"], "text": text,
            "usage_scope": "general_food_explanation", "runtime_eligible": True,
        })
    return results


def review_recipe_candidate(bundle: dict, recipe_id: str, allergies: list[str] | None = None) -> dict:
    """A human-review worksheet, not an automatic recipe approval mechanism."""
    from app.infrastructure.knowledge_review import source_release_status
    from app.services.recipe_source_structure import structure_ingredients
    from app.services.recipe_validation import unlisted_additions, unsupported_claim

    if not isinstance(recipe_id, str) or not recipe_id.isascii() or not recipe_id.isdigit():
        raise ValueError("레시피 ID는 CSV의 숫자 ID를 사용하세요.")
    matches = [r for r in [*bundle["recipes"], *bundle["rejected_recipes"]] if r["recipe_id"] == recipe_id]
    if len(matches) != 1:
        raise ValueError("레시피 ID가 없거나 중복됩니다.")
    recipe = matches[0]
    sources = {s.source_id: s for s in SOURCES}
    if recipe["source_id"] not in sources or recipe.get("review_status") != "pending":
        raise ValueError("알 수 없거나 검토 상태가 변경된 자료입니다. 다시 추출하세요.")
    source = sources[recipe["source_id"]]
    source_hash = next(s["sha256"] for s in bundle["sources"] if s["source_id"] == source.source_id)
    # Recompute instead of trusting an old cached parser result in the bundle.
    structure = structure_ingredients(recipe["ingredients_text"], recipe["name"]) if recipe["ingredients_text"] else None
    names = [i["name"] for i in structure["ingredients"]] if structure else []
    checks = []
    for step in recipe["steps"]:
        checks.append({
            **step,
            "listed_names_found": [name for name in names if name in step["text"]],
            "possible_unlisted_mentions": unlisted_additions([step["text"]], names),
            "claim_review_required": bool(
                unsupported_claim(step["text"])
                or re.search(r"치료|예방|완치|면역력|혈압|혈당", step["text"])
            ),
        })
    step_numbers = [step["number"] for step in recipe["steps"]]
    return {
        "recipe_id": recipe_id, "name": recipe["name"], "source_url": source.url,
        "source_sha256": source_hash,
        "source_release": source_release_status(source.source_id, source_hash),
        "ingredients_text": recipe["ingredients_text"], "ingredient_structure": structure,
        "steps": checks, "import_issues": recipe["issues"],
        "step_number_gap": step_numbers != list(range(1, len(step_numbers) + 1)),
        "nutrition_basis": "unverified", "review_status": "pending",
        "recommendation_eligible": False, "precheck": screen_recipe_text(recipe, allergies or []),
        "limitations": [
            "문자열 대조이므로 재료의 별칭·양념장 같은 묶음·조리 표현을 오인할 수 있습니다.",
            "검사에 걸리지 않아도 재료가 빠짐없이 확인됐거나 안전하다는 의미는 아닙니다.",
            "인분·사용량을 자동 보정하지 않습니다. 복합 재료 라벨·조리 조건과 이용 허락은 별도 검토해야 합니다.",
        ],
    }


def screen_recipe_text(recipe: dict, allergies: list[str]) -> dict:
    """Reuse existing alias checks without asserting ingredients are verified.

    No match is NOT an absence-of-allergen claim. Sauces, quantities, preparation
    and production cross-contact need further review even for official recipes.
    """
    profile = Profile(allergies=allergies)
    product = Product(
        product_name=recipe["name"],
        rawmtrl_nm="\n".join([
            recipe["ingredients_text"], *(step["text"] for step in recipe["steps"]),
        ]),
        facts_verified=False,
    )
    return {
        "status": "review_required", "personalized_recommendation": False,
        "allergen_matches": [
            {"allergen": allergy, "matched_terms": terms}
            for allergy, terms in find_triggered_allergies(profile, product)
        ],
        "cross_contact_mentions": [
            {"allergen": allergy, "matched_terms": terms}
            for allergy, terms in find_cross_contamination_allergies(profile, product)
        ],
        "unsupported_allergies": unsupported_profile_allergies(profile),
        "warning": (
            "원문 문자열 사전 검사입니다. 일치 성분이 없어도 프로필 기준 적합으로 판정하지 않습니다. "
            "복합 재료의 실제 제품 라벨·분량·조리 단계와 교차오염을 별도로 확인해야 합니다."
        ),
    }


def search_prepared_sources(
    bundle: dict, query: str, *, kind: str, allergies: list[str] | None = None, limit: int = 3,
) -> list[dict]:
    if kind not in {"pdf", "recipe"}:
        raise ValueError("검색 종류는 pdf 또는 recipe여야 합니다.")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
        raise ValueError("검색 건수는 1~20이어야 합니다.")
    query = normalized(query).strip().casefold()
    if not query or len(query) > 200:
        raise ValueError("검색어는 1~200자여야 합니다.")
    tokens = query.split()
    sources = {source.source_id: source for source in SOURCES}
    source_hashes = {source["source_id"]: source["sha256"] for source in bundle["sources"]}
    ranked = []
    records = bundle["pdf_pages"] if kind == "pdf" else bundle["recipes"]
    for record in records:
        if record.get("review_status") != "pending" or record.get("source_id") not in sources:
            raise ValueError("알 수 없거나 승인 상태가 변경된 검색 자료입니다. 다시 추출하세요.")
        if kind == "pdf":
            if record["extraction_status"] != "text_extracted":
                continue
            title, content = "", record["text"]
        else:
            if record["issues"]:
                continue
            title = record["name"]
            content = record["ingredients_text"] + "\n" + "\n".join(
                step["text"] for step in record["steps"]
            )
        full_text = normalized(title + "\n" + content).casefold()
        if not all(token in full_text for token in tokens):
            continue
        score = sum(
            10 * (token in title.casefold()) + min(full_text.count(token), 5)
            for token in tokens
        )
        ranked.append((score, record))
    ranked.sort(key=lambda pair: -pair[0])  # Stable ties preserve source order.
    results = []
    for _score, record in ranked[:limit]:
        source = sources[record["source_id"]]
        result = {
            "source_id": source.source_id, "source_title": source.title,
            "source_url": source.url, "review_status": "pending",
            "source_sha256": source_hashes[source.source_id],
        }
        if kind == "pdf":
            result.update(page=record["page"], text=record["text"])
        else:
            result.update(
                recipe_id=record["recipe_id"], name=record["name"],
                ingredients_text=record["ingredients_text"], steps=record["steps"],
                nutrition_basis="unverified", servings=None,
                precheck=screen_recipe_text(record, allergies or []),
                ingredient_structure=record.get("ingredient_structure"),
            )
        results.append(result)
    return results
