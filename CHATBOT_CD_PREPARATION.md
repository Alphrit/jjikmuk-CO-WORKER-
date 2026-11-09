# 챗봇 자동 CD 준비

이 PR은 AI/ 원본을 변경하지 않고 chatbot 브랜치의 테스트와 자동 배포 연결만 추가합니다. 백엔드 저장소의 검토 대상 재사용 workflow를 고정 커밋으로 참조합니다. 테스트가 실패하면 배포하지 않습니다.

**초기 상태는 배포 꺼짐입니다.** 저장소 변수 CHATBOT_CD_ENABLED를 false로 두고, 사용자 승인으로 실제 서버 설치·호환성 확인·최초 전환이 끝난 뒤 켭니다. PR 병합만으로 설치 준비가 완료되는 것은 아닙니다.

- 원본: Alphrit/jjikmuk-CO-WORKER- 의 chatbot 브랜치 AI/.
- 일반 코드 변경: 전체 pytest → 정확한 Git 파일 패키지 → AWS OIDC → DB/설정/런타임/RAG 기준 대조 → 서비스 전환·안정성 검사.
- DB migration/requirements/RAG 자료 변경: 자동 실행하지 않고 알림과 함께 보류. 승인된 수동 준비 후 기준 등록 필요.
- 검증 실패 후 복귀: DB/RAG/설정이 유지된 경우 이전 코드로 복귀. 데이터 자동 복원은 없음.
- 실패 알림은 기본 main 브랜치의 별도 작은 PR에서 연결합니다. 작성자·병합자·fisfin을 멘션합니다. 병합 자체는 취소되지 않습니다.

담당자는 chatbot 보호의 필수 검사로 실제 CI에 표시되는 `cd / build`를 지정하고, `chatbot-production` 환경을 chatbot만/리뷰 없음/대기 없음으로 구성합니다. 기본 main 앱 코드를 이 브랜치에 합치지 않습니다.

[통합 연결·승인 범위](https://github.com/KW-coworker/JJIKMUK-Backend/blob/feat/unified-server-cd/ops/operations/UNIFIED_CONNECTION.md)
[저장소 담당자 설정 요청서](https://github.com/KW-coworker/JJIKMUK-Backend/blob/feat/unified-server-cd/ops/operations/GITHUB_UNIFIED_OWNER_REQUEST.md)
