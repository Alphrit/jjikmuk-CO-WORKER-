package com.coworker.jjikmuk.data.local.dummy

import com.coworker.jjikmuk.domain.model.Product

/**
 * 상품 관련 화면에서 임시로 사용하는 더미데이터입니다.
 *
 * 현재 데모는 API 연결 없이 새우깡 상품을 보여주기 위해 고정 데이터를 사용합니다.
 */
object ProductDummyData {
    const val DEMO_SHRIMP_CRACKER_ID = "nongshim_shrimp_cracker"
    const val DEMO_SHRIMP_CRACKER_BARCODE = "8801043014830"
    const val DEMO_SAFETY_ANSWER =
        "활성화된 프로필 기준 요약: 피하는 편이 좋아요/ 코워커: 피하는 편이 좋아요 [나]'나' 기준으로 보면 농심 새우깡 90g은 피하는 편이 더 안전해 보여요. 우유 관련 성분(우유)이 확인됐어요. 알레르기 반응 이력이 있으면 소량도 주의하는 편이 안전해요. [코워커]'코워커' 기준으로 보면 농심 새우깡 90g은 피하는 편이 더 안전해 보여요. 새우 관련 성분(새우)이 확인됐어요. 알레르기 반응 이력이 있으면 소량도 주의하는 편이 안전해요."

    val recommendProducts: List<Product> = listOf(
        Product(
            id = DEMO_SHRIMP_CRACKER_ID,
            category = "제조사 정보 없음",
            name = "새우깡",
            allergyTags = listOf("우유", "새우"),
            matchedAllergyTags = listOf("우유", "새우"),
            rawMaterials = "곡류 가공품, 미강유, 팜유, 향미유, 기타가공품",
            caloriesKcal = 160.0,
            proteinG = 2.0,
            fatG = 8.0,
            carbohydrateG = 20.0,
            energyPercent = 8,
            proteinPercent = 4,
            fatPercent = 15,
            carbohydratePercent = 6,
            proteinMacroPercent = 5.0,
            fatMacroPercent = 45.0,
            carbohydrateMacroPercent = 50.0
        ),
        Product(
            id = "pocky_blueberry",
            category = "해태제과",
            name = "해태 포키 블루베리",
            allergyTags = listOf("우유", "땅콩")
        ),
        Product(
            id = "pocky_green_tea",
            category = "해태제과",
            name = "해태 포키 녹차",
            allergyTags = listOf("우유", "땅콩")
        ),
        Product(
            id = "pocky_melon",
            category = "해태제과",
            name = "해태 포키 멜론",
            allergyTags = listOf("우유")
        ),
        Product(
            id = "pocky_original",
            category = "해태제과",
            name = "해태 포키 오리지널",
            allergyTags = listOf("우유", "밀")
        )
    )

    val demoShrimpCracker: Product
        get() = requireNotNull(findProductById(DEMO_SHRIMP_CRACKER_ID))

    fun findProductById(productId: String): Product? {
        return recommendProducts.firstOrNull { product ->
            product.id == productId
        }
    }
}
