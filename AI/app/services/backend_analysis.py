"""Explain backend verdicts without re-running the legacy label decision policy.

RAG, search, recommendations, comparisons and recipes keep their existing
pipelines. A BARCODE_ROW verdict never becomes a verdict about another product.
"""

from app.core.backend_analysis import PublicBackendAnalysis
from app.core.models import AssessmentSummary, ChatResponse
from app.core.public_product import public_product_payload, public_conversation_state

PRODUCT_INTENTS = {"can_i_eat", "profile_based_recheck", "ingredient_check", "nutrition_explain", "product_summary"}
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


def _facts(req, intent):
    product = req.product
    if product is None:
        return ""
    if intent == "nutrition_explain":
        from app.intent.text import detect_nutrition_keywords
        from app.services.handlers import NUTRITION_LABELS, NUTRITION_UNITS
        requested = detect_nutrition_keywords(req.message)
        # detect_nutrition_keywords returns display names; expose only requested
        # nutrients, or all registered values for a broad nutrition request.
        keys = [key for key, label in NUTRITION_LABELS.items() if label in requested] if requested else list(NUTRITION_LABELS)
        lines = [f"{NUTRITION_LABELS[key]}: {product.nutrition[key]:g}{NUTRITION_UNITS[key]}"
                 if key in product.nutrition else f"{NUTRITION_LABELS[key]}: 정보 없음" for key in keys]
        return "등록된 영양정보 — " + ", ".join(lines) + ". 기준량이 전달되지 않아 1회분·한 봉지 섭취량으로 환산할 수 없어요.\n"
    if intent in {"ingredient_check", "product_summary"}:
        ingredients = product.rawmtrl_nm or ", ".join(product.ingredients)
        return ("등록된 원재료: " + ingredients[:1500] + "\n") if ingredients else "등록된 원재료 정보가 부족해요.\n"
    return ""


def decision_response(req, intent):
    ctx = req.analysis_context
    profiles = [req.profile] if req.profile else req.profiles
    results = _profile_results(req, intent)
    decision = ctx.analysis
    axes = _axes(decision)
    text = _facts(req, intent)
    if req.product:
        text = f"{req.product.product_name or '선택한 상품'} 기준으로 확인했어요.\n" + text
    if results:
        text += "\n".join(f"{item['profile_name']}: {item['answer']}" for item in results)
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
    intent = rule_based_classify_intent(req.message, req.product, profile, req.chat_history)
    if intent in {"unknown", "ambiguous"} and personal_assessment_request(req.message):
        intent = "can_i_eat"
    named_query = _explicit_named_product_query(req.message, profile)
    same_product = bool(req.product and (not named_query or candidate_matches_product_query(named_query, req.product)))
    if intent in {"unknown", "ambiguous"} and named_query and any(word in req.message for word in ("찾아", "검색")):
        intent = "product_summary"
    if not interrupt and same_product and intent in PRODUCT_INTENTS:
        from app.services.food_conditions import prepare_food_conditions
        prepared = prepare_food_conditions(req.message, profiles, state, profiles)
        def conditions(p):
            return (p.resolved_allergies(), p.resolved_special_diets(),
                    p.vegetarian_dairy_allowed, p.vegetarian_egg_allowed, p._conversation_exclusions)
        if (prepared.allergy_guard or prepared.ambiguous_diet
                or [conditions(p) for p in profiles] != [conditions(p) for p in prepared.profiles]):
            # A new turn-local condition was not part of the backend verdict.
            # Preserve the existing parser, but never re-label that old verdict
            # as a decision for the newly stated conditions.
            text = "방금 말씀한 조건은 전달받은 개인별 판정에 반영되지 않았어요. 프로필 조건을 반영해 이 상품을 다시 확인해주세요."
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
        application = "product_selection_required"
        text = f"{response.current_product.product_name or '찾은 상품'}을 찾았어요. 이 상품을 선택해 현재 프로필의 판정 정보를 받아야 개인 조건을 확인할 수 있어요."
        response = response.model_copy(update={
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
