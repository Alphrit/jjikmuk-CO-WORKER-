"""Synthetic JSON using the exact DTO/Map fields of backend eae67ec.

No production profiles, secrets or verdicts are copied into these fixtures.
"""
from copy import deepcopy
from uuid import uuid4

from app.core.backend_analysis import CONTRACT_HEADER, CONTEXT_HEADER, ANALYSIS_VERSION

TOKEN = "v2-test-internal-token"


def selection(mode="provided", ids=()):
    return dict(mode=mode, analysisApplied=mode != "none", includeSelf=mode == "self", familyProfileIds=list(ids))


def decision(status="PASS", diet="PASS", *, selected=None):
    filters = [] if diet in {"PASS", "NOT_EVALUATED"} else ["vegan"]
    return dict(status=status, analysisApplied=status != "NOT_EVALUATED", selection=selected or selection(),
        isDangerous=status == "DANGER", dangerousIngredients=["우유"] if status == "DANGER" else [],
        conflictingAllergenIds=["milk"] if status == "DANGER" else [], evidenceSources=["allergy"],
        evidenceLevel="DECLARED_LABEL", verificationRequired=status == "UNKNOWN", message="백엔드 판정 설명",
        dietConstraints=dict(status=diet, requiredFilters=filters, satisfiedFilters=[],
            failedFilters=filters if diet == "MISMATCH" else [], uncertainFilters=filters if diet == "UNKNOWN" else [],
            evidenceOrigin="SOURCE", confidence=0.95, message="백엔드 식단 판정 설명", attributes=[]))


def request_payload(statuses=("DANGER", "PASS"), *, product=True, diets=None):
    people = [dict(id=None, nickname=["엄마", "아빠"][i % 2], allergies="우유" if status == "DANGER" else "땅콩",
        diseases=None, specialDiet=None, dislikedIngredients=None, profileKey=f"request:{i}",
        profileSource="CLIENT_REQUEST", profile_id=i + 1, target_type="provided") for i, status in enumerate(statuses)]
    diets = diets or ["PASS"] * len(people)
    mode = "provided" if people else "none"
    group_status = "DANGER" if "DANGER" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS" if people else "NOT_EVALUATED"
    group_diet = "MISMATCH" if "MISMATCH" in diets else "UNKNOWN" if "UNKNOWN" in diets else "PASS" if people else "NOT_EVALUATED"
    evidence = dict(allergyEvidenceLevel="DECLARED_LABEL", allergyConfidence=0.95,
        nutritionOrigin="SOURCE", nutritionConfidence=0.95, dietaryOrigin="SOURCE", dietaryConfidence=0.95)
    return dict(message="이 제품 먹어도 돼?", profile=people[0] if len(people) == 1 else None,
        profiles=people if len(people) != 1 else [],
        product=dict(barcode="8800000000001", productName="합성 시험 우유", allergy="우유",
            rawMaterialName="우유", energyKcal=60.0, proteinG=3.0, sodiumMg=40.0, vegan=True) if product else None,
        chat_history=[], analysisContext=dict(contextId=str(uuid4()), contractVersion=ANALYSIS_VERSION,
            policyVersion="recommendation-v2", selection=selection(mode),
            effectiveConditions=dict(allergyIds=["milk", "peanut"], unknownAllergyTerms=[], requiredDietFilterIds=[],
                diseases=[], dislikedIngredients=[]) if people else None,
            productResolution="FOUND" if product else "NOT_PROVIDED", decisionScope="BARCODE_ROW",
            analysis=decision(group_status, group_diet, selected=selection(mode)) if product else None,
            dataEvidence=evidence if product else None, decisionAuthority="BACKEND", historyIsAuthoritative=False,
            storedDietFlagsAreAuthoritative=False, profileSource="CLIENT_REQUEST",
            profileAnalyses=[dict(profileKey=p["profileKey"], analysis=decision(s, d) if product else None)
                for p, s, d in zip(people, statuses, diets)]))


def headers(payload):
    return {"X-Food-Chatbot-Token": TOKEN, CONTRACT_HEADER: ANALYSIS_VERSION,
            CONTEXT_HEADER: payload["analysisContext"]["contextId"]}


def server_profiles(payload):
    payload = deepcopy(payload)
    people = [payload["profile"]] if payload["profile"] else payload["profiles"]
    context = payload["analysisContext"]
    context["profileSource"] = "SERVER" if people else "NONE"
    selected = selection("self" if len(people) == 1 else "selected" if people else "none", [41] if len(people) > 1 else [])
    selected["includeSelf"] = bool(people)
    context["selection"] = selected
    if context["analysis"]:
        context["analysis"]["selection"] = selected
    for i, p in enumerate(people):
        kind = "self" if i == 0 else "family"
        p.update(profileKey=f"{kind}:41", profileSource="SERVER", profile_id=41, target_type=kind, id=41 if i == 0 else None)
        row = context["profileAnalyses"][i]
        row["profileKey"] = p["profileKey"]
        if row["analysis"]:
            row["analysis"]["selection"] = selection("self" if i == 0 else "selected", [41] if i else [])
    return payload
