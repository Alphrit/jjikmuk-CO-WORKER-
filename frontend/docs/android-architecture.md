# Android Architecture Guide

## 1. 프로젝트 개요

이 문서는 찍먹 Android 앱의 기본 아키텍처와 패키지 구조를 설명합니다.

현재 Android 앱은 다음 기준으로 개발합니다.

- Language: Kotlin
- UI: XML
- Architecture: MVVM
- DI: Hilt
- State Management: StateFlow
- View Access: ViewBinding
- Local Preference: DataStore
- 화면 구조: MainActivity + Fragment 기반

현재 단계에서는 백엔드 API 연동 전이므로 일부 데이터는 FakeRepository 또는 DummyData를 통해 제공합니다.

이후 Retrofit API 연동 시에도 ViewModel, Repository, UI 계층의 책임 분리는 유지합니다.

---

## 2. 전체 패키지 구조

기본 패키지 구조는 다음과 같습니다.

```txt
com.coworker.jjikmuk
├── JjikmukApplication.kt
├── MainActivity.kt
│
├── core
│   ├── base
│   ├── common
│   ├── extension
│   ├── permission
│   └── util
│
├── data
│   ├── local
│   │   └── dummy
│   │
│   ├── remote
│   │   ├── api
│   │   └── dto
│   │
│   └── repository
│
├── di
│   └── RepositoryModule.kt
│
├── domain
│   ├── model
│   ├── repository
│   └── usecase
│
└── feature
    ├── auth
    ├── chat
    ├── history
    ├── home
    ├── meal
    ├── mypage
    ├── navigation
    ├── product
    └── scanner
```

---

## 3. 각 패키지 역할

### 3.1 core

`core`는 특정 화면에 종속되지 않는 공통 코드를 둡니다.

예시:

```txt
core
├── common
├── extension
├── permission
└── util
```

사용 기준:

- 여러 feature에서 공통으로 사용하는 유틸
- Android 확장 함수
- 공통 상수
- 권한 처리
- 날짜/문자열 포맷터

주의할 점:

`core`는 특정 feature 화면을 직접 알면 안 됩니다.

좋지 않은 예:

```txt
core → feature.home.HomeFragment
core → feature.history.HistoryFragment
```

특정 화면을 직접 다루는 네비게이션 코드는 `feature/navigation` 쪽에 둡니다.

---

### 3.2 data

`data`는 실제 데이터를 가져오는 구현체를 둡니다.

예시:

```txt
data
├── local
│   └── dummy
│
├── remote
│   ├── api
│   └── dto
│
└── repository
```

역할:

- Repository 구현체
- FakeRepository
- DummyData
- Retrofit API
- DTO
- Local storage
- DataStore
- Room 사용 시 DAO, Entity, Database

현재 백엔드 API 연동 전에는 `data/local/dummy`와 `data/repository`를 이용해 더미 데이터를 제공합니다.

예시:

```txt
data/local/dummy/ProductDummyData.kt
data/repository/ProductRepositoryImpl.kt
data/repository/ChatRepositoryImpl.kt
data/repository/FakeUserProfileRepository.kt
```

주의할 점:

`data` 계층은 `feature` 계층을 import하지 않습니다.

좋지 않은 예:

```kotlin
import com.coworker.jjikmuk.feature.product.dummy.ProductDummyData
```

좋은 예:

```kotlin
import com.coworker.jjikmuk.data.local.dummy.ProductDummyData
```

---

### 3.3 domain

`domain`은 앱의 핵심 모델과 기능 명세를 둡니다.

예시:

```txt
domain
├── model
├── repository
└── usecase
```

역할:

- 순수 데이터 모델
- Repository interface
- UseCase

예시:

```txt
domain/model/Product.kt
domain/model/UserProfile.kt
domain/repository/ProductRepository.kt
domain/repository/ChatRepository.kt
domain/repository/UserProfileRepository.kt
```

주의할 점:

`domain` 모델에는 Android UI 리소스를 넣지 않습니다.

좋지 않은 예:

```kotlin
data class Product(
    val id: String,
    val name: String,
    val imageResId: Int
)
```

좋은 예:

```kotlin
data class Product(
    val id: String,
    val category: String,
    val name: String,
    val allergyTags: List<String> = emptyList()
)
```

`imageResId`는 Android drawable 리소스이므로 `domain`이 아니라 `feature`의 UI 모델에서 관리합니다.

---

### 3.4 di

`di`는 Hilt Module을 둡니다.

예시:

```txt
di
└── RepositoryModule.kt
```

Repository interface와 구현체를 연결합니다.

```kotlin
@Module
@InstallIn(SingletonComponent::class)
abstract class RepositoryModule {

    @Binds
    @Singleton
    abstract fun bindProductRepository(
        impl: ProductRepositoryImpl
    ): ProductRepository

    @Binds
    @Singleton
    abstract fun bindChatRepository(
        impl: ChatRepositoryImpl
    ): ChatRepository
}
```

ViewModel에서는 Repository 구현체를 직접 생성하지 않고 interface를 주입받습니다.

좋지 않은 예:

```kotlin
class ChatViewModel : ViewModel() {
    private val chatRepository = ChatRepositoryImpl()
}
```

좋은 예:

```kotlin
@HiltViewModel
class ChatViewModel @Inject constructor(
    private val chatRepository: ChatRepository
) : ViewModel()
```

---

### 3.5 feature

`feature`는 화면 단위 코드를 둡니다.

예시:

```txt
feature
├── home
├── chat
├── product
├── history
├── scanner
├── mypage
└── navigation
```

각 feature에는 보통 다음 파일이 들어갑니다.

```txt
FeatureFragment.kt
FeatureViewModel.kt
FeatureUiState.kt
FeatureUiModel.kt
```

예시:

```txt
feature/home
├── HomeFragment.kt
├── HomeViewModel.kt
├── HomeUiState.kt
├── HomeProfileUiModel.kt
└── HomeProfileMapper.kt
```

---

## 4. MVVM 구조

이 프로젝트는 MVVM 구조를 사용합니다.

기본 흐름은 다음과 같습니다.

```txt
Fragment
↓
ViewModel
↓
Repository interface
↓
RepositoryImpl
↓
API / DummyData / Local Storage
```

예시:

```txt
ChatFragment
↓
ChatViewModel
↓
ChatRepository
↓
ChatRepositoryImpl
↓
ProductRepository
↓
ProductRepositoryImpl
↓
ProductDummyData
```

---

## 5. 각 계층의 책임

### 5.1 Fragment

Fragment는 화면 표시와 사용자 이벤트 전달만 담당합니다.

Fragment에서 하는 일:

- XML View 초기화
- 클릭 이벤트 연결
- RecyclerView Adapter 연결
- ViewModel의 uiState 관찰
- 화면 이동 처리

Fragment에서 하지 않는 일:

- Repository 직접 생성
- API 직접 호출
- 더미 데이터 직접 보유
- 비즈니스 로직 처리
- 복잡한 데이터 가공

예시:

```kotlin
private fun observeViewModel() {
    viewLifecycleOwner.lifecycleScope.launch {
        viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
            viewModel.uiState.collect { state ->
                chatMessageAdapter.submitList(state.messages)
            }
        }
    }
}
```

---

### 5.2 ViewModel

ViewModel은 화면 상태와 화면 이벤트 처리를 담당합니다.

ViewModel에서 하는 일:

- uiState 관리
- Repository 호출
- 로딩/성공/실패 상태 관리
- domain model을 ui model로 변환
- 사용자 이벤트 처리

ViewModel에서 하지 않는 일:

- View 직접 참조
- Context 직접 보관
- FragmentManager 사용
- Activity/Fragment 직접 호출

예시:

```kotlin
@HiltViewModel
class ChatViewModel @Inject constructor(
    private val chatRepository: ChatRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()
}
```

---

### 5.3 Repository

Repository는 데이터 출처를 추상화합니다.

Repository interface는 `domain/repository`에 둡니다.

```kotlin
interface ProductRepository {
    fun getAllProducts(): List<Product>
    fun getRecommendProducts(limit: Int): List<Product>
    fun findProductById(productId: String): Product?
}
```

Repository 구현체는 `data/repository`에 둡니다.

```kotlin
class ProductRepositoryImpl @Inject constructor() : ProductRepository {
    override fun getAllProducts(): List<Product> {
        return ProductDummyData.recommendProducts
    }
}
```

---

## 6. Hilt 사용 규칙

### 6.1 Application

`JjikmukApplication`에는 `@HiltAndroidApp`을 붙입니다.

```kotlin
@HiltAndroidApp
class JjikmukApplication : Application()
```

`AndroidManifest.xml`에도 등록되어 있어야 합니다.

```xml
<application
    android:name=".JjikmukApplication"
    ... >
```

---

### 6.2 Activity / Fragment

Hilt ViewModel을 사용하는 Activity 또는 Fragment에는 `@AndroidEntryPoint`를 붙입니다.

```kotlin
@AndroidEntryPoint
class MainActivity : AppCompatActivity()
```

```kotlin
@AndroidEntryPoint
class ChatFragment : Fragment()
```

---

### 6.3 ViewModel

Hilt로 의존성을 주입받는 ViewModel에는 `@HiltViewModel`과 `@Inject constructor`를 사용합니다.

```kotlin
@HiltViewModel
class HomeViewModel @Inject constructor(
    private val userProfileRepository: UserProfileRepository
) : ViewModel()
```

---

### 6.4 Repository

Repository 구현체에는 `@Inject constructor`를 붙입니다.

```kotlin
class ChatRepositoryImpl @Inject constructor(
    private val productRepository: ProductRepository
) : ChatRepository
```

Repository interface와 구현체 연결은 `RepositoryModule`에서 처리합니다.

```kotlin
@Module
@InstallIn(SingletonComponent::class)
abstract class RepositoryModule {

    @Binds
    @Singleton
    abstract fun bindChatRepository(
        impl: ChatRepositoryImpl
    ): ChatRepository
}
```

---

## 7. Domain Model과 UI Model 분리 기준

이 프로젝트에서는 `domain model`과 `ui model`을 분리합니다.

### 7.1 Domain Model

`domain/model`에 위치합니다.

특징:

- 앱의 순수 데이터
- Android UI 리소스 없음
- 화면 선택 상태 없음
- 서버/API/DB 구조와 가까움

예시:

```kotlin
data class Product(
    val id: String,
    val category: String,
    val name: String,
    val allergyTags: List<String> = emptyList()
)
```

---

### 7.2 UI Model

`feature` 내부에 위치합니다.

특징:

- 특정 화면에서 보여주기 위한 데이터
- drawable 리소스 포함 가능
- 선택 여부 포함 가능
- 화면 표시 텍스트 포함 가능

예시:

```kotlin
data class ProductUiModel(
    val id: String,
    val category: String,
    val name: String,
    val imageResId: Int,
    val allergyTags: List<String> = emptyList()
)
```

---

### 7.3 Mapper

domain model을 ui model로 변환하는 함수입니다.

예시:

```kotlin
fun Product.toUiModel(): ProductUiModel {
    return ProductUiModel(
        id = id,
        category = category,
        name = name,
        imageResId = R.drawable.ic_launcher_foreground,
        allergyTags = allergyTags
    )
}
```

사용 예시:

```kotlin
val productUiModels = products.map { product ->
    product.toUiModel()
}
```

---

## 8. Product 모델 사용 기준

상품 관련 모델은 다음 기준으로 사용합니다.

```txt
data / domain / repository
→ Product

feature / fragment / adapter / uiState
→ ProductUiModel
```

예시:

```txt
ProductRepositoryImpl
→ Product 반환

ChatViewModel
→ Product를 ProductUiModel로 변환

ChatUiState
→ ProductUiModel 저장

RecommendProductAdapter
→ ProductUiModel 표시
```

좋은 예:

```kotlin
val recommendProducts = chatRepository.getRecommendProducts(limit = 2)
    .map { product ->
        product.toUiModel()
    }
```

좋지 않은 예:

```kotlin
adapter.submitList(products) // products가 List<Product>인 경우
```

좋은 예:

```kotlin
adapter.submitList(
    products.map { product ->
        product.toUiModel()
    }
)
```

---

## 9. Home 프로필 모델 사용 기준

홈 화면의 프로필도 domain model과 ui model을 분리합니다.

```txt
domain/model/UserProfile.kt
→ 순수 프로필 데이터

feature/home/HomeProfileUiModel.kt
→ 홈 화면 표시용 데이터
```

예시:

```kotlin
data class UserProfile(
    val id: String,
    val name: String,
    val relation: ProfileRelation
)
```

```kotlin
data class HomeProfileUiModel(
    val id: String,
    val name: String,
    val relationText: String,
    val imageResId: Int,
    val isSelected: Boolean
)
```

Repository는 `UserProfile`을 반환하고, `HomeViewModel`에서 `HomeProfileUiModel`로 변환합니다.

```kotlin
private val initialProfiles =
    userProfileRepository.getProfiles()
        .mapIndexed { index, profile ->
            profile.toHomeProfileUiModel(
                isSelected = index == 0
            )
        }
```

---

## 10. 화면 이동 구조

현재는 Navigation Component를 아직 사용하지 않고, `FragmentManager`로 화면을 전환합니다.

예시:

```kotlin
parentFragmentManager.beginTransaction()
    .replace(R.id.mainContainer, ProductDetailFragment.newInstance(product.id))
    .addToBackStack(null)
    .commit()
```

현재 구조:

```txt
MainActivity
└── mainContainer
    ├── HomeFragment
    ├── ChatFragment
    ├── ProductDetailFragment
    └── ...
```

추후 화면 수가 많아지면 Navigation Component 도입을 고려합니다.

장기 목표:

```txt
MainActivity
└── NavHostFragment
    └── nav_graph.xml
```

---

## 11. BottomNavController 위치

`BottomNavController`는 하단 네비게이션 UI와 화면 이동을 처리합니다.

현재 위치:

```txt
feature/navigation/BottomNavController.kt
```

이유:

`BottomNavController`는 `HomeFragment`, `HistoryFragment` 등 특정 feature 화면을 알고 있습니다.

따라서 공통 모듈인 `core`에 두지 않고 `feature/navigation`에 둡니다.

---

## 12. Chat 추천 상품 BottomSheet 분리

채팅 화면의 추천 상품 BottomSheet는 `ChatFragment`에서 직접 생성하지 않고 별도 클래스로 분리합니다.

```txt
feature/chat/RecommendProductBottomSheet.kt
```

역할:

- BottomSheetDialog 생성
- 추천 상품 RecyclerView 연결
- 상품 클릭 이벤트 전달
- 더보기 클릭 이벤트 전달

`ChatFragment`는 BottomSheet의 내부 구현을 몰라도 됩니다.

```kotlin
RecommendProductBottomSheet(
    context = requireContext(),
    layoutInflater = layoutInflater,
    products = products,
    onProductClick = { product ->
        // 상품 상세 이동
    },
    onMoreClick = {
        // 상품 검색 화면 이동
    }
).show()
```

---

## 13. ViewBinding 사용 기준

이 프로젝트는 DataBinding을 사용하지 않고 ViewBinding을 사용합니다.

ViewBinding을 사용하는 이유:

- `findViewById`보다 타입 안전함
- DataBinding보다 학습 난이도가 낮음
- XML에 ViewModel을 직접 연결하지 않아도 됨
- MVVM 구조와 함께 사용하기 쉬움

DataBinding은 사용하지 않습니다.

사용하지 않는 방식:

```xml
@{viewModel.title}
```

현재 기준:

```txt
XML은 화면 구조만 담당
Fragment에서 binding으로 View 접근
ViewModel의 StateFlow를 observe하여 UI 갱신
```

---

## 14. StateFlow 사용 기준

ViewModel의 화면 상태는 `StateFlow`로 관리합니다.

예시:

```kotlin
private val _uiState = MutableStateFlow(ChatUiState())
val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()
```

Fragment에서는 `repeatOnLifecycle`로 수집합니다.

```kotlin
viewLifecycleOwner.lifecycleScope.launch {
    viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
        viewModel.uiState.collect { state ->
            // UI 업데이트
        }
    }
}
```

---

## 15. 새 기능을 만들 때 권장 흐름

예를 들어 `scanner` 기능을 만든다면 다음 순서로 구성합니다.

```txt
feature/scanner
├── ScannerFragment.kt
├── ScannerViewModel.kt
└── ScannerUiState.kt
```

데이터가 필요하면 domain과 data에 Repository를 추가합니다.

```txt
domain/repository/ScannerRepository.kt
data/repository/ScannerRepositoryImpl.kt
di/RepositoryModule.kt
```

흐름:

```txt
ScannerFragment
↓
ScannerViewModel
↓
ScannerRepository
↓
ScannerRepositoryImpl
↓
API / Local / DummyData
```

---

## 16. 코드 작성 규칙 요약

### Fragment

해야 하는 일:

- View 초기화
- 클릭 이벤트 연결
- uiState 관찰
- 화면 이동

하지 말아야 하는 일:

- Repository 직접 생성
- API 직접 호출
- 복잡한 데이터 가공
- 더미 데이터 직접 보유

---

### ViewModel

해야 하는 일:

- 화면 상태 관리
- Repository 호출
- domain model을 ui model로 변환
- 이벤트 처리

하지 말아야 하는 일:

- View 직접 참조
- FragmentManager 사용
- Activity/Fragment 직접 호출

---

### Repository

해야 하는 일:

- 데이터 출처 관리
- API/Local/DummyData 접근
- domain model 반환

하지 말아야 하는 일:

- Fragment import
- Adapter import
- UI model 반환

---

### Domain Model

해야 하는 일:

- 순수 데이터 표현

하지 말아야 하는 일:

- drawable 리소스 보유
- View 상태 보유
- Android Context 보유

---

### UI Model

해야 하는 일:

- 화면 표시용 데이터 표현
- drawable 리소스 포함 가능
- 선택 여부 포함 가능

---

## 17. 빌드 확인 명령어

수정 후에는 항상 다음 명령어로 빌드 확인을 합니다.

```bash
./gradlew :app:assembleDebug
```

필요 시 clean build를 실행합니다.

```bash
./gradlew --stop
./gradlew clean
./gradlew :app:assembleDebug
```

---

## 18. 커밋 전 체크리스트

커밋 전 아래 항목을 확인합니다.

```txt
- [ ] ./gradlew :app:assembleDebug 성공
- [ ] Fragment에서 RepositoryImpl을 직접 생성하지 않았는가?
- [ ] ViewModel에서 Repository interface를 주입받는가?
- [ ] domain model에 imageResId 같은 UI 리소스가 들어가지 않았는가?
- [ ] data 계층이 feature 계층을 import하지 않는가?
- [ ] Adapter에는 UI model을 전달하는가?
- [ ] 불필요한 import가 없는가?
- [ ] 임시 주석이나 테스트 코드가 남아 있지 않은가?
```

---

## 19. 현재 완료된 구조 정리

현재까지 정리된 주요 내용은 다음과 같습니다.

```txt
- Hilt 진입점 설정
- Repository DI 구조 적용
- FakeRepository 기반 구조 마련
- Product 도메인 모델과 UI 모델 분리
- Home 프로필 도메인 모델과 UI 모델 분리
- BottomNavController를 feature/navigation으로 이동
- MainActivity 시작 화면 처리 정리
- Chat 추천 상품 BottomSheet 로직 분리
```

---

## 20. 앞으로 개선하면 좋은 것

추후 개선 후보입니다.

```txt
- Navigation Component 도입
- Retrofit API 연동
- Room 또는 DataStore 역할 분리
- ViewBinding 전면 적용
- BaseFragment 도입 여부 검토
- 에러/로딩 공통 UiState 정리
- 이미지 리소스 실제 디자인 에셋으로 교체
- ProductUiMapper에서 상품별 이미지 매핑 개선
- 테스트 코드 추가
```
