# 찍먹 워크스페이스

본 디렉토리는 AI 기반 식품 알러지 스캐너 및 챗봇 앱 '찍먹'의 프론트엔드와 백엔드 코드를 포함하고 있습니다.

**작업 전 주의사항**
* 빌드 충돌을 막기 위해 **절대로 최상위(`jjikmuk`) 폴더 전체를 한 번에 열지 마세요.**
* **프론트엔드 개발자:** `Android Studio`를 실행하고 **`frontend` 폴더만** 엽니다.
* **백엔드 개발자:** `IntelliJ IDEA`를 실행하고 **`backend` 폴더만** 엽니다.

---

## 1. 프론트엔드 (모바일 앱)
**Kotlin Multiplatform (Compose)** 기반으로 작성되어 있으며, iOS와 Android를 동시에 개발합니다.

* **작업 디렉토리:** `frontend/`
* **사용 IDE:** Android Studio

### 프론트엔드 핵심 작업 공간 (`frontend/composeApp/src/`)
대부분의 UI 및 앱 로직 개발은 `commonMain`에서 이루어집니다.

* **`commonMain/kotlin/org/jjikmuk/app/`** **(메인 작업 공간)**
  * 안드로이드와 iOS가 100% 코드를 공유하는 공간입니다.
  * 모든 UI(화면), 상태 관리, 네트워크 통신 로직을 여기에 작성하세요.
* **`commonMain/composeResources/`**
  * 앱에서 공통으로 사용할 이미지 파일(`drawable`)이나 폰트 등을 넣는 공간입니다.
* **`androidMain/`** & **`iosMain/`**
  * 각 OS 기기 고유의 기능(예: 카메라 권한, 바코드 스캐너 네이티브 API 연동 등)이 필요할 때만 코드를 작성하는 공간입니다.

> **참고:** `frontend/iosApp/` 폴더는 iOS 빌드를 위한 껍데기(Xcode 프로젝트)입니다. Swift 코드를 직접 건드릴 일이 없다면 무시하셔도 좋습니다.

---

## 2. 백엔드 (서버 및 API)
**Spring Boot (Kotlin)** 기반으로 작성되어 있으며, 데이터베이스 연동 및 AI(RAG) 챗봇 통신을 담당합니다.

* **작업 디렉토리:** `backend/`
* **사용 IDE:** IntelliJ IDEA (Community 또는 Ultimate)

### 백엔드 핵심 작업 공간 (`backend/src/`)
RESTful API 개발과 DB 엔티티 설계가 이루어지는 곳입니다.

* **`main/kotlin/org/jjikmuk/backend/`** **(메인 작업 공간)**
  * Controller(API 주소 매핑), Service(비즈니스 로직), Repository(DB 접근), Entity(데이터베이스 테이블 설계) 코드를 작성하세요.
* **`main/resources/`**
  * `application.yml` (또는 `.properties`) 파일이 위치할 곳입니다.
  * 서버 포트, 데이터베이스 연결 정보, 외부 API 설정 등을 관리합니다.
* **`test/kotlin/org/jjikmuk/backend/`**
  * 작성한 API나 로직이 잘 작동하는지 검증하는 단위 테스트(Unit Test) 코드를 작성하는 공간입니다.

---

## 규칙
* `.gradle`, `.idea`, `build` 폴더는 개발 툴이 자동으로 생성하는 폴더입니다. **절대 수동으로 수정하지 마세요.**
* 새로운 라이브러리를 추가할 때는, 프론트엔드는 `frontend` 내의 `build.gradle.kts`에, 백엔드는 `backend` 내의 `build.gradle.kts`에 각각 선언해야 합니다.

## GitHub 사용 규칙
안전한 협업과 코드 덮어쓰기(충돌) 방지를 위해 **`main` 브랜치에 직접 푸시(Push)하는 것은 절대 금지**합니다. 반드시 각자의 이름으로 된 브랜치를 새로 파서 작업한 후, 안전하게 `main`과 합치는 방식을 사용해 주세요.

### 기본 작업 순서

**1. 최신 코드 가져오기**
* 작업을 시작하기 전, 항상 `main` 브랜치의 최신 상태를 내 컴퓨터로 먼저 가져옵니다.
```bash
git checkout main
git pull origin main
```

**2. 내 이름으로 새 브랜치 생성 및 이동**
* main 브랜치를 바탕으로 본인의 이름이 들어간 새 브랜치를 만듭니다. (예: feature/seongmin, feat/yujin 등)

```bash
git checkout -b feature/본인이름
```
**3. 각자 브랜치에서 작업 후 커밋 및 푸시**
* 본인의 브랜치에서 코딩을 진행한 뒤, 작업 내역을 깃허브에 올립니다. (이때 코드는 main이 아닌 내 브랜치에만 안전하게 올라갑니다.)

```bash
git add .
git commit -m "작업 내용 간단히 설명 (예: 바코드 스캔 UI 초안 작성)"
git push origin -u feature/본인이름
```
**4. Pull Request (PR) 생성 및 합치기 (Merge)**

* 깃허브 웹사이트 저장소에 접속합니다.

* 내 브랜치를 main 브랜치에 합쳐달라는 PR을 생성합니다.

* 충돌이 없는지 확인하고, (필요하다면 팀원의 리뷰를 거친 후) main 브랜치로 최종 병합(Merge)합니다.