# Scanner Feature README

이 문서는 JJIKMUK Android 앱의 바코드 스캐너 기능을 다른 팀원이 이해하고, API/화면/앱 내 진입점과 연결할 때 참고하는 기준 문서입니다.

## 현재 구조

스캐너는 단순히 바코드 문자열만 반환하는 화면이 아닙니다. 현재 구현은 카메라 또는 갤러리에서 바코드를 인식한 뒤, 프론트에서 바로 상품 조회 API를 호출하고 결과 바텀시트를 표시합니다.

주요 파일:

| 역할 | 파일 |
| --- | --- |
| 스캐너 화면 Activity | `feature/scanner/BarcodeScannerActivity.kt` |
| 스캐너 상태 및 API 호출 | `feature/scanner/ScannerViewModel.kt` |
| 스캐너 UI 상태 모델 | `feature/scanner/ScannerUiState.kt` |
| 카메라 스캔 가이드 커스텀 View | `feature/scanner/ScanGuideView.kt` |
| 갤러리 이미지 바코드 선택 오버레이 | `feature/scanner/GalleryBarcodeOverlayView.kt` |
| 스캐너 레이아웃 | `res/layout/activity_barcode_scanner.xml` |
| 상품 스캔 Repository 인터페이스 | `domain/repository/ProductScanRepository.kt` |
| 상품 스캔 Repository 구현 | `data/repository/ProductScanRepositoryImpl.kt` |
| 상품 조회 API | `data/remote/api/ProductApi.kt` |
| 상품 조회 응답 DTO | `data/remote/dto/product/ProductScanResponseDto.kt` |
| Hilt Repository 바인딩 | `di/RepositoryModule.kt` |
| 하단 탭에서 스캐너 진입 | `feature/navigation/BottomNavController.kt` |

## 사용자 흐름

1. 사용자가 하단 네비게이션의 카메라 버튼을 누릅니다.
2. `BottomNavController`가 `BarcodeScannerActivity`를 실행합니다.
3. 스캐너 화면에서 카메라 권한을 확인합니다.
4. 권한이 있으면 CameraX Preview와 ImageAnalysis를 시작합니다.
5. 사용자가 중앙 스캔 버튼을 누르면 10초 동안 바코드 인식이 활성화됩니다.
6. ML Kit이 UPC-A 또는 EAN-13 바코드를 감지합니다.
7. 감지한 바코드는 13자리 EAN-13 문자열로 정규화됩니다.
8. `ScannerViewModel.submitBarcode()`가 상품 조회 API를 호출합니다.
9. 조회 성공 또는 미등록 상품 상태에 따라 결과 바텀시트를 표시합니다.

갤러리 흐름도 지원합니다.

1. 사용자가 갤러리 버튼을 누릅니다.
2. 이미지 선택기가 열립니다.
3. 선택된 이미지의 EXIF 회전을 보정하고 최대 2048px 기준으로 다운샘플링합니다.
4. ML Kit이 이미지 안의 바코드 후보를 찾습니다.
5. 후보 박스를 사용자가 직접 선택합니다.
6. 선택 버튼을 누르면 동일하게 상품 조회 API를 호출합니다.

## 앱 진입점

현재 스캐너는 하단 네비게이션의 카메라 버튼에서 실행됩니다.

```kotlin
rootView.findViewById<View?>(R.id.navCamera)?.setOnClickListener {
    context.startActivity(
        android.content.Intent(context, BarcodeScannerActivity::class.java)
    )
}
```

새 화면에서 스캐너를 실행해야 하면 같은 방식으로 `BarcodeScannerActivity`를 시작하면 됩니다.

```kotlin
startActivity(Intent(requireContext(), BarcodeScannerActivity::class.java))
```

주의: 현재 구조는 `ActivityResultContract`로 바코드 문자열을 반환하지 않습니다. 스캐너 Activity 내부에서 상품 조회까지 처리합니다. 다른 화면에서 바코드만 받아야 하는 요구가 생기면 별도 contract 또는 result extra를 다시 설계해야 합니다.

## 카메라와 바코드 인식

사용 라이브러리:

```kotlin
implementation("androidx.camera:camera-core:1.3.0")
implementation("androidx.camera:camera-camera2:1.3.0")
implementation("androidx.camera:camera-lifecycle:1.3.0")
implementation("androidx.camera:camera-view:1.3.0")
implementation("com.google.mlkit:barcode-scanning:17.2.0")
```

필수 Manifest 설정:

```xml
<uses-permission android:name="android.permission.CAMERA" />
<uses-permission android:name="android.permission.INTERNET" />

<activity
    android:name=".feature.scanner.BarcodeScannerActivity"
    android:exported="false" />
```

스캐너는 다음 바코드만 상품 조회 대상으로 사용합니다.

| 입력 | 처리 |
| --- | --- |
| UPC-A 12자리 | 앞에 `0`을 붙여 13자리로 변환 |
| EAN-13 13자리 | 그대로 사용 |
| 그 외 길이 | 무시 |

## 플래시 버튼

우측 상단 `btnScannerFlash`가 CameraX torch를 제어합니다.

- 카메라 바인딩 후 `cameraInfo.hasFlashUnit()`으로 지원 여부를 확인합니다.
- 플래시가 켜지면 아이콘 tint가 `#FFDD4A`로 바뀝니다.
- Activity 종료 시 `enableTorch(false)`로 플래시를 끕니다.

관련 코드:

- `BarcodeScannerActivity.startCamera()`
- `BarcodeScannerActivity.toggleFlash()`
- `BarcodeScannerActivity.updateFlashButtonTint()`

## API 연결

스캐너가 호출하는 API는 다음과 같습니다.

```http
GET /api/products/{barcode}?userId={userId}
```

Retrofit 정의:

```kotlin
@GET("api/products/{barcode}")
suspend fun scanProduct(
    @Path("barcode") barcode: String,
    @Query("userId") userId: Long? = null
): Response<ProductScanResponseDto>
```

응답 DTO의 핵심 구조:

```kotlin
data class ProductScanResponseDto(
    val message: String?,
    val data: ProductScanDataDto?
)

data class ProductScanDataDto(
    val product: ProductInfoDto?,
    val nutrientPercents: NutrientPercentsDto?,
    val analysis: ProductAnalysisDto?
)
```

Repository는 DTO를 도메인 모델로 변환합니다.

- `ProductInfoDto` -> `ScannedProduct`
- `NutrientPercentsDto` -> `NutrientPercents`
- `ProductAnalysisDto` -> `ProductAnalysis`

HTTP 404는 미등록 상품으로 처리합니다. 이 경우 `ScannerResult.requiresRegistration = true`이고, 결과 바텀시트에 `제품 등록하기` 문구가 표시됩니다.

404가 아닌 에러는 Toast로 메시지를 표시하고 결과 바텀시트는 열지 않습니다.

## 빌드 설정

`frontend/app/build.gradle.kts`에서 다음 Gradle property를 `BuildConfig`로 주입합니다.

| Property | 기본값 | 용도 |
| --- | --- | --- |
| `API_BASE_URL` | `http://10.0.2.2:8080/` | Retrofit base URL |
| `USE_MOCK_SCAN` | `false` | true면 실제 API 대신 mock 결과 사용 |
| `SCAN_USER_ID` | 빈 문자열 | API 호출 시 `userId` query로 전달 |

로컬에서 mock 스캔을 쓰려면 `frontend/gradle.properties` 또는 로컬 Gradle property에 다음처럼 설정합니다.

```properties
USE_MOCK_SCAN=true
SCAN_USER_ID=1
```

실제 서버를 연결할 때는 예시처럼 base URL을 맞춥니다.

```properties
API_BASE_URL=http://10.0.2.2:8080/
USE_MOCK_SCAN=false
```

에뮬레이터에서 로컬 PC의 백엔드 서버에 접근할 때는 `localhost`가 아니라 `10.0.2.2`를 사용해야 합니다.

## UI 구성

`activity_barcode_scanner.xml`의 주요 View ID:

| ID | 역할 |
| --- | --- |
| `barcodePreviewView` | CameraX preview |
| `btnScannerClose` | 스캐너 종료 |
| `btnScannerFlash` | 플래시 on/off |
| `scanGuideView` | 스캔 가이드 및 스캔 라인 애니메이션 |
| `btnScanGallery` | 갤러리 이미지 선택 |
| `btnScanCapture` | 10초 스캔 시도 시작 |
| `layoutPreviousScans` | 마지막 조회 결과 다시 보기 |
| `galleryScanContainer` | 갤러리 이미지 분석 화면 |
| `galleryBarcodeOverlay` | 갤러리 바코드 후보 박스 |
| `btnGalleryReselect` | 이미지 다시 선택 |
| `btnGallerySelectBarcode` | 선택한 바코드 조회 |
| `scannerResultScrim` | 결과 바텀시트 배경 dim |
| `scannerResultSheet` | 상품 조회 결과 바텀시트 |

최근 UI 변경 사항:

- 우측 상단에 플래시 버튼을 추가했습니다.
- 기존 스캔 모드 텍스트 영역과 상품 비교 버튼을 제거했습니다.
- 갤러리 버튼과 스캔 버튼 위치를 `scanGuideView` 기준으로 다시 배치했습니다.

## 결과 바텀시트 상태

상품이 조회된 경우:

- `Safe` 또는 `Caution` 표시
- 상품명 표시
- 위험 성분 또는 분석 메시지 표시
- 칼로리, 당류, 지방, 나트륨 표시
- 일일 기준치 비율이 있으면 수치 아래에 `%`로 표시
- 기본 버튼 문구는 `MORE →`

상품이 없거나 404인 경우:

- 등록 안내 메시지 표시
- 영양 정보 영역 숨김
- 기본 버튼 문구는 `제품 등록하기`

현재 `btnScanResultPrimary`에는 클릭 동작이 연결되어 있지 않습니다. 상품 상세 화면 이동 또는 상품 등록 화면 이동을 붙일 팀원은 이 버튼의 클릭 리스너를 추가해야 합니다.

## 통합할 때 확인할 것

1. `BarcodeScannerActivity`가 Manifest에 등록되어 있는지 확인합니다.
2. `CAMERA`, `INTERNET` 권한이 Manifest에 있는지 확인합니다.
3. `RepositoryModule`에 `ProductScanRepositoryImpl` 바인딩이 유지되어야 합니다.
4. `NetworkModule`의 Retrofit base URL이 `BuildConfig.API_BASE_URL`을 사용해야 합니다.
5. 백엔드의 상품 조회 응답 필드명이 `ProductScanResponseDto`와 맞아야 합니다.
6. 404를 미등록 상품으로 볼지, 다른 status/body로 볼지는 백엔드와 맞춰야 합니다.
7. 사용자별 분석이 필요하면 `SCAN_USER_ID` 임시 값 대신 실제 로그인 사용자 ID를 연결해야 합니다.
8. 상품 등록 화면 또는 상세 화면을 연결하려면 `btnScanResultPrimary` 클릭 동작을 구현해야 합니다.

## 테스트 체크리스트

기능 확인:

- 카메라 권한 허용 시 preview가 정상 표시된다.
- 카메라 권한 거부 시 Activity가 종료된다.
- 스캔 버튼을 누른 뒤 EAN-13 바코드를 인식하면 결과 바텀시트가 열린다.
- UPC-A 12자리 바코드는 앞에 `0`이 붙은 13자리로 조회된다.
- 10초 동안 인식 실패 시 Toast가 표시되고 스캔 애니메이션이 멈춘다.
- 조회 성공 후 바텀시트를 내리면 이전 결과 버튼이 나타난다.
- 이전 결과 버튼을 누르면 마지막 결과가 다시 열린다.
- 우측 상단 플래시 버튼을 누르면 플래시가 켜지고 색상이 바뀐다.
- 플래시가 켜진 상태에서 화면을 닫으면 플래시가 꺼진다.
- 갤러리 이미지에서 바코드 후보 박스가 표시된다.
- 갤러리 후보를 선택해야 선택 버튼이 활성화된다.
- 미등록 상품 응답 또는 404에서 등록 안내 바텀시트가 표시된다.
- 500 등 서버 오류에서는 Toast만 표시되고 바텀시트가 열리지 않는다.

환경 확인:

- `USE_MOCK_SCAN=true`에서 mock 결과가 표시된다.
- `USE_MOCK_SCAN=false`에서 실제 서버 API가 호출된다.
- 에뮬레이터에서 `API_BASE_URL=http://10.0.2.2:8080/`로 로컬 서버에 접근된다.
- 실기기에서는 PC IP 또는 배포 서버 URL을 사용한다.

## 현재 남은 연결 작업

아래 항목은 스캐너의 기본 동작은 아니지만 앱 통합 단계에서 이어서 처리해야 합니다.

- `btnScanResultPrimary` 클릭 시 상품 상세 또는 상품 등록 화면으로 이동
- 홈 화면의 스캔 대상 프로필 선택값을 실제 사용자 ID 또는 가족 프로필 ID와 연결
- `SCAN_USER_ID` Gradle property 제거 후 로그인 세션 기반 사용자 정보 사용
- 이전 스캔 기록을 메모리의 마지막 1건이 아니라 서버 또는 로컬 저장소와 연결
- 기존 `docs/frontend/barcode-scanner-integration.md`는 오래된 계약 방식 내용이 섞여 있으므로, 현재 구현 기준은 이 README를 우선 참고
