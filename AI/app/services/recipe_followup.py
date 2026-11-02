"""Replay an intact server-issued recipe; never accept client-authored steps.

The compact receipt contains only the already-public recipe and a condition
digest. It is not a credential or permission to call an external AI service.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import zlib

from app.core.models import ChatResponse, RecipeResult

_LOCAL_KEY = secrets.token_bytes(32)
MAX_RECEIPT_LENGTH = 3072
_DOMAIN = b"jjikmuk.recipe-replay.v1:"


def _key():
    # Stable across workers/restarts when the deployment's bridge secret is
    # configured. Key rotation invalidates old receipts, with a safe fallback.
    secret = os.getenv("FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET") or os.getenv("FOOD_CHATBOT_API_TOKEN")
    return secret.encode() if secret else _LOCAL_KEY


def _conditions(profiles, goals):
    payload = sorted(json.dumps(profile.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
                     for profile in profiles if profile.is_active)
    return hashlib.sha256(json.dumps([payload, sorted(goals)], ensure_ascii=False).encode()).hexdigest()


def recipe_receipt(recipe, profiles, goals):
    payload = json.dumps({"recipe": recipe.model_dump(mode="json"), "conditions": _conditions(profiles, goals)},
                         ensure_ascii=False, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(zlib.compress(payload)).decode()
    mac = hmac.new(_key(), _DOMAIN + encoded.encode(), hashlib.sha256).hexdigest()
    receipt = encoded + "." + mac
    return receipt if len(receipt) <= MAX_RECEIPT_LENGTH else None


def _read_receipt(receipt, profiles, goals):
    if not isinstance(receipt, str) or len(receipt) > MAX_RECEIPT_LENGTH:
        return None
    try:
        encoded, mac = receipt.rsplit(".", 1)
        expected = hmac.new(_key(), _DOMAIN + encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, expected):
            return None
        decoder = zlib.decompressobj()
        raw = decoder.decompress(base64.b64decode(encoded, altchars=b"-_", validate=True), 65537)
        if len(raw) > 65536 or not decoder.eof or decoder.unused_data:
            return None
        data = json.loads(raw)
        if data["conditions"] != _conditions(profiles, goals):
            return None
        return RecipeResult.model_validate(data["recipe"])
    except (ValueError, KeyError, TypeError, zlib.error):
        return None


def recipe_followup_response(message, state, profiles):
    state = dict(state or {})
    has_context = bool(state.get("recipe_fingerprints") or state.get("recipe_receipt"))
    from app.intent.request_semantics import recipe_followup_action
    action = recipe_followup_action(message)
    recap, ingredients = action == "steps", action == "ingredients"
    servings, edit = action == "servings", action == "edit"
    from app.services.diet_goal_context import read_diet_goal_update
    if read_diet_goal_update(message).touched:
        edit = None
    if not has_context or not (recap or ingredients or servings or edit):
        return None
    recipe = None
    if servings:
        answer = "현재 레시피는 1인분 기준이에요. 인분에 따른 재료 분량·조리시간 자동 조정은 아직 지원하지 않아요. 기존 레시피의 분량은 그대로 유지했어요."
    elif edit:
        answer = "기존 레시피의 특정 재료만 삭제하는 수정은 아직 지원하지 않아요. 재료를 임의로 뺀 조리법을 제공하지 않고, 기존 레시피를 유지했어요. 알레르기 제외가 필요하면 프로필 조건을 확인하고 새 레시피를 요청해 주세요."
    else:
        recipe = _read_receipt(state.get("recipe_receipt"), profiles, state.get("recipe_requested_diets", []))
        if recipe is None:
            answer = "이전 레시피 원문이나 당시 조건을 현재 요청에서 확인할 수 없어요. 대상·조건이 바뀌었다면 현재 ON인 프로필로 레시피를 다시 요청해 주세요."
        elif ingredients:
            answer = f"{recipe.title}의 재료 목록이에요.\n" + "\n".join(
                f"- {item.name}: {item.amount}" for item in recipe.ingredients
            ) + "\n이전 레시피의 분량이에요. 실제 재료의 알레르기 표시도 확인해 주세요."
        else:
            answer = f"{recipe.title}의 조리 순서예요.\n" + "\n".join(
                f"{index}. {step}" for index, step in enumerate(recipe.steps, 1)
            ) + "\n이전 조리법을 다시 표시한 내용이에요. 실제 재료의 알레르기 표시도 확인해 주세요."
    return ChatResponse(
        intent="recipe_recommend", task_type="general_chat", answer_source="rules", pipeline_stage="rules",
        answer=answer, recipe=recipe, risk_level="unknown", overall_decision="check_required",
        assessment_status="incomplete", active_profile_names=[p.profile_name for p in profiles],
        conversation_state=state, internal_conversation_state=state,
    )
