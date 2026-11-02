"""Compose verified product facts without changing assessment decisions."""

import re

from app.intent.text import detect_nutrition_keywords


def complete_product_facts(response, product, profile, message):
    if not product or not product.facts_verified or response.intent not in {
        "can_i_eat", "profile_based_recheck", "ingredient_check", "nutrition_explain", "product_summary",
    }:
        return response
    from app.services.handlers import handle_nutrition_explain
    additions, reasons = [], []
    if response.intent != "nutrition_explain" and (
        (detect_nutrition_keywords(message) and re.search(r"얼마|몇|알려|함량|수치|각각|보여|정리", message))
        or re.search(r"영양\s*성분", message)
        or re.search(r"(?:이|그)\s*수치.*포함", message)
    ):
        nutrition = handle_nutrition_explain(profile, product, message)
        additions.append(nutrition.answer)
        reasons.extend(nutrition.reasons)
    if "원재료" in message:
        ingredients = product.ingredient_text_sources()
        text = ", ".join(ingredients) if ingredients else "정보를 확인하지 못했어요"
        if not ingredients or text not in response.answer:
            additions.append(f"등록된 원재료: {text}.")
    return response.model_copy(update={
        "answer": "\n".join([response.answer, *additions]),
        "reasons": list(dict.fromkeys([*response.reasons, *reasons])),
    })


def concise_product_answer(response, product, profiles, message):
    if response.intent not in {"product_summary", "can_i_eat", "profile_based_recheck"} or not re.search(
        r"한\s*(?:줄|문장)|쉽게|짧게", message,
    ):
        return response
    if not product or not product.facts_verified:
        return response
    from app.services.response_helpers import find_triggered_allergies, find_cross_contamination_allergies
    findings = []
    for profile in profiles:
        triggered = [name for name, _ in find_triggered_allergies(profile, product)]
        contact = [name for name, _ in find_cross_contamination_allergies(profile, product)]
        if triggered:
            findings.append(f"{profile.profile_name}: {', '.join(triggered)} 알레르기와 충돌하는 표시가 있어 피해야 해요")
        elif contact:
            findings.append(f"{profile.profile_name}: {', '.join(contact)} 제조시설 경고가 있어 주의가 필요해요")
    if response.overall_decision == "avoid" and not findings:
        findings = [response.answer.splitlines()[0]]
    if not findings:
        ingredients = product.ingredient_text_sources()
        findings = ["주요 원재료 " + ", ".join(ingredients[:1]) if ingredients else "원재료 정보 부족",
                    "현재 정보만으로 섭취 적합성을 확정할 수 없어요"]
    answer = f"{product.product_name or '이 제품'} — " + "; ".join(findings) + ". 실제 포장지도 확인해 주세요."
    return response.model_copy(update={"answer": answer})
