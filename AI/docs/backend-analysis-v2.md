# 백엔드 ↔ AI `chat-analysis-v2` 연결

2026-11-02. AI 구현 기준은 첨부 ZIP의 `reference/chat-analysis-contract-v2.md`와 **KW-coworker/JJIKMUK-Backend의 `eae67ecfde34f3ba1a8296c7ae2dd7b74e94d38a`**이다. ZIP에 빠진 판정 상세 구조는 아래 원본 소스를 읽어 확인했다. 기존 작업 폴더의 `backend/`는 이 버전과 다르며 변경하지 않았다.

- [ChatService: 실제 AI 요청 구성](https://github.com/KW-coworker/JJIKMUK-Backend/blob/eae67ecfde34f3ba1a8296c7ae2dd7b74e94d38a/src/main/kotlin/org/jjikmuk/backend/domain/chat/ChatService.kt)
- [ProductService: evaluateProductConditions 반환 필드](https://github.com/KW-coworker/JJIKMUK-Backend/blob/eae67ecfde34f3ba1a8296c7ae2dd7b74e94d38a/src/main/kotlin/org/jjikmuk/backend/domain/product/ProductService.kt)
- [RecommendationDto: 알레르기·식단·자료 근거](https://github.com/KW-coworker/JJIKMUK-Backend/blob/eae67ecfde34f3ba1a8296c7ae2dd7b74e94d38a/src/main/kotlin/org/jjikmuk/backend/domain/recommendation/RecommendationDto.kt)
- [ChatProfileResolver: 개인 연결 키](https://github.com/KW-coworker/JJIKMUK-Backend/blob/eae67ecfde34f3ba1a8296c7ae2dd7b74e94d38a/src/main/kotlin/org/jjikmuk/backend/domain/chat/ChatProfileResolver.kt)

## 호출과 인증

앱은 기존대로 로그인 JWT를 사용해 백엔드 `POST /api/chat`을 호출한다. 아래는 **신뢰하는 백엔드 → AI**의 `POST /chat` 규격이며 앱이 판정 데이터를 직접 작성하는 규격이 아니다.

```http
POST /chat
Content-Type: application/json
X-Food-Chatbot-Token: <기존 내부 서비스 토큰>
X-Jjikmuk-Analysis-Contract: chat-analysis-v2
X-Jjikmuk-Analysis-Context: <본문 analysisContext.contextId>
```

AI의 `FOOD_CHATBOT_API_TOKEN`이 설정되어 있어야 v2를 받는다. 미설정은 503, 토큰 누락·오류는 401이다. 본문·버전·contextId가 맞지 않으면 422이며 성공 확인 헤더를 반환하지 않는다. 알 수 없는 판정 필드·상태, 중복/누락된 개인 키, 개인 선택과 판정의 불일치도 422다. 일반 기존 요청에는 v2 확인 헤더를 붙이지 않는다.

외부 AI 동의는 기존과 동일하다. 다음 헤더는 최종 JSON **바이트 전체**를 직렬화한 뒤 서명한다. 분석 규격 v2로 바뀌어도 **동의 서명의 canonical version은 v1**이다.

```text
X-Food-Chatbot-User-Id
X-Food-Chatbot-External-AI-Consent
X-Food-Chatbot-External-AI-Timestamp
X-Food-Chatbot-External-AI-Nonce
X-Food-Chatbot-External-AI-Signature
```

서명 입력 순서는 `v1`, timestamp, `POST`, `/chat`, **로그인 계정 ID**, `granted|denied`, nonce, 실제 본문의 SHA-256을 줄바꿈으로 연결한 값이다. 기존 HMAC-SHA256 검증, 만료·재사용 방지, 외부 AI 운영 스위치, 요청 크기·호출 제한을 유지한다. 가족 ID·`profile_id`·`profileKey`를 로그인 계정 ID 대신 서명에 쓰지 않는다. 동의 누락·무효·OFF에서는 외부 생성/검색 임베딩을 사용하지 않으며 규칙/백엔드 판정 설명은 가능하다.

**첨부 기준 백엔드의 `AiChatClient`에는 v2 확인 헤더만 있고 내부 토큰·동의 서명 연결이 없다.** 백엔드 담당자는 운영의 `ExternalAiConsentSigner`와 토큰 전달을 그 경로에도 연결해야 한다. AI에서 검사를 끄거나 동의를 true로 대체하지 않는다. 운영 enum `ANALYSIS_V1`이 이 v2 경로를 가리키므로 임의의 `ANALYSIS_V2` 설정값을 만들지 않는다.

## 입력

전체 실행 가능한 합성 예시는 아래 JSON에 있다. 실제 개인정보·비밀값·운영 상품은 포함하지 않는다.

| 경우 | 요청 예시 |
|---|---|
| 엄마 DANGER, 아빠 PASS | [multi.request.json](../tests/fixtures/backend_v2/multi.request.json) |
| 1명 | [single.request.json](../tests/fixtures/backend_v2/single.request.json) |
| 전원 OFF | [none.request.json](../tests/fixtures/backend_v2/none.request.json) |
| 상품 미선택 | [no_product.request.json](../tests/fixtures/backend_v2/no_product.request.json) |
| 본인·가족의 숫자 ID가 같음 | [server_collision.request.json](../tests/fixtures/backend_v2/server_collision.request.json) |

0명은 `profile=null, profiles=[]`, 1명은 `profile=개인, profiles=[]`, 2명 이상은 `profile=null, profiles=[개인들]`이다. 비활성 프로필은 백엔드에서 제외한 뒤 전송한다. 같은 이름을 합치거나 집단 조건을 가상의 사람으로 추가하지 않는다.

`profileKey`와 `profileAnalyses[].profileKey`의 집합이 정확히 같아야 한다. 배열 순서로 판정을 연결하지 않는다. `self:41`과 `family:41`은 서로 다른 사람이다. `request:0`은 현재 요청 안의 키이므로 다음 요청의 같은 키를 같은 사람으로 간주하지 않는다. `profileSource=CLIENT_REQUEST`는 요청자가 제시한 조건의 출처이며 식품 라벨의 검증 수준이 아니다.

`analysisContext.analysis`와 개인별 `analysis`는 `status`, `dietConstraints`, `analysisApplied`, `selection`, `isDangerous`, `dangerousIngredients`, `conflictingAllergenIds`, `evidenceSources`, `evidenceLevel`, `verificationRequired`, `message`를 받는다. 식단은 `requiredFilters/satisfiedFilters/failedFilters/uncertainFilters`, `attributes`, 출처·confidence·message를 별도로 받는다. 기존 저장 diet boolean이나 과거 채팅으로 이 상태를 변경하지 않는다. 자유문 `message`는 판정 상태를 바꾸는 명령으로 실행하지 않는다.

| 제한 | 기존 요청 | v2 요청 |
|---|---:|---:|
| 프로필 입력 합계 | 20 | 21 |
| message | 4,000자 | 4,000자 |
| 이력 항목 | 50 | 20 |
| 이력 각 content | 4,000자 | 10,000자 |
| 이력 합계 | 20,000자 | 40,000자 |

기존 본문 크기 1 MiB 및 프로필 조건 텍스트의 한도도 적용한다. v2 이력은 백엔드 규격에 맞춰 같은 역할이 연속될 수 있다. 서버 저장 대화 요청에서는 기존처럼 `conversation_id/turn_id`와 서명된 계정 증명이 필요하고, 클라이언트 이력·상태를 함께 보내지 않는다.

AI 공개 상품 계약의 크기 제한도 입력 단계에서 확인한다(예: 상품명 512자, 원재료 텍스트 8,192자). 제한을 넘는 라벨을 조용히 잘라 판정하지 않으며 422를 반환한다. 프로필 조건은 기존 항목당 256자·목록당 32개·개인 합계 4,096자·전체 합계 32,768자 제한을 유지하므로 백엔드 입력 검증에도 이 공통 한도를 적용해야 한다.

## 출력

실제 v2 처리가 끝난 200 응답에만 입력과 같은 두 확인 헤더를 반환한다. 기존 `answer`, `sources`, 상품/추천/비교 카드, `recipe`, `conversation_state`, 저장 대화 필드는 유지한다.

- `answer_source="backend"`: 해당 상품의 백엔드 판정을 설명한 답변. LLM 재판정으로 바꾸지 않는다.
- `per_profile_results[]`: 기존 개인별 필드에 `profileKey`, `profileSource`, `backend_status`, `backend_diet_status`를 추가한다. **v2 상품 판정은 1명이어도 결과 1개를 반환**한다. 이전 일반 요청의 단일 인원 배열 동작은 유지한다.
- `backend_analysis`: `contractVersion`, `contextId`, `policyVersion`, `decisionAuthority`, `decisionScope`, `profileSource`, `selectionMode`, `productResolution`, `barcode`, 집단 `status/dietStatus`, `application`을 제공한다. `effectiveConditions`의 질환/비선호 원문은 공개 응답에 추가하지 않는다.

실제 격리 HTTP 응답 예시: [2명](../tests/fixtures/backend_v2/multi.response.json), [1명](../tests/fixtures/backend_v2/single.response.json), [전원 OFF](../tests/fixtures/backend_v2/none.response.json). 임의의 모의 AI 응답이 아니라 새 Python 서버에서 얻은 응답이며, 상품과 프로필은 합성 데이터다.

`application`은 다음처럼 해석한다.

| 값 | 의미 |
|---|---|
| `product_decision` | 현재 바코드에 대해 개인별 백엔드 판정을 실제 답변·UI 결과에 적용 |
| `context_only` | RAG 일반 설명, 레시피, 추천, 비교, 응급/조건 확인 등. 전달된 바코드 판정은 다른 상품/생성 재료의 판정으로 복사하지 않음 |
| `product_selection_required` | AI 검색에서 새 상품을 찾았지만 그 상품의 백엔드 판정이 이번 요청에 없음. 해당 바코드를 선택해 재요청 필요 |

집단 `status`는 개인별 결과가 아니다. 엄마 DANGER·아빠 PASS이면 각각 `avoid`·`no_known_conflict`가 되고, 전체는 `avoid`다. 일반 RAG/레시피 결과에 첨부된 `backend_analysis.status`를 그 답변 전체의 안전 배지로 사용하지 않는다.

| 백엔드 상태 | 공개 의미 |
|---|---|
| 알레르기 DANGER | `allergy_status=conflict`, `overall_decision=avoid` |
| 알레르기 PASS | `allergy_status=no_known_match`. 식단 상태도 함께 확인; 섭취 안전 보증 아님 |
| 알레르기 UNKNOWN | `allergy_status=unknown`; 다른 확인된 충돌이 없으면 `check_required` |
| 식단 MISMATCH | `diet_match=not_match`. 알레르기 DANGER가 없으면 `caution` |
| 식단 UNKNOWN | `diet_match=unknown`. 다른 확인된 충돌이 없으면 `check_required` |
| 전원 OFF / NOT_EVALUATED | 개인 배열 비움, `assessment_summary.decision=not_assessed`; UNKNOWN과 구분 |
| 상품 미선택 | 백엔드 상태 null. 새 개인 안전 판정 없음 |

알레르기 PASS와 식단 PASS를 모두 받았을 때만 해당 범위의 `no_known_conflict`를 제공한다. 프로필 조건 변경은 다음 요청의 새 판정을 사용한다. 기존 저장 답변은 당시 사람·결과·contextId를 보존한다.

## 기존 기능과 연동 범위

일반 RAG의 검색 질문 정리·후속 문맥·생성 답변 근거 검사·검토 원문 대체, 상품 검색/후보 선택, 대체 추천, 다중 상품 비교, 레시피/재료·순서 재안내, 응급 안내, 대화 저장/조회/삭제 코드는 유지한다. 다만 다음은 v2의 판정 범위에 따른 연결 조건이다.

1. **새 바코드:** AI가 대화에서 다른 상품을 찾았으면, 그 바코드를 백엔드에 다시 보내 새 판정을 받아야 한다. 기존 바코드의 PASS를 새 상품에 붙이지 않는다.
2. **새 개인 조건:** 채팅에서 추가 알레르기/채식 허용 조건을 말했으면, 기존 판정에 반영된 것으로 안내하지 않는다. 조건 확인 또는 프로필 반영 후 재요청을 안내한다. 기존 요청 방식의 임시 조건 처리는 유지한다.
3. **추천·비교·레시피:** 기존 AI 저장소와 검증 기능을 사용한다. 하나의 BARCODE_ROW 판정은 대안 전체·다른 비교 상품·생성 재료를 인증하지 않는다. 이 기능 전체를 백엔드 공통 판정으로 전환하려면 후보별 판정 계약이 별도로 필요하다.
4. **영양 기준량:** 첨부 ProductDto는 수치는 보내지만 100g/1회분 등의 명시 기준량 계약을 보내지 않는다. v2의 숫자를 임의로 한 봉지 수치로 환산하지 않는다. 기존 저장소 기반의 기준량 기능은 유지한다.
5. **저장 대화 재시도:** 같은 turn_id의 재시도는 같은 contextId와 요청을 사용해야 한다. 새 판정을 받은 다른 요청에 기존 turn_id를 재사용하면 기존처럼 409다.

## 배포 및 검증

- 변경 범위: AI 폴더. 새 필수 환경변수·라이브러리·DB migration 없음. 기존 서비스 토큰/동의/저장 대화 설정을 사용한다.
- 기존 방식과 v2를 함께 지원한다. 기존 요청 한도를 v2 한도 때문에 일괄 완화하지 않는다.
- ZIP의 오래된 `main.py`/`hybrid.py`로 파일 전체를 덮어쓰지 않는다. 기존 로컬에는 없는 `team_catalog.py`의 결과 제한을 새로 추가하지 않았다.
- 서버 담당자가 관리 중인 호출 누계/nonce 상태 DB나 운영 비밀값을 초기화하지 않는다. 해당 운영 코드의 병합·유지는 배포 담당자가 현재 운영 버전을 기준으로 확인해야 한다.
- AI 내부 토큰·본문 서명 테스트를 통과해도 로그인 백엔드의 v2 서명 전송 및 실제 앱 표시까지 검증한 것은 아니다. 이 두 구간은 담당자와 실제 연결 검사를 해야 한다.

```bash
python -m pytest tests/test_backend_analysis_v2.py -q
python -m pytest -q
python evaluation/run_backend_v2_acceptance.py
# 합성 질문 2건의 실제 검색/생성 호출까지 허용할 때만:
python evaluation/run_backend_v2_acceptance.py --live-rag
```

결과 및 실제 확인 범위는 [검증 기록](../evaluation/2026-11-02-backend-v2-verification.md)을 참고한다.
