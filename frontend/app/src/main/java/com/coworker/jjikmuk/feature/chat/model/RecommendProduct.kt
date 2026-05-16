package com.coworker.jjikmuk.feature.chat.model

/**
 * 채팅 화면의 추천 상품 바텀시트에서 사용하는 UI 모델입니다.
 *
 * 현재는 imageResId로 drawable 리소스를 직접 참조하므로
 * domain/model이 아니라 feature/chat/model에 두는 것이 적절합니다.
 *
 * 나중에 서버 API와 연결하면 domain/model/Product.kt와
 * data/remote/dto/product 쪽 모델로 분리할 수 있습니다.
 */
data class RecommendProduct(
    val id: String,
    val category: String,
    val name: String,
    val imageResId: Int,
    val allergyTags: List<String>
)