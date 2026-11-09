"""Limits, timeouts and keys aligned with JJIKMUK-Backend (main 203cb21)."""

import time

import pytest
from pydantic import ValidationError

from app.api.request_guard import _client_key
from app.core.models import ChatRequest, Product, Profile, MAX_PROFILE_LIST_ITEMS
from app.infrastructure import external_ai_consent as consent
from app.infrastructure import llm
from backend_v2_helpers import request_payload


def test_request_product_keeps_backend_food_categories():
    product = Product.model_validate({"barcode": "1", "productName": "아몬드브리즈 언스위트",
                                      "foodCategories": "음료", "foodType": "혼합음료"})
    assert product.category_large == "음료"
    assert product.prdlst_dcnm == "혼합음료"


def test_v2_request_accepts_backend_sized_product_name():
    payload = request_payload()
    payload["product"]["productName"] = "가" * 900  # backend column allows 1000
    assert ChatRequest.model_validate(payload).product.product_name == "가" * 900
    payload["product"]["productName"] = "가" * 1001
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(payload)


def test_profile_accepts_backend_term_count():
    assert MAX_PROFILE_LIST_ITEMS >= 50
    profile = Profile(name="검증", allergies=[f"알레르기{index}" for index in range(50)])
    assert len(profile.allergies) == 50


def test_llm_call_is_skipped_without_spending_budget_when_request_time_is_up(monkeypatch):
    monkeypatch.setattr(llm, "reserve_call", lambda *_: pytest.fail("no call may be reserved"))
    token = llm._REQUEST_DEADLINE.set(time.monotonic() + 0.5)
    try:
        assert llm._call_timeout() is None
        assert llm._post_json("https://api.openai.com/v1/responses", {}, {}) is None
    finally:
        llm._REQUEST_DEADLINE.reset(token)


def test_llm_call_timeout_is_capped_by_the_request_budget(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("FOOD_CHATBOT_REQUEST_BUDGET_SECONDS", "25")
    with llm.llm_request_budget():
        assert 20 < llm._call_timeout() <= 25
    assert llm._call_timeout() == 120


def test_rate_limit_key_is_per_signed_owner_behind_the_service_token():
    scope = {"client": ("10.0.0.5", 443)}
    headers = {"x-food-chatbot-user-id": "42"}
    assert _client_key(scope, headers, service_authenticated=True) == "user:42"
    assert _client_key(scope, headers, service_authenticated=False) == "10.0.0.5"
    assert _client_key(scope, {"x-food-chatbot-user-id": "42; drop"}, service_authenticated=True) == "10.0.0.5"


def test_verified_request_user_id_requires_a_verified_assertion():
    verified = consent.ExternalAIConsentDecision(verified=True, consent_granted=False,
                                                 reason="consent_denied", user_id="42")
    token = consent.set_external_ai_request_context(verified)
    try:
        assert consent.verified_request_user_id() == 42
    finally:
        consent.reset_external_ai_request_context(token)
    unverified = consent.ExternalAIConsentDecision(user_id="42")
    token = consent.set_external_ai_request_context(unverified)
    try:
        assert consent.verified_request_user_id() is None
    finally:
        consent.reset_external_ai_request_context(token)
