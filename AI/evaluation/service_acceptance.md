# 식품 챗봇 승인 시나리오와 검증 근거

2026-09-25 기준. 최초 명세의 17개 시나리오를 현재 코드의 테스트에 연결한다.
아래 근거는 로컬 테스트용 제품 저장소·출처 메타데이터, 메모리·SQLite 대화 저장소 및 모의 LLM 기준이다.
운영 제품 후보 확보, 실제 MySQL·외부 LLM·RAG 검색·배포 환경의 통합 검증 완료를 뜻하지 않는다.

## 최초 명세의 17개 시나리오

파일은 `AI/tests/` 기준이다. 함수명은 pytest에서 직접 선택해 실행할 수 있다.

| 번호 | 시나리오 | 직접 연결된 테스트 |
| --- | --- | --- |
| 1 | 새우 알레르기와 새우깡 → 피하기 | `test_chatbot.py::test_direct_allergen_conflict_produces_avoid_decision` |
| 2 | 충돌 프로필·성분 명시 | `test_food_chat_clarification_boundaries.py::test_allergy_owner_question_retains_registered_conflict`, `test_food_chat_core_scope.py::test_multi_profile_results_keep_each_persons_allergy_outcome_separate` |
| 3 | 충돌하지 않는 대체 과자 자동 표시 | `test_recommendation_policy_regressions.py::test_conflicting_product_chat_automatically_adds_database_alternative` |
| 4 | 후보 선택 후 원래 질문 처리 | `test_food_chat_core_scope.py::test_product_candidate_selection_resumes_the_original_question` |
| 5 | 새로 말한 알레르기를 현재 답변에 적용 | `test_food_chat_core_scope.py::test_message_allergy_is_applied_for_this_turn_without_persisting_it` |
| 6 | 한 ON 프로필만 충돌해도 전체 피하기 | `test_food_chat_completion_regressions.py::test_known_family_conflict_survives_another_missing_profile` |
| 7 | 프로필별 결과 표시 | `test_food_chat_core_scope.py::test_multi_profile_results_keep_each_persons_allergy_outcome_separate`, `test_food_chat_acceptance_evidence.py::test_recipe_family_results_are_separate_for_a_selected_product` |
| 8 | 저염 질문에 기준과 가능한 제품 후보 제공 | `test_food_chat_core_scope.py::test_productless_low_sodium_request_returns_verified_catalog_candidates`, `test_recommendation_policy_regressions.py::test_diet_recommendation_requires_explicit_product_claim` |
| 9 | 출발 제품 없는 알레르기 맞춤 추천 | `test_food_chat_acceptance_evidence.py::test_productless_allergy_recommendation_returns_only_matching_catalog_candidates` |
| 10 | 충돌 제품 사용 레시피 차단 | `test_food_chat_core_scope.py::test_conflicting_product_recipe_waits_when_only_untraced_alternatives_exist` |
| 11 | 대체 제품·대체 레시피 자동 제안 | `test_recommendation_policy_regressions.py::test_conflicting_product_recipe_uses_verified_substitute_and_keeps_warning` |
| 12 | 제품 없는 식이 맞춤 레시피 | `test_food_chat_core_scope.py::test_productless_diet_recipe_does_not_select_a_similarly_named_product`, `test_productless_recipe_exposes_each_active_profiles_result` |
| 13 | 다른 레시피 요청에 중복 방지 | `test_food_chat_core_scope.py::test_recipe_followups_keep_diet_and_do_not_repeat_after_four_variants` |
| 14 | 정보 부족 시 안전 단정 금지 | `test_food_chat_completion_regressions.py::test_missing_source_label_blocks_recipe_without_claiming_a_completed_check`, `test_food_chat_revalidation.py::test_missing_allergy_profile_never_claims_a_completed_check` |
| 15 | 대화 재조회·제목 변경·삭제 | `test_conversation_core.py::test_rooms_keep_full_transcripts_until_owner_deletes_them`, `test_sqlite_transcript_survives_repository_reopen`, `test_public_room_api_can_create_rename_list_and_delete` |
| 16 | 다른 사용자의 대화 조회 차단 | `test_conversation_core.py::test_public_room_api_can_create_rename_list_and_delete`의 다른 사용자 GET → 404 검사 |
| 17 | 현재 중증 알레르기 표현에 긴급 안내 우선 | `test_food_chat_scope_boundary.py::test_current_severe_allergic_reaction_keeps_minimal_emergency_guidance` |

3·9·11번은 검증 가능한 후보가 있다는 조건에서의 동작이다. 라벨 출처·원재료·프로필 정보가 부족하면 후보를 만들거나 억지로 채우지 않는다. 12번은 요청한 식이 목표와 검증된 적합성을 구분한다. 13번은 검토된 후보를 소진하면 새 레시피가 없음을 알리는 방식이며 무한 생성 보장이 아니다.

## 후속 보고에서 완료 근거가 필요했던 항목

- `missing_information`: `response_assessment.py`가 기존 누락 정보와 제품·프로필 누락 정보를 합친다. `test_food_chat_acceptance_evidence.py::test_missing_information_agrees_with_summary_across_scopes`는 섭취 판단·영양·성분·제품 있음/없음 레시피를 검사한다. 비교는 `test_food_chat_completion_regressions.py::test_comparison_confirms_a_variant_then_keeps_both_products_for_the_axis`, 확인 질문은 새 `test_food_chat_clarification_boundaries.py`에서 확인한다. 모든 임의 입력의 동등성을 증명한 것은 아니다.
- LLM 레시피 검증: `recipe_validation.py`의 검토된 1인분 재료·사용량·문법 검사와 `recipe_composition.py`의 재료·조리 일관성 검사를 사용한다. `test_food_chat_core_scope.py`의 `test_recipe_discards_llm_draft_that_does_not_use_selected_product`, `test_recipe_rejects_llm_draft_with_zero_selected_product_amount`, `test_recipe_checks_diet_conflicts_in_llm_substitutions_and_steps`, `test_productless_recipe_rejects_unlisted_packaged_food_in_steps`가 제품 사용·양·대체·추가 포장식품을 직접 검사한다. 비정상 사용량·인분·안심/영양 표현은 `test_food_chat_revalidation.py::test_unverifiable_recipe_prose_and_quantities_fall_back`에서 검사한다. 실제 모델 품질이나 모든 조리법의 안전성 검증과는 다르다.
- 레시피 프로필별 결과: 제품 없는 경우는 `test_food_chat_core_scope.py::test_productless_recipe_exposes_each_active_profiles_result`, 제품 있는 경우는 `test_food_chat_acceptance_evidence.py::test_recipe_family_results_are_separate_for_a_selected_product`에 연결된다.
- 신규 파일 추적·커밋: 현재 작업 트리에 신규 파일이 있으며 커밋·푸시는 수행하지 않았다. 로컬 테스트 통과와 GitHub 반영은 별개다. 배포/커밋 담당자는 위 신규 테스트·서비스 파일을 포함해 `AI/` 변경 목록을 확인해야 한다.

## 이전 4개 동작 수정의 경계

`test_food_chat_clarification_boundaries.py`는 확실/불확실 알레르기 분리, 조건 확인 중 기존 충돌 유지, 미확인 식이와 사실 질문 분리, 프로필 제한과 대화 발언 분리를 검사한다. 저장형은 메모리·SQLite 및 SQLite 재개방을 사용하며, 무상태 응답 왕복도 포함한다.

과거 버전이 이미 저장한 대화 `false`에는 그 값의 발언 출처가 없다. 이를 일괄 삭제하면 진짜 제외 요청도 해제하므로 자동 보정하지 않는다. 현재 프로필의 제한을 지키면서 명확한 사용자 재진술로 갱신한다.

## 후속 재검증의 6개 수정과 근거

`test_food_chat_pending_conditions.py`에 다음 재현·경계 검사를 추가했다. 기존 977개 테스트의 통과 여부와 별도로 이 사례들을 확인한다.

| 수정 | 직접 연결된 테스트 |
| --- | --- |
| 요리 표현에 따른 알레르기 확인 우회 | `test_uncertainty_guard_is_not_a_separate_request_keyword_list`, `test_request_paraphrase_guard_runs_before_search_or_external_ai` |
| 알레르기 대상 확인 답변 연결 | `test_owner_answer_resumes_original_request_and_is_consumed_once`, `test_stateless_owner_and_certainty_answers_round_trip`, `test_owner_clarification_then_product_selection_keeps_temporary_allergy` |
| 존댓말에서도 기존 제품·충돌 유지 | `test_polite_same_product_question_keeps_registered_conflict` |
| 진술 뒤 질문의 물음표 때문에 제외 조건 누락 | `test_declarative_exclusion_survives_question_ending`, `test_a_real_allowance_question_is_not_a_declaration` |
| 알레르기 확인 응답 전 명확한 제외 조건 보존 | `test_allergy_clarification_preserves_independent_dairy_exclusion`, `test_direct_service_collects_exclusion_before_allergy_guard` |
| 채식 설정 없는 프로필의 재료 제외 적용 | `test_nonvegetarian_exclusions_share_recipe_and_product_evaluator`, `test_excluded_dairy_is_filtered_from_nonvegetarian_alternative_candidates`, `test_nonvegetarian_exclusion_still_rejects_llm_recipe_substitution` |

저장형 사례는 메모리·SQLite로 실행하며 SQLite 저장소 재개방 뒤의 후속 요청과 조회 응답을 확인한다. OFF/불분명한 대상을 임의 지정하지 않는 경계, 명시적 철회, 독립 질문에서 임시 알레르기를 재사용하지 않는 경계도 검사한다. 알레르기 확인은 단순한 표시 정보 요청과 구분하며, 새 제품 식별 실패 시 과거 제품으로 돌아가지 않는 기존 회귀 검사를 유지한다.

2026-09-23 수정 후 로컬 검증: 전체 **1,038개 통과**(기존 977개 + 신규 61개, 31.85초). 컴파일 검사와 `git diff --check`도 통과했다. 실제 외부 연동 검증 결과는 아래 미완료 목록과 구분한다.

## 2026-09-25 재검증의 6개 동작·2개 사용성 수정

`test_food_chat_review_followups.py`에 첨부 재현 사례와 이어지는 경계 검사를 추가했다.

| 수정 | 직접 연결된 테스트 |
| --- | --- |
| 등록 항목과 별개로 미지원 신규 알레르기 제한 | `test_unsupported_new_allergy_is_not_hidden_by_registered_allergy`, `test_unknown_conditions_remain_separate_and_bounded_in_public_state` |
| 일반적 사람 표현과 명확한 개인 진술 분리 | `test_general_clause_does_not_erase_personal_assertion`, `test_general_information_and_denial_do_not_add_an_allergy` |
| 식이 확인 중 임시 알레르기 유지 | `test_diet_clarification_preserves_allergy_until_original_request_completes`, `test_stateless_diet_reply_keeps_temporary_allergy`, `test_successive_diet_answers_preserve_allergy_until_both_are_resolved` |
| 유제품·달걀 성분 별칭을 제외 검사와 공유 | `test_dairy_aliases_block_product_and_candidates_without_claiming_an_allergy`, `test_saved_exclusion_uses_label_aliases_after_store_reopening`, `test_recipe_steps_and_substitutions_use_the_same_exclusion_dictionary` |
| 요청형 질문의 제외 조건 반영 | `test_polite_exclusion_question_blocks_source_recipe`, `test_permission_question_still_does_not_assert_an_exclusion` |
| 같은 제품의 표현 변경에도 기존 경고 유지 | `test_deictic_rephrasing_keeps_known_product_and_registered_conflict` |
| 제품 후보 선택 해석 통합 | `test_candidate_choice_after_owner_clarification_uses_shared_parser`, `test_pending_candidate_language_check_never_queries_the_catalog` |
| 단일 확인 대상의 “있어/없어” 연결 | `test_short_certainty_answer_resolves_one_known_owners_pending_allergy`, `test_short_certainty_never_guesses_an_owner_or_multiple_conditions`, `test_short_denial_does_not_remove_registered_or_other_confirmed_allergies` |

연속 검증에서 추가로 확인한 제품 선택 대기의 재등장과 과거 답변에 따른 원래 질문의 의도 변경도 수정했다. `test_product_selection_then_diet_answer_keeps_the_same_temporary_allergy`는 대상 확인→제품 선택→식이 확인을 메모리·SQLite 재개방을 거쳐 검사한다. 제품 선택을 마치면 후보 대기는 해제하되, 아직 끝나지 않은 요청의 알레르기와 식이 조건은 유지한다. 완성된 판정 뒤 독립 질문에 임시 알레르기를 영구 적용하지 않는다.

미지원 조건은 지원 항목과 다른 제한 상태로 보존하며 자동 성분 판정을 지원한다고 주장하지 않는다. 성분 동의어 검증에 사용한 제품은 명시적인 원재료·출처를 가진 합성 테스트 자료다. 레시피 단계·대체 재료 검사는 로컬 검증기의 동작 확인이지 실제 외부 모델 호출 품질 검증이 아니다.

2026-09-25 수정 후 로컬 검증: 전체 **1,118개 통과**(기존 1,038개 + 신규 80개, 39.84초). `app`·`tests`·`evaluation` 컴파일 검사와 `git diff --check`도 통과했다. 아래 실제 연동·운영 검증은 완료로 표시하지 않는다.

## 2026-09-26 요청 의미·제품 재확인의 6개 수정

`test_food_chat_request_meaning.py`에 이전 1,118개 테스트 시점의 신규 재현 사례와 다음 경계를 추가했다.

| 수정 | 직접 연결된 테스트 |
| --- | --- |
| 심함·서술형 미지원 알레르기 누락 방지 | `test_unsupported_assertion_is_not_absent` |
| 개인 자기소개·불확실성·일반적인 사람 표현 구분 | `test_personal_copula_is_a_condition`, `test_uncertain_personal_copula_is_not_absent_or_confirmed`, `test_general_or_denied_condition_is_not_asserted` |
| 취소 부정·확인 질문 중단과 실제 취소 구분 | `test_negated_cancellation_preserves_request_allergy`, `test_stopping_questions_does_not_resolve_an_unanswered_condition`, `test_real_cancellation_clears_request_allergy` |
| ‘없이’·존댓말 제외 요청 반영 | `test_exclusion_request_blocks_milk_and_survives_storage`, `test_permission_question_is_not_a_directive`, `test_exclusion_prefix_does_not_become_a_new_product_name` |
| 같은 제품의 요리 질문과 다른 대상 구분 | `test_deictic_request_preserves_registered_warning`, `test_other_target_is_not_a_deictic_followup` |
| 조사 ‘로’ 처리·조회 실패와 제품 미지정 구분 | `test_instrumental_particle_resolves_to_catalog_candidates`, `test_real_product_name_ending_ro_is_preserved`, `test_unknown_source_never_silently_becomes_generic_recipe` |

검색 실패→제품명/바코드 보완→필요 시 후보 선택→최종 판정의 연속 대화에서 임시 알레르기·식이 목표를 유지한다. 보완 바코드까지 실패하면 확인 대기를 유지한다. OFF로 바뀐 대상에게서 다른 프로필로 알레르기를 옮기지 않으며, 완료 후 독립 질문에 임시 알레르기를 영구 적용하지 않는다. 제품 없는 일반 레시피로의 명시적 전환도 검사한다.

저장형 테스트는 메모리와 SQLite를 사용하며 SQLite 재개방·답변 재조회와 무상태 왕복을 포함한다. 최종 로컬 검증은 **전체 1,233개 통과**(기존 1,118개 + 신규 115개, 51.44초)다. `app`·`tests`·`evaluation` 컴파일 검사와 `git diff --check`도 통과했다. 외부 AI 활성화·실제 운영 DB 변경은 수행하지 않았다.

## 2026-09-26 나열 조건·복합 요청 후속 수정

1,233개 테스트 이후 발견한 6개 문제를 `test_food_chat_coordinated_requests.py`에 재현·경계 테스트로 추가했다.

| 수정 | 직접 연결된 테스트 |
| --- | --- |
| 앞선 알레르기 나열 항목 누락 방지 | `test_allergy_list_never_drops_shrimp`, `test_coordination_does_not_cross_a_predicate`, `test_lists_do_not_merge_different_owners` |
| 미지원 개인 조건의 미해석 상태 보존 | `test_uninterpreted_personal_allergy_stays_pending`, `test_general_information_is_not_a_personal_list` |
| 요청 문장을 포함한 제품 보완 답변의 조건 유지 | `test_complete_source_reply_preserves_unfinished_conditions`, `test_repeated_failed_source_reply_keeps_conditions`, `test_full_source_reply_does_not_reassign_off_owner` |
| 복합 취소·취소 부정의 조건 처리와 생성 동작 일치 | `test_compound_cancellation_stops_generation`, `test_negated_compound_cancellation_keeps_allergy`, `test_compound_cancellation_cannot_search_or_generate`, `test_cancellation_then_new_task_does_not_resume_old_allergy` |
| 나열한 제외 재료의 공통 서술 처리 | `test_coordinated_exclusions_block_milk`, `test_separate_predicates_and_permission_questions`, `test_coordinated_exclusions_do_not_mix_profile_owners` |
| 조사 없는 제품 지정의 조회 실패 처리 | `test_unresolved_unmarked_source_never_becomes_generic_or_old`, `test_generic_recipe_is_not_a_missing_catalog_name`, `test_stateless_condition_and_source_boundaries` |

신규 검사는 118개이며 저장형 HTTP(메모리·SQLite), SQLite 재개방, 저장 답변 재조회와 무상태 요청을 포함한다. 완료 이후 독립 질문에 임시 알레르기를 자동 적용하지 않는 경계와 기존 비교 취소 흐름도 함께 검사한다. `test_list_parsing_does_not_backtrack_exponentially`는 알려진 식품을 길게 나열한 뒤 다른 문장에서 알레르기를 진술했을 때 발생했던 과도한 재탐색을 검사한다.

최종 로컬 검증은 **전체 1,351개 통과**(기존 1,233개 + 신규 118개, 61.46초)다. `app`·`tests`·`evaluation` 컴파일 검사와 `git diff --check`도 통과했다. 변경 범위는 `AI/` 내부이며 커밋·푸시는 하지 않았다.

미지원 식품은 확인 대기이지 자동 판정 지원이 아니다. 여러 사람의 진술을 안전하게 연결할 수 없을 때는 프로필별 조건을 임의로 합치지 않고 확인하며, 임의 자연어 표현 전체의 처리를 보증하지 않는다. 실제 운영 DB·외부 AI는 호출하거나 변경하지 않았다.

## 2026-09-26 조건 보존·부분 취소·제품 재검색 수정

1,351개 테스트 이후 발견한 6개 문제를 `test_food_chat_request_updates.py`에 재현·경계 테스트로 추가했다.

| 수정 | 직접 연결된 테스트 |
| --- | --- |
| ‘그리고’ 나열의 모든 알레르기·제외 항목 반영 | `test_and_allergy_list_keeps_every_item`, `test_and_exclusion_list_blocks_milk`, `test_and_does_not_join_separate_allergy_predicates`, `test_and_does_not_merge_profile_owners` |
| 미지원 개인 진술·자기소개를 조건 없음으로 버리지 않음 | `test_unsupported_personal_statement_never_disappears`, `test_general_person_or_denial_is_not_a_personal_condition`, `test_uncertain_personal_identity_stays_uncertain` |
| 질문형 제품 보완 답변의 임시 알레르기·식이 목표 유지 | `test_question_style_source_reply_keeps_allergy_and_goals`, `test_stateless_source_question_round_trip`, `test_source_update_does_not_move_an_off_profiles_allergy` |
| 전체·부분 취소 구분 및 현재 발언의 조건·대상 보존 | `test_stopping_clarification_never_resolves_allergy`, `test_cancellation_keeps_conditions_from_current_utterance`, `test_current_condition_owner_survives_cancellation_rewrite`, `test_full_cancellation_still_ends_temporary_allergy` |
| 질문형 제품 지정의 조회 실패를 일반·과거 제품 레시피로 대체하지 않음 | `test_recipe_source_identity_does_not_depend_on_request_verb`, `test_unknown_question_style_source_cannot_use_old_or_generic_product`, `test_generic_question_does_not_require_a_catalog_product` |
| 반복 검색 실패 뒤 최신 제품 지정·추가 목표 유지 | `test_latest_goals_survive_repeated_failed_source_lookup`, `test_search_cancellation_keeps_request_conditions_but_clears_candidates`, `test_partial_search_cancellation_can_replace_source_in_same_message` |

신규 검사는 131개다. 메모리·SQLite 저장형 HTTP, SQLite 재개방·답변 재조회, 무상태 왕복, 직접 서비스 호출을 포함한다. `test_standalone_search_cancellation_never_queries_catalog`는 단독 검색 중단에서 검색·최종 생성 경로를 호출하지 않는지 검사한다. `test_partial_clarification_cancellation_is_not_a_product_name`은 부분 취소 문구가 제품명에 섞이지 않고 원래 제품 재확인을 이어가는지 검사한다. 후보 선택 대기 중 검색을 중단한 경우에도 원래 요청의 식이 목표를 복원하도록 보완했다.

최종 로컬 검증은 **전체 1,482개 통과**(기존 1,351개 + 신규 131개, 60.59초)다. 신규 모듈 단독 실행도 **131개 통과**(10.68초)했다. `app`·`tests`·`evaluation` 컴파일 검사와 `git diff --check`도 통과했다. 이번 수정은 `AI/` 내부로 제한했으며 다른 폴더의 기존 변경은 건드리지 않았다. 커밋·푸시는 하지 않았다.

공개 응답 필드·저장소 스키마·계정 알레르기는 변경하지 않았다. 미지원 알레르기는 자동 판정 지원이 아니라 확인 대기이며, 모든 자연어 표현의 처리를 보증하지 않는다. 임시 알레르기는 끝나지 않은 요청에만 유지하고 전체 취소·완료 이후 독립 질문에 자동 적용하지 않는다. 실제 운영 DB·외부 AI 호출·운영 채팅 데이터 변경은 수행하지 않았다.

## 2026-09-26 조건 변경·부분 취소·비교 적용의 8개 수정

1,482개 테스트 이후 발견한 8개 문제를 `test_food_chat_condition_changes.py`에 재현·경계 검사로 추가했다. 기능 범위를 확장하거나 실제 외부 서비스를 켜는 작업은 포함하지 않았다.

| 수정 | 직접 연결된 테스트 |
| --- | --- |
| ① 부분 취소 대상의 조사·단독 표현 때문에 임시 알레르기가 해제되지 않음 | `test_partial_stop_never_withdraws_pending_allergy`, `test_standalone_clarification_stop_does_not_deny_allergy` |
| ② 검색 중단 응답 전에 현재 발언의 새 조건 수집 | `test_search_stop_collects_new_conditions_before_acknowledgement`, `test_search_stop_parses_conditions_without_search_or_generation`, `test_search_stop_retains_new_allergy_owner_without_reassignment`, `test_search_stop_does_not_assign_off_owner_to_remaining_profile` |
| ③ 개인 진술·증상 보고·이중 부정과 일반 정보 질문 구분 | `test_personal_or_uncertain_allergy_never_becomes_absent`, `test_information_query_does_not_assign_personal_allergy`, `test_symptom_report_is_not_an_information_query` |
| ④ 재료 투입·사용의 부정 요청과 허용 여부 질문 구분 | `test_negative_ingredient_action_blocks_milk`, `test_ingredient_permission_question_is_not_exclusion` |
| ⑤ 채식과 독립적인 제외 조건을 비교·후속 영양 비교에도 적용 | `test_comparison_checks_exclusions_without_vegetarian_identity` |
| ⑥ 제품 식별과 식이 변경을 분리하고 실패 시 일반 레시피로 대체하지 않음 | `test_source_and_goal_change_preserve_unfinished_allergy`, `test_stateless_source_change_keeps_conditions` |
| ⑦ 목표 추가·철회·교체를 구분하고 계정 제한은 유지 | `test_goal_withdrawal_replaces_pending_goal`, `test_goal_only_updates_resume_source_and_keep_allergy`, `test_goal_update_operations_are_ordered_and_scoped`, `test_goal_withdrawal_never_removes_account_restriction`, `test_named_goal_removal_keeps_other_goals` |
| ⑧ 취소 앞에서 말한 현재 식이 목표 보존 | `test_current_goals_before_cancellation_survive`, `test_direct_service_keeps_goals_before_cancel_and_stopped_search` |

신규 검사는 128개다. 메모리·SQLite 저장형 HTTP, SQLite 재개방·답변 재조회, 무상태 왕복, 직접 서비스 호출을 포함한다. 최초 68개 재현·경계 검사는 수정 전 60개 실패·8개 통과였고, 수정 후 모두 통과했다. 이후 목표만 보내는 변경 답변, 단독 확인 중단, OFF 대상, 허용 질문, 목표 취소·초기화의 부정, 특정 목표만 해제하는 경계를 추가했다. 신규 모듈 최종 단독 실행은 **128개 통과**(18.23초)다.

최종 로컬 검증은 **전체 1,610개 통과**(기존 1,482개 + 신규 128개, 86.23초)다. `app`·`tests`·`evaluation` 컴파일 검사와 `git diff --check`도 통과했다. 이번 변경은 `AI/` 내부로 제한했고 다른 폴더의 기존 변경은 건드리지 않았다. 커밋·푸시는 하지 않았다.

`diet_goal_context.py`는 구조화된 `DietGoalUpdate`로 목표 변경을 표현하고 제품 조회·요청 재구성·최종 식이 계산에서 공통 해석을 사용한다. 기존 공개 응답 필드와 대화 저장소 스키마를 유지했다. 계정의 알레르기·식이 제한을 변경하지 않으며, 지원 범위 밖의 알레르기와 증상 진술을 진단하거나 자동 적합성 확인으로 바꾸지 않는다. 자연어 전체의 처리를 보증하는 검증은 아니다.

## 2026-09-28 커밋 전 저장소·평가 산출물 정리

이번 작업의 기준 실행은 **1,610개 통과**(87.29초)였습니다. 공개 HTTP 응답에서 내부 진단 필드를 제외하는 처리와 대화 저장소 분리는 이미 구현되어 있어 유지했습니다. 제품 판정·레시피 생성 범위를 새로 확대하지 않았습니다.

### 수정·정리 내용

- `repository.py`의 약 3,038줄 구현을 제품·활동 저장소별 공통 계약, 메모리, SQLite, MySQL 구현과 `mysql_support.py`로 분리했습니다. 기존 파일은 153줄의 호환 import 진입점이며, 기존 클래스와 같은 객체를 반환합니다. 제품·활동·대화 저장소는 하나의 MySQL 연결 풀 구현을 공유합니다.
- API 안정성 평가가 비공개 `pipeline_stage`를 필수로 요구하던 오류를 수정했습니다. 현재 공개 허용 목록 밖의 필드가 반환되면 평가가 실패합니다. 실제 LLM/RAG 평가 요약의 해당 필드 조회도 제거했습니다. 내부 감사·검증용 진단 필드는 삭제하지 않았습니다.
- API의 무의미한 `retention_days=None` 전달 함수와 신규 SQLite의 만료 인덱스 생성을 제거했습니다. 기존 DB의 컬럼·인덱스·과거 날짜·대화는 변경하지 않습니다. `purge_expired()`의 삭제 없는 호환 동작과 레거시 `histories` 기본 쓰기 비활성화를 유지했습니다.
- 과거 평가 결과 210개(39,245,038바이트)를 `results/archive/pre-cleanup-20260928/`로 이동했습니다. 이동 후 파일별 SHA-256을 대조했습니다. 영구 삭제하지 않았으며, 기존 `legacy-20260917/` 보관물은 유지했습니다. 보관 경로·복원·Git 제외 정책은 [평가 결과 보관 정책](artifact_retention.md)에 기록했습니다.

### 로컬 검증

- 전체 **1,623개 통과**(기존 1,610개 + 신규 13개, 100.73초).
- 신규 `test_repository_cleanup.py`: 기존 import와 분리 구현의 동일성, 연결 풀 공유, 신규 SQLite의 불필요한 인덱스 미생성, 기존 인덱스·과거 만료일이 있는 대화의 재개방·보존을 검사합니다.
- 신규 `test_evaluation_public_contract.py`: 평가 코드를 별도 프로세스에서 실행해 공개 응답 허용·비공개 필드 거부, 실제 `/chat` 계약, 잘못된 요청과 후속 제품 연결을 검사합니다. 테스트 프로세스는 외부 AI를 끄고 메모리 제품을 사용합니다.
- 별도 API 안정성 평가: **24/24 요청 통과**, 계약 오류 0개, 잘못된 요청 6/6 처리, 후속 대화 통과. 이 실행의 p95는 86.33ms이며 메모리·규칙 경로 결과이지 운영·LLM 응답시간이 아닙니다.
- `app`·`tests`·`evaluation` 컴파일 검사와 `git diff --check` 통과. 변경은 `AI/` 안으로 제한했고, 커밋·푸시는 하지 않았습니다.

### 실제 연동 확인과 차단 사유

**MySQL은 읽기 전용 연결 및 분리한 저장소 코드의 스키마 준비 검사를 실제로 실행했습니다.** 최초 샌드박스 네트워크 제한으로 발생한 2003 오류 후, 허용된 읽기 전용 재시도에서 `SELECT 1`에 성공했습니다. 접속 주소·자격증명·사용자 대화 원문은 결과에 기록하지 않았습니다.

| 검사 대상 | 현재 연결된 DB의 결과 |
| --- | --- |
| 제품 저장소 | 기본 식별·제품명·성분/영양 컬럼과 제품 행 존재 확인. 라벨 출처·수집 시점·내용 결합 검증용 필수 컬럼 9개 누락 |
| 활동 로그 | `chatbot_logs`는 있으나 현재 코드가 요구하는 필수 컬럼 7개 누락. 준비 검사 실패 |
| 저장형 채팅 | `chatbot_conversations`, `chatbot_messages` 테이블 없음. 준비 검사 실패 |
| 공통 연결 풀 | 제품·활동·대화 저장소가 같은 연결 풀 객체를 사용하는 것 확인 |

따라서 MySQL 연결 자체는 확인했지만 실제 채팅 저장·재조회·앱 전체 기동까지 성공한 것은 아닙니다. 현재 연결된 DB가 운영 DB인지도 이 검사로 단정하지 않습니다. 스키마를 자동 생성하거나 마이그레이션을 실행하지 않았습니다.

DB 담당자는 대상 환경·기존 적용 이력을 먼저 확인한 뒤 `20260903_product_label_provenance.sql`, `20260903_product_label_revision_binding.sql`, 로그 확장 마이그레이션(`20260730_chatbot_safety_monitoring.sql`, `20260828_policy_provenance_trace.sql`, `20260828_response_trace.sql`, `20260909_chatbot_log_product_trace.sql`), `20260828_conversation_history.sql`의 필요한 부분을 검토해야 합니다. 이미 적용된 ALTER 문을 전체 재실행하지 않습니다. 라벨 출처 컬럼만 추가해도 제품 근거가 자동으로 검증되는 것은 아니며 실제 수집 데이터가 필요합니다. `20260910_conversation_no_expiry.sql`은 이전 보존기간 값이 있는 DB용이며 이번에는 실행하지 않았습니다.

**외부 LLM/RAG 검증 명령은 사전 점검에서 중단되어 실제 시나리오 0/10개를 실행했습니다.** API 키 존재 여부는 확인했지만 `FOOD_CHATBOT_ENABLE_EXTERNAL_AI=false`이고 동의 서명 설정이 없습니다. 기능이 꺼져 있어 provider 가용성도 false로 표시되며, 키 인증 실패가 확인된 것은 아닙니다. 실제 모델 호출·과금·질문 또는 프로필 전송은 하지 않았습니다. RAG 문서·인덱스 메타데이터는 준비 상태지만 실제 임베딩·검색 결과의 검증은 아닙니다.

실제 LLM 검증을 이어가려면 가상 질문·프로필을 이용한 외부 전송·비용 범위를 확인하고 활성화·동의 설정을 갖춰야 합니다. DB 저장형 검증은 승인된 테스트 DB에 필요한 스키마를 적용한 뒤 수행해야 합니다. 이번 정리를 위해 안전 설정을 우회하지 않았습니다.

## 아직 완료로 표시하지 않는 항목

- 실제 운영 DB의 제품·대체 후보·라벨 출처 충족 여부
- 실제 MySQL 대화 저장·재조회·다중 턴 처리
- 동의를 갖춘 실제 외부 LLM 호출 및 답변 품질
- 실제 RAG 임베딩·검색, 배포 시 인덱스 준비 검사
- 운영 배포의 인증·서명·네트워크·저장소 통합 동작
- 모델·API·라우터 등 저장소 이외 대형 파일의 추가 분리

외부 AI 호출을 켜거나 DB 데이터를 수정하지 않았습니다. 제품·활동 저장소 구조 정리와 읽기 전용 MySQL 준비 검사는 위 2026-09-28 항목까지 반영됐지만, 이 문서의 테스트 연결만으로 남은 실제 연동·운영 검증을 완료 처리하지 않습니다.
