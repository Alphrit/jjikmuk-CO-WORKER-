"""Explain backend verdicts without re-running the legacy label decision policy.

RAG, search, recommendations, comparisons and recipes keep their existing
pipelines. A BARCODE_ROW verdict never becomes a verdict about another product.
"""

import re

from app.core.backend_analysis import PublicBackendAnalysis
from app.core.models import AssessmentSummary, ChatResponse
from app.core.public_product import public_product_payload, public_conversation_state
from app.intent.common import PRODUCT_INTENTS
from app.services.product_facts import (
    asked_ingredient_text, ingredient_label_text, intolerance_text, name_only_allergen_warning, product_facts_text,
)

_SAFETY_TEXT = {
    "PASS": "선택한 알레르기 조건과 표시 정보 사이에서 충돌이 발견되지 않았어요. 섭취 안전을 보장하는 뜻은 아니에요.",
    "DANGER": "선택한 알레르기 조건과 충돌하는 표시가 확인됐어요. 이 상품은 피해주세요.",
    "UNKNOWN": "알레르기 판정에 필요한 정보가 부족해요. 충돌이 없다고 볼 수 없으니 포장 표시를 확인해주세요.",
    "NOT_EVALUATED": "선택한 사람이 없어 개인별 알레르기·식단 조건을 검사하지 않았어요.",
}
_DIET_TEXT = {
    "PASS": "요청한 필수 식단 조건을 충족하는 것으로 판정됐어요.",
    "MISMATCH": "요청한 필수 식단 조건과 맞지 않는 항목이 있어요.",
    "UNKNOWN": "필수 식단 조건을 충족하는지 확인할 정보가 부족해요.",
    "NOT_EVALUATED": "식단 적합성은 검사하지 않았어요.",
}
_DIET_LABELS = {
    "vegan": "비건", "lactoVegetarian": "락토 채식", "ovoVegetarian": "오보 채식",
    "lactoOvoVegetarian": "락토오보 채식", "pescatarian": "페스코 채식", "pollotarian": "폴로 채식",
    "lowSugar": "저당", "lowSodium": "저나트륨", "glutenFree": "글루텐프리",
    "lowCalorie": "저열량", "lowFat": "저지방", "highProtein": "고단백",
}


def _axes(decision):
    if decision is None:
        return dict(risk_level="unknown", overall_decision="check_required", allergy_status="unknown",
                    diet_match="unknown", assessment_status="incomplete")
    status, diet = decision.status, decision.dietConstraints.status
    # Allergy and dietary conflicts are independent. Missing information never
    # becomes a no-conflict result; NOT_EVALUATED remains explicit in metadata.
    overall = ("avoid" if status == "DANGER" else "caution" if diet == "MISMATCH" else
               "check_required" if status in {"UNKNOWN", "NOT_EVALUATED"} or diet == "UNKNOWN" else "no_known_conflict")
    return dict(
        risk_level={"avoid": "high", "caution": "medium", "check_required": "unknown", "no_known_conflict": "low"}[overall],
        overall_decision=overall,
        allergy_status={"PASS": "no_known_match", "DANGER": "conflict"}.get(status, "unknown"),
        diet_match={"PASS": "match", "MISMATCH": "not_match"}.get(diet, "unknown"),
        assessment_status="complete" if status in {"PASS", "DANGER"} and diet in {"PASS", "MISMATCH"} else "incomplete",
    )


def _decision_text(decision):
    if decision is None:
        return "선택한 상품의 판정 정보가 없어요. 상품을 선택하면 현재 프로필 기준으로 확인할 수 있어요."
    text = _SAFETY_TEXT[decision.status]
    if decision.status == "DANGER" and decision.dangerousIngredients:
        text += " 충돌 항목: " + ", ".join(x[:40] for x in decision.dangerousIngredients[:3]) + (" 등" if len(decision.dangerousIngredients) > 3 else "") + "."
    diet = decision.dietConstraints
    if decision.status != "NOT_EVALUATED":
        text += " " + (_DIET_TEXT[diet.status] if diet.requiredFilters else "지정된 필수 식단 조건은 없어요.")
        if diet.failedFilters:
            text += " 미충족 항목: " + ", ".join(_DIET_LABELS.get(x, x)[:30] for x in diet.failedFilters[:3]) + (" 등" if len(diet.failedFilters) > 3 else "") + "."
    return text


def _summary(req, application):
    ctx = req.analysis_context
    return PublicBackendAnalysis(
        contextId=ctx.contextId, policyVersion=ctx.policyVersion, profileSource=ctx.profileSource,
        selectionMode=ctx.selection.mode, productResolution=ctx.productResolution,
        barcode=req.product.barcode if req.product else None,
        status=ctx.analysis.status if ctx.analysis else None,
        dietStatus=ctx.analysis.dietConstraints.status if ctx.analysis else None,
        application=application,
    )


def _profile_results(req, intent, *, decisions=True):
    profiles = [req.profile] if req.profile else req.profiles
    by_key = {item.profileKey: item.analysis for item in req.analysis_context.profileAnalyses}
    results = []
    for p in profiles:
        decision = by_key[p.profile_key] if decisions else None
        text = _decision_text(decision)
        results.append(dict(
            profile_id=p.profile_id, profile_name=p.profile_name, target_type=p.target_type,
            profileKey=p.profile_key, profileSource=p.profile_source, intent=intent,
            backend_status=decision.status if decision else None,
            backend_diet_status=decision.dietConstraints.status if decision else None,
            **_axes(decision), answer=text, reasons=[text[:1000]],
        ))
    return results


def decision_response(req, intent, *, lead=""):
    ctx = req.analysis_context
    profiles = [req.profile] if req.profile else req.profiles
    results = _profile_results(req, intent)
    decision = ctx.analysis
    axes = _axes(decision)
    text = product_facts_text(req.product, req.message, intent)
    if req.product:
        text = (f"{req.product.product_name or '선택한 상품'} 기준으로 확인했어요.\n"
                + lead + intolerance_text(req.product, req.message) + text)
    if results:
        text += "\n".join(f"{item['profile_name']}: {item['answer']}" for item in results)
        if req.product:
            # A missing label field must not hide an allergen the name states.
            warnings = [line for profile in profiles for line in name_only_allergen_warning(req.product, profile)]
            if warnings:
                text += "\n" + "\n".join(warnings)
    else:
        text += _SAFETY_TEXT["NOT_EVALUATED"]
    if ctx.profileSource == "CLIENT_REQUEST" and profiles:
        text += "\n이번 요청에서 선택한 프로필 조건을 기준으로 한 결과예요."
    text += "\n질환이나 개인별 섭취 가능량은 이 판정에 포함되지 않아요."
    state = dict(req.conversation_state)
    state.update(last_intent=intent)
    if req.product:
        state["last_confirmed_product"] = {"barcode": req.product.barcode, "product_name": req.product.product_name}
        for key in ("pending_product_candidates", "unresolved_product_query", "pending_target_product", "pending_intent"):
            state.pop(key, None)
    summary_decision = ("not_assessed" if not profiles else
        {"avoid": "avoid", "caution": "caution", "no_known_conflict": "no_conflict_found", "check_required": "insufficient_information"}[axes["overall_decision"]])
    return ChatResponse(
        intent=intent, answer=text, answer_source="backend", pipeline_stage="backend_analysis",
        task_type="product_analysis", **axes, active_profile_names=[p.profile_name for p in profiles],
        profile_name=profiles[0].profile_name if len(profiles) == 1 else None,
        per_profile_results=results, current_product=public_product_payload(req.product) if req.product else None,
        nutrition_facts=public_product_payload(req.product)["nutrition"] if req.product else {},
        reasons=[_decision_text(decision)[:1000]], backend_analysis=_summary(req, "product_decision"),
        policy_version=ctx.policyVersion,
        assessment_summary=AssessmentSummary(
            scope="overall_intake", decision=summary_decision,
            evidence_status="not_applicable" if not profiles else "insufficient" if axes["assessment_status"] == "incomplete" else "limited",
            safety_assessed=bool(profiles and decision), personalized=bool(profiles),
            explanation="현재 요청에 포함된 백엔드의 상품·개인별 판정을 설명한 결과입니다. 실제 섭취 안전의 보증은 아닙니다.",
        ),
        recommended_questions=["이 상품의 원재료를 알려줘", "이 상품의 영양정보를 알려줘", "대체 상품을 추천해줘"],
        conversation_state=public_conversation_state(state), internal_conversation_state=state,
    )


def _candidate_choice(products, search_query, message, intent):
    """A candidate-card reply (the app resends the question with the tapped barcode)."""
    from app.services.conversation_router import build_product_confirmation_response
    return build_product_confirmation_response(
        products, search_query=search_query, reason="multiple_matches" if len(products) > 1 else "low_confidence",
        original_message=message, pending_intent=intent,
    )


def _stale_selection_choice(req, named_query, intent):
    """The sent barcode predates an open candidate list in the history.

    With a fixed app the barcode is the last selection. After '두유 먹어도 돼?'
    offered 두유 candidates, '먹어도 되냐고' still carries the old 쌀새우깡
    barcode; answering for it would answer the wrong product. A barcode that is
    one of the open candidates is the choice itself and is answered normally.
    """
    from app.services.candidate_prompts import leaves_choice_open
    from app.services.product import find_product_candidates_from_message
    from app.services.product_search import extract_product_search_query
    if req.product is None or named_query or not req.chat_history:
        return None
    last_reply = next((item.content for item in reversed(req.chat_history) if item.role == "assistant"), "")
    if not leaves_choice_open(last_reply):
        return None
    for item in reversed(req.chat_history):
        candidates = find_product_candidates_from_message(item.content, limit=3) if item.role == "user" else []
        if candidates:
            break
    else:
        return None
    if req.product.barcode in {candidate.barcode for candidate in candidates}:
        return None
    return _candidate_choice(candidates, extract_product_search_query(item.content), req.message, intent)


def same_product_name(query, product):
    from app.services.product_search import candidate_matches_product_query, compact_search_text
    return (compact_search_text(query) == compact_search_text(product.product_name or "")
            or candidate_matches_product_query(query, product))


def run_backend_pipeline(req, legacy_pipeline):
    """Return (response, product, audit context), logging only in the API wrapper."""
    from app.api.adapters import normalize_active_profiles
    from app.intent.request_semantics import current_topic_message, personal_assessment_request
    from app.intent.rules import rule_based_classify_intent
    from app.services.condition_subject import unresolved_profile_target_response
    from app.services.conversation_controls import conversation_control_response
    from app.services.emergency_triage import build_emergency_triage_response
    from app.services.request_language import standalone_cancellation_response
    from app.services.conversation_router import _explicit_named_product_query
    from app.services.product_search import candidate_matches_product_query

    profiles = normalize_active_profiles(req)
    # Backend keys for request snapshots are local to THIS request. Never carry
    # last turn's allergy/vegetarian exceptions onto a different request:0.
    state = {k: v for k, v in req.conversation_state.items() if k not in {"vegetarian_allowances", "pending_allergy"}}
    req = req.model_copy(update={"message": current_topic_message(req.message), "conversation_state": state})
    profile = profiles[0] if profiles else None
    interrupt = (build_emergency_triage_response(req.message, profiles=profiles, has_food_context=bool(req.product))
                 or conversation_control_response(req.message, state)
                 or standalone_cancellation_response(req.message, state, profiles=profiles, known_profiles=profiles)
                 or unresolved_profile_target_response(req.message, profiles, profiles, state))
    named_query = _explicit_named_product_query(req.message, profile)
    # '바나나맛우유 먹어도 돼?' asks about the product, not about 우유: classify
    # without the product's own name.
    intent_message = req.message
    for name in dict.fromkeys(filter(None, [named_query, req.product.product_name if req.product else None])):
        intent_message = intent_message.replace(name, " ")
    intent = rule_based_classify_intent(intent_message if req.product else req.message, req.product, profile, req.chat_history)
    if intent in {"unknown", "ambiguous"} and personal_assessment_request(req.message):
        intent = "can_i_eat"
    elif intent in {"unknown", "ambiguous"} and req.product and re.fullmatch(r"\s*\S+(?:은|는|도)\s*[?？]*\s*", intent_message):
        from app.intent.text import detect_ingredient_keyword
        if detect_ingredient_keyword(intent_message):
            # '우유는?' about the selected product asks for that ingredient.
            intent = "ingredient_check"
        elif named_query and same_product_name(named_query, req.product):
            intent = "product_summary"
    elif intent in {"unknown", "ambiguous"} and req.product and named_query and same_product_name(named_query, req.product):
        # '새우깡은?' with 새우깡 selected (e.g. right after tapping its card)
        # asks about the selected product; asking to select it again loops.
        intent = "product_summary"
    same_product = bool(req.product and (not named_query or candidate_matches_product_query(named_query, req.product)))
    if intent in {"unknown", "ambiguous"} and named_query and any(word in req.message for word in ("찾아", "검색")):
        intent = "product_summary"
    stale = None if interrupt else _stale_selection_choice(req, named_query, intent)
    if stale is not None:
        stale = stale.model_copy(update={
            "backend_analysis": _summary(req, "product_selection_required"),
            "per_profile_results": _profile_results(req, intent, decisions=False),
        })
        return stale, None, dict(profile=profile, product=None, barcode=req.product.barcode,
                                 report_number=req.product.report_number, message=req.message)
    if not interrupt and same_product and intent in PRODUCT_INTENTS:
        from app.services.food_conditions import prepare_food_conditions
        prepared = prepare_food_conditions(req.message, profiles, state, profiles)
        def conditions(p):
            return (p.resolved_allergies(), p.resolved_special_diets(),
                    p.vegetarian_dairy_allowed, p.vegetarian_egg_allowed, p._conversation_exclusions)
        if (prepared.allergy_guard or prepared.ambiguous_diet
                or [conditions(p) for p in profiles] != [conditions(p) for p in prepared.profiles]):
            # A new turn-local condition was not part of the backend verdict:
            # never re-label that verdict as a decision for the new condition.
            stated = dict.fromkeys(term for before, after in zip(profiles, prepared.profiles)
                                   for term in after.resolved_allergies() if term not in before.resolved_allergies())
            facts = ("".join(ingredient_label_text(req.product, term) for term in stated)
                     or asked_ingredient_text(req.product, req.message))
            results = _profile_results(req, "can_i_eat")
            if (_axes(req.analysis_context.analysis)["overall_decision"] in {"avoid", "caution"}
                    and results and all(item["backend_status"] != "PASS" for item in results)):
                # A warning for the saved profile conditions still holds and
                # must stay visible; only the new condition is outside it. Any
                # PASS line would be outdated, so mixed results are withheld.
                lead = "새로 말씀한 조건이 누구의 것인지 알려 주시면 그 조건도 확인할게요.\n" if prepared.allergy_guard else ""
                lead += facts + "방금 말씀한 조건은 아래 프로필 기준 판정에 반영되지 않았어요. 계속 적용하려면 설정에서 프로필 정보를 바꿔 주세요.\n"
                response = decision_response(req, "profile_based_recheck", lead=lead)
                activity = dict(profile=profile, product=req.product, barcode=req.product.barcode,
                                report_number=req.product.report_number, message=req.message)
                return response, req.product, activity
            # A PASS without the new condition is outdated: withhold it. Label
            # facts for the stated condition are still shown without a verdict.
            text = (f"{req.product.product_name or '선택한 상품'} 기준으로 확인했어요.\n" + facts
                    + "방금 말씀한 조건은 전달받은 개인별 판정에 반영되지 않았어요. 프로필 조건을 반영해 이 상품을 다시 확인해주세요.")
            response = (prepared.allergy_guard or ChatResponse(intent="profile_based_recheck", answer=text,
                **_axes(None), active_profile_names=[p.profile_name for p in profiles], reasons=[text]))
            response = response.model_copy(update={"backend_analysis": _summary(req, "context_only")})
            activity = dict(profile=profile, product=None, barcode=req.product.barcode,
                            report_number=req.product.report_number, message=req.message)
            return response, None, activity
        response = decision_response(req, intent)
        resolved = req.product
        activity = dict(profile=profile, product=resolved, barcode=resolved.barcode,
                        report_number=resolved.report_number, message=req.message)
        return response, resolved, activity

    # Keep all legacy task handlers. Product decisions they compute for an
    # unselected barcode must not masquerade as a backend v2 decision.
    response, resolved, activity = legacy_pipeline(req, log_activity=False)
    application = "context_only"
    found_query = bool(named_query and response.intent in {"unknown", "ambiguous"})
    if not interrupt and (response.intent in PRODUCT_INTENTS or found_query) and response.current_product is not None:
        if req.product and resolved is not None and resolved.barcode and resolved.barcode == req.product.barcode:
            # The selection being asked for is the one already made: answer it.
            response = decision_response(req, response.intent if response.intent in PRODUCT_INTENTS else "product_summary")
            activity = dict(profile=profile, product=req.product, barcode=req.product.barcode,
                            report_number=req.product.report_number, message=req.message)
            return response, req.product, activity
        application = "product_selection_required"
        from app.services.candidate_prompts import CHOOSE_FROM_CANDIDATES
        from app.services.product import find_product_candidates_from_message
        from app.services.response_helpers import object_particle
        name = response.current_product.product_name or "찾은 상품"
        text = (f"{name}{object_particle(name)} 찾았어요. {CHOOSE_FROM_CANDIDATES}. "
                "선택하면 현재 프로필의 판정 정보로 확인할 수 있어요.")
        # Without cards the reply asked for a choice the app could not make.
        products = [resolved] if resolved is not None else []
        products += [candidate for candidate in find_product_candidates_from_message(req.message, limit=3)
                     if not resolved or candidate.barcode != resolved.barcode]
        choice = _candidate_choice(products[:3], named_query or "", req.message, response.intent) if products else None
        response = response.model_copy(update={
            **({"product_candidates": choice.product_candidates, "conversation_state": choice.conversation_state,
                "internal_conversation_state": choice.internal_conversation_state} if choice else {}),
            **_axes(None), "intent": "product_summary" if found_query else response.intent,
            "answer": text, "reasons": [text], "per_profile_results": _profile_results(req, "product_summary" if found_query else response.intent, decisions=False),
            "assessment_summary": AssessmentSummary(scope="product_selection", decision="not_assessed",
                evidence_status="not_applicable", safety_assessed=False, personalized=False, explanation=text),
        })
    elif response.per_profile_results:
        by_subject = {(p.profile_id, p.target_type): p for p in profiles}
        response = response.model_copy(update={"per_profile_results": [
            item.model_copy(update={"profileKey": by_subject[(item.profile_id, item.target_type)].profile_key,
                                    "profileSource": by_subject[(item.profile_id, item.target_type)].profile_source})
            for item in response.per_profile_results if (item.profile_id, item.target_type) in by_subject
        ]})
    return response.model_copy(update={"backend_analysis": _summary(req, application)}), resolved, activity
