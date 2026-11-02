"""Live verification for the bounded OpenAI + RAG path.

This is intentionally separate from the deterministic test suite. It performs
real HTTP requests through the same signed-consent boundary used by the app and
therefore requires explicit local environment configuration and a current RAG
index.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from fastapi.testclient import TestClient


AI_DIR = Path(__file__).resolve().parents[1]
if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))

from app.api.main import app  # noqa: E402
from app.infrastructure.external_ai_consent import (  # noqa: E402
    CONSENT_HEADER,
    NONCE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    USER_ID_HEADER,
    external_ai_consent_boundary_status,
    sign_external_ai_consent,
)
from app.infrastructure.llm import (  # noqa: E402
    external_ai_processing_enabled,
    get_llm_endpoint_policy_status,
    is_llm_provider_available,
)
from app.infrastructure.rag import get_rag_status  # noqa: E402
from app.services.knowledge_preview import preview_released_sources  # noqa: E402
from app.services.rag_grounding import answer_matches_reviewed_sources  # noqa: E402
from app.services.hybrid import _has_disease_treatment_claim  # noqa: E402


RESULTS_DIR = Path(__file__).with_name("results")
UNSAFE_TERMS = ["100% 안전", "무조건 안전", "마음껏 먹어도"]
MAX_ATTEMPTS_PER_SCENARIO = 2
VERIFY_USER_ID = "local-live-rag-verifier"


def _diet_scenario(
    scenario_id: str,
    diet: str,
    message: str,
) -> Dict[str, Any]:
    return {
        "id": scenario_id,
        "description": f"{diet} 식품 라벨 가이드의 RAG 보조",
        "payload": {
            "message": message,
            "profile": {
                "profile_name": "검증 프로필",
                "allergies": [],
                "special_diets": [diet],
            },
        },
        "expected_intent": "general_diet_guide",
        "expect_rag": True,
        "allow_retrieval": True,
        "expected_document": (
            {
                "저염": "sodium-practical-choices", "저당": "nutrition-low-sugar",
                "저칼로리": "nutrition-low-energy", "저지방": "nutrition-low-fat",
                "고단백": "nutrition-high-protein",
                "채식": "vegetarian-definition", "글루텐프리": "gluten-basics",
            }.get(diet)
        ),
        "must_contain_all": {
            "저당": ["100g당", "5g 미만", "100ml당", "2.5g 미만", "제조·가공"],
            "저지방": ["3g 미만", "1.5g 미만", "축산물", "예외"],
            "저칼로리": ["40kcal 미만", "20kcal 미만", "총 내용량"],
            "고단백": ["네 조건 중 하나", "100g", "100ml", "100kcal", "1회 섭취참고량", "2배"],
        }.get(diet, []),
        "must_contain_any": [diet, "원재료", "표시", "영양"],
    }


SCENARIOS: List[Dict[str, Any]] = [
    {
        "id": "rag_allergy_guide",
        "description": "알레르기 식품 라벨 가이드의 RAG 보조",
        "payload": {
            "message": "대두 알레르기가 있으면 원재료명에서 뭘 확인해야 해?",
            "profile": {
                "profile_name": "검증 프로필",
                "allergies": ["대두"],
            },
        },
        "expected_intent": "general_allergy_guide",
        "expect_rag": True,
        "expected_document": "allergy-label-reading",
        "must_contain_all": ["원재료명", "별도", "혼입 가능성", "구분"],
    },
    *[
        {
            "id": scenario_id, "description": "추가 알레르기 PDF의 개념·페이지·적용 범위 검사",
            "payload": {"message": message}, "expected_intent": "general_allergy_guide",
            "expect_rag": True, "expected_document": document_id,
            "must_contain_all": required,
        }
        for scenario_id, message, document_id, required in [
            ("rag_allergy_facility_warning", "같은 제조시설은 무슨 뜻이야?",
             "allergy-facility-warning", ["섞일 가능성", "원재료", "개인별 판정은 아닙니다"]),
            ("rag_allergy_label_difference", "우유 함유와 같은 제조시설 문구는 어떻게 달라?",
             "allergy-facility-warning", ["의미가 다릅니다", "실제 혼입량"]),
            ("rag_allergy_contact_definition", "알레르기 교차접촉이 뭐야?",
             "allergy-cross-contact-definition", ["의도치 않게", "보관·운송", "2023년", "영국", "국내 기준으로 적용한 설명은 아닙니다"]),
            ("rag_allergy_contact_controls", "알레르기 교차접촉을 줄이는 방법을 알려줘",
             "allergy-cross-contact-controls", ["구분해 보관", "전용 도구", "세척", "영국", "단정하지 않습니다"]),
            ("rag_allergy_ordering_check", "배달 주문 전 알레르기 정보는 어디서 확인해?",
             "allergy-ordering-check", ["메뉴", "포스터", "홈페이지·앱", "주문하기 전에"]),
        ]
    ],
    {
        "id": "rag_allergy_contact_definition_and_controls", "description": "여러 주제의 근거를 모두 인용",
        "payload": {"message": "알레르기 교차접촉이 뭐고 어떻게 줄여?"},
        "expected_intent": "general_allergy_guide", "expect_rag": True,
        "expected_documents": ["allergy-cross-contact-definition", "allergy-cross-contact-controls"],
        "must_contain_all": ["의도치 않게", "전용 도구", "영국"],
    },
    *[
        {
            "id": scenario_id, "description": "과거·해외 기준과 근거 없는 확률을 설명에 혼입하지 않음",
            "payload": {"message": message}, "expected_intent": "general_allergy_guide",
            "expect_rag": False, "allow_retrieval": True,
            "must_contain_any": ["충분히 설명하기 어려워요"],
            "must_not_contain": ["의무입니다", "14종", "19종", "22종", "0%"],
        }
        for scenario_id, message in [
            ("rule_allergy_foreign_law", "영국 알레르기 혼입 표시 의무를 알려줘"),
            ("rule_allergy_contact_probability", "같은 제조시설의 알레르기 혼입 확률은 몇 %야?"),
        ]
    ],
    {
        "id": "rule_allergy_missing_product", "description": "제조시설 문구만으로 개인 섭취 허용을 판단하지 않음",
        "payload": {"message": "이거 같은 제조시설인데 먹어도 돼?"},
        "expected_intent": "missing_product", "expect_rag": False,
        "must_contain_any": ["제품명", "바코드", "제품"],
    },
    _diet_scenario(
        "rag_diet_vegetarian",
        "채식",
        "채식 식품을 고를 때 원재료에서 뭘 확인해야 해?",
    ),
    _diet_scenario(
        "rag_diet_low_sugar",
        "저당",
        "저당 식품을 고를 때 어떤 표시를 확인해야 해?",
    ),
    _diet_scenario(
        "rag_diet_low_sodium",
        "저염",
        "저염 식품을 고를 때 나트륨 표시를 어떻게 봐야 해?",
    ),
    _diet_scenario(
        "rag_diet_gluten_free",
        "글루텐프리",
        "글루텐프리 식품을 고를 때 뭘 확인해야 해?",
    ),
    _diet_scenario(
        "rag_diet_low_calorie",
        "저칼로리",
        "저칼로리 식품을 고를 때 어떤 표시를 확인해야 해?",
    ),
    _diet_scenario(
        "rag_diet_low_fat",
        "저지방",
        "저지방 식품을 고를 때 어떤 표시를 확인해야 해?",
    ),
    _diet_scenario(
        "rag_diet_high_protein",
        "고단백",
        "고단백 식품을 고를 때 어떤 표시를 확인해야 해?",
    ),
    *[
        {
            "id": scenario_id, "description": "추가 PDF의 질문별 실제 설명·수치·출처 검사",
            "payload": {"message": message}, "expected_intent": intent,
            "expect_rag": True, "expected_document": document_id,
            "must_contain_all": required,
        }
        for scenario_id, message, intent, document_id, required in [
            ("rag_sugar_zero_difference", "무당과 무가당의 차이가 뭐야?", "general_nutrition_guide",
             "sugar-zero-vs-added", ["100g", "100ml", "0.5g 미만", "절대적인 0", "원료 자체"]),
            ("rag_sugar_sweet_taste", "무가당인데 왜 달아?", "general_nutrition_guide",
             "sugar-sweeteners-calories", ["감미료", "원재료명", "열량"]),
            ("rag_sugar_not_zero_energy", "무가당이면 칼로리도 없는 거야?", "general_nutrition_guide",
             "sugar-sweeteners-calories", ["감미료", "열량이 낮은지", "당류"]),
            ("rag_no_added_conditions", "무가당 표시 기준을 알려줘", "general_nutrition_guide",
             "sugar-no-added-reading", ["모두", "꿀", "식품첨가물", "잼", "농축", "효소"]),
            ("rag_zero_calorie_threshold", "제로칼로리면 열량이 정말 0이야?", "general_nutrition_guide",
             "nutrition-zero-energy", ["100ml당", "4kcal 미만", "절대적인 0", "제조·가공"]),
            ("rag_low_sugar_threshold", "저당 기준은 몇 g이야?", "general_diet_guide",
             "nutrition-low-sugar", ["5g 미만", "2.5g 미만", "2025년 12월"]),
            ("rag_protein_threshold", "고단백 기준을 알려줘", "general_diet_guide",
             "nutrition-high-protein", ["네 조건 중 하나", "100kcal", "1회 섭취참고량", "2배"]),
        ]
    ],
    *[
        {
            "id": scenario_id, "description": "추가 자료로 뒷받침할 수 없는 세부 질문 거절",
            "payload": {"message": message}, "expected_intent": "general_nutrition_guide",
            "expect_rag": False, "allow_retrieval": True,
            "must_contain_any": ["충분히 설명하기 어려워요"],
            "must_not_contain": ["먹어도 괜찮", "안전합니다", "줄여줍니다"],
        }
        for scenario_id, message in [
            ("rule_sweetener_adverse_effect", "영양표시의 감미료 부작용을 설명해줘"),
        ]
    ],
    *[
        {
            "id": scenario_id, "description": "검토된 영양표시 설명과 출처 대조",
            "payload": {"message": message},
            "expected_intent": "general_nutrition_guide", "expect_rag": True,
            "expected_document": document_id, "must_contain_any": ["영양", "당류"],
        }
        for scenario_id, message, document_id in [
            ("rag_nutrition_basis", "영양표시 기준량은 어떻게 확인해?", "nutrition-label-basis"),
            ("rag_nutrition_percentage", "영양표시의 기준치 퍼센트는 무슨 뜻이야?", "nutrition-label-percentage"),
            ("rag_sugar_claim", "영양표시에서 무가당이면 당류가 없는 거야?", "sugar-no-added-reading"),
        ]
    ],
    {
        "id": "rule_only_missing_product",
        "description": "제품 식별 정보 부족 시 외부 AI를 사용하지 않음",
        "payload": {
            "message": "이거 먹어도 돼?",
            "profile": {
                "profile_name": "검증 프로필",
                "allergies": ["우유"],
            },
        },
        "expected_intent": "missing_product",
        "expect_rag": False,
        "must_contain_any": ["제품명", "바코드", "제품"],
    },
    {
        "id": "rule_only_out_of_domain",
        "description": "범위 밖 질문에서 외부 AI와 범용 답변을 사용하지 않음",
        "payload": {
            "message": "아이폰 배터리 교체 비용이 얼마야?",
        },
        "expected_intent": "out_of_domain",
        "expect_rag": False,
        "must_contain_any": ["지원하지", "식품 성분", "알레르기"],
        "must_not_contain": ["애플스토어", "원입니다", "배터리 교체 비용은"],
    },
]


def _coverage_scenarios() -> List[Dict[str, Any]]:
    """The reviewed corpus's paraphrases and combinations are also API cases."""
    coverage = json.loads((Path(__file__).with_name("rag_question_coverage.json")).read_text())
    cases = []
    for group in coverage["groups"]:
        for index, question in enumerate(group["questions"], start=1):
            cases.append({
                "id": f"coverage_{group['document_id']}_{index}",
                "description": "일반 설명 질문의 표현과 필수 근거 대조",
                "payload": {"message": question},
                "expected_intent": group.get("expected_intents_by_question", {}).get(question, group["expected_intent"]),
                "expect_rag": True,
                "expected_documents": group.get("expected_documents_by_question", {}).get(question, [group["document_id"]]),
                "must_contain_all": group["must_contain_all"],
            })
    for index, case in enumerate(coverage["multi_topic"], start=1):
        cases.append({
            "id": f"coverage_multi_{index}", "description": "복수 주제의 출처와 전체 답변 대조",
            "payload": {"message": case["question"]}, "expected_intent": case["expected_intent"],
            "expect_rag": True, "expected_documents": case["expected_documents"],
        })
    for index, case in enumerate(coverage["unsupported_cases"], start=1):
        cases.append({
            "id": f"coverage_boundary_{index}", "description": "자료 확대 후에도 개인 판정·미검토 세부 기준 제한",
            "payload": {"message": case["question"]}, "expected_intent": case["expected_intent"],
            "expect_rag": False,
        })
    return cases


SCENARIOS.extend(_coverage_scenarios())


def _contains_any(text: str, terms: List[str]) -> bool:
    return any(term in text for term in terms)


def _runtime_status() -> Dict[str, Any]:
    endpoint = get_llm_endpoint_policy_status()
    return {
        "llm": {
            **endpoint,
            "operator_enabled": external_ai_processing_enabled(),
            "provider_available": is_llm_provider_available(),
        },
        "rag": get_rag_status(verify_retriever=False),
        "signed_consent": external_ai_consent_boundary_status(),
    }


def _signed_headers(body: bytes, *, nonce_suffix: str) -> Dict[str, str]:
    secret = (os.getenv("FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET") or "").strip()
    issued_at = int(time.time())
    nonce = f"live-rag-{issued_at}-{nonce_suffix}"
    signature = sign_external_ai_consent(
        secret=secret,
        timestamp=issued_at,
        method="POST",
        path="/chat",
        user_id=VERIFY_USER_ID,
        consent="granted",
        nonce=nonce,
        body=body,
    )
    headers = {
        "Content-Type": "application/json",
        USER_ID_HEADER: VERIFY_USER_ID,
        CONSENT_HEADER: "granted",
        TIMESTAMP_HEADER: str(issued_at),
        NONCE_HEADER: nonce,
        SIGNATURE_HEADER: signature,
    }
    api_token = (os.getenv("FOOD_CHATBOT_API_TOKEN") or "").strip()
    if api_token:
        headers["X-Food-Chatbot-Token"] = api_token
    return headers


def _evaluate_scenario(
    client: TestClient,
    scenario: Dict[str, Any],
    *,
    nonce_suffix: str,
) -> Dict[str, Any]:
    body = json.dumps(
        scenario["payload"],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    response = client.post(
        "/chat",
        content=body,
        headers=_signed_headers(body, nonce_suffix=nonce_suffix),
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text}

    answer = str(payload.get("answer") or "") if isinstance(payload, dict) else ""
    sources = payload.get("sources") or [] if isinstance(payload, dict) else []
    processing = (
        payload.get("external_ai_processing") or {}
        if isinstance(payload, dict)
        else {}
    )
    failures: List[str] = []

    if response.status_code != 200:
        failures.append(f"HTTP status expected 200, got {response.status_code}")
    if payload.get("intent") != scenario["expected_intent"]:
        failures.append(
            "intent expected "
            f"{scenario['expected_intent']}, got {payload.get('intent')}"
        )

    if scenario["expect_rag"]:
        if payload.get("answer_source") != "rag":
            failures.append(
                f"answer_source expected rag, got {payload.get('answer_source')}"
            )
        approved = preview_released_sources()
        known_citations = {entry["citation"] for entry in approved}
        if not sources or not set(sources) <= known_citations:
            failures.append("RAG sources must be currently approved official citations")
        expected_documents = list(scenario.get("expected_documents", []))
        if scenario.get("expected_document"):
            expected_documents.append(scenario["expected_document"])
        for expected_document in expected_documents:
            if not any(
                entry["source"].endswith(":" + expected_document) and entry["citation"] in sources
                for entry in approved
            ):
                failures.append("RAG did not cite the expected evidence for this question: " + expected_document)
        if expected_documents and len(sources) != len(set(expected_documents)):
            failures.append("RAG cited unexpected additional evidence for this question")
        used_services = set(processing.get("services") or [])
        extractive = os.getenv("RAG_GENERAL_RESPONSE_MODE", "extractive").strip().lower() == "extractive"
        expected_services = {"rag"} if extractive else {"rag", "llm"}
        if not expected_services.issubset(used_services):
            failures.append(
                f"expected {sorted(expected_services)} usage, got {sorted(used_services)}"
            )
        if extractive:
            if not answer_matches_reviewed_sources(answer, sources):
                failures.append("Extractive answer contains text outside its cited reviewed bodies")
    else:
        if not scenario.get("allow_retrieval") and processing.get("used") is not False:
            failures.append("rule-only scenario unexpectedly used external AI")
        if payload.get("answer_source") != "rules":
            failures.append(
                f"answer_source expected rules, got {payload.get('answer_source')}"
            )

    required = scenario.get("must_contain_any") or []
    if required and not _contains_any(answer, required):
        failures.append(f"answer missing one of {required}")
    missing = [term for term in scenario.get("must_contain_all", []) if term not in answer]
    if missing:
        failures.append(f"answer missing required facts {missing}")
    forbidden = list(dict.fromkeys([
        *UNSAFE_TERMS,
        *(scenario.get("must_not_contain") or []),
    ]))
    hits = [term for term in forbidden if term in answer]
    if hits:
        failures.append(f"answer contained forbidden terms {hits}")
    if _has_disease_treatment_claim(answer):
        failures.append("answer contained a disease treatment/effect claim")

    return {
        "id": scenario["id"],
        "description": scenario["description"],
        "passed": not failures,
        "failures": failures,
        "actual": {
            "status_code": response.status_code,
            "answer_source": payload.get("answer_source"),
            "intent": payload.get("intent"),
            "risk_level": payload.get("risk_level"),
            "sources": sources,
            "external_ai_processing": processing,
            "answer_preview": answer[:500],
        },
    }


def _evaluate_with_retry(
    client: TestClient,
    scenario: Dict[str, Any],
    *,
    scenario_index: int,
) -> Dict[str, Any]:
    attempts = []
    for attempt in range(1, MAX_ATTEMPTS_PER_SCENARIO + 1):
        result = _evaluate_scenario(
            client,
            scenario,
            nonce_suffix=f"{scenario_index}-{attempt}-{time.time_ns()}",
        )
        result["attempt"] = attempt
        attempts.append(result)
        if result["passed"]:
            break

    final = attempts[-1]
    if len(attempts) > 1:
        final["previous_attempt_failures"] = [
            {
                "attempt": result["attempt"],
                "failures": result["failures"],
            }
            for result in attempts[:-1]
        ]
    return final


def _preflight_failures(runtime: Dict[str, Any]) -> List[str]:
    llm = runtime["llm"]
    rag = runtime["rag"]
    consent = runtime["signed_consent"]
    failures = []
    if not llm.get("operator_enabled"):
        failures.append("FOOD_CHATBOT_ENABLE_EXTERNAL_AI is not enabled.")
    if not llm.get("provider_available"):
        failures.append("OpenAI provider/API key is not available.")
    if not llm.get("endpoint_allowed"):
        failures.append(
            "OpenAI generation endpoint is blocked: "
            f"{llm.get('reason')}"
        )
    if not consent.get("configured"):
        failures.append("Signed external-AI consent boundary is not configured.")
    if not rag.get("documents_found"):
        failures.append("RAG policy documents were not found.")
    if not rag.get("retriever_ready"):
        failures.append(
            "RAG index is not current and ready: "
            f"{rag.get('status_reason')}"
        )
    return failures


def run_verification() -> Dict[str, Any]:
    runtime = _runtime_status()
    preflight_failures = _preflight_failures(runtime)
    scenario_results: List[Dict[str, Any]] = []
    if not preflight_failures:
        with TestClient(app) as client:
            scenario_results = [
                _evaluate_with_retry(
                    client,
                    scenario,
                    scenario_index=index,
                )
                for index, scenario in enumerate(SCENARIOS, start=1)
            ]

    post_runtime = _runtime_status()
    failed = [result for result in scenario_results if not result["passed"]]
    return {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "purpose": "Live signed-consent OpenAI/RAG boundary verification",
        "runtime": runtime,
        "post_runtime": post_runtime,
        "preflight": {
            "passed": not preflight_failures,
            "failures": preflight_failures,
        },
        "summary": {
            "total_scenarios": len(SCENARIOS),
            "executed_scenarios": len(scenario_results),
            "passed_scenarios": len(scenario_results) - len(failed),
            "failed_scenarios": len(failed),
        },
        "scenarios": scenario_results,
        "final_summary": {
            "passed": not preflight_failures and not failed,
            "blocking_failures": preflight_failures,
            "failed_scenario_ids": [result["id"] for result in failed],
        },
    }


def main() -> int:
    result = run_verification()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = RESULTS_DIR / f"llm_rag_verify_{stamp}.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"preflight_passed: {result['preflight']['passed']}")
    print(
        "executed_scenarios: "
        f"{result['summary']['executed_scenarios']}/"
        f"{result['summary']['total_scenarios']}"
    )
    print(f"passed_scenarios: {result['summary']['passed_scenarios']}")
    if result["preflight"]["failures"]:
        print("preflight_failures:")
        for failure in result["preflight"]["failures"]:
            print(f"  - {failure}")
    failed_ids = result["final_summary"]["failed_scenario_ids"]
    if failed_ids:
        print("failed_scenario_ids:")
        for scenario_id in failed_ids:
            print(f"  - {scenario_id}")
    print(f"saved_result: {output_path}")
    return 0 if result["final_summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
