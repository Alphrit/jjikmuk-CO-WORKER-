package com.coworker.jjikmuk.data.local.dummy

import com.coworker.jjikmuk.domain.model.Product

/**
 * 상품 관련 화면에서 임시로 사용하는 더미데이터입니다.
 *
 * 현재는 API 연결 전 단계이므로 drawable 리소스와 고정 문자열을 사용합니다.
 * 나중에 서버 API가 연결되면 이 파일 대신 Repository / UseCase에서 상품 목록을 받아오면 됩니다.
 */
object ProductDummyData {

    val recommendProducts: List<Product> = listOf(
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

    fun findProductById(productId: String): Product? {
        return recommendProducts.firstOrNull { product ->
            product.id == productId
        }
    }
}