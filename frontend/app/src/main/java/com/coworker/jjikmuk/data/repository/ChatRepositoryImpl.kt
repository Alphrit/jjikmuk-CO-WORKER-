package com.coworker.jjikmuk.data.repository

import com.coworker.jjikmuk.domain.model.Product
import com.coworker.jjikmuk.domain.repository.ChatRepository
import com.coworker.jjikmuk.domain.repository.ProductRepository
import javax.inject.Inject

class ChatRepositoryImpl @Inject constructor(
    private val productRepository: ProductRepository
) : ChatRepository {

    override fun makeDummyResponse(userMessage: String): String {
        return "'$userMessage'에 대해 확인해볼게요. 입력한 음식명이나 제품명을 기준으로 성분, 알레르기 가능성, 섭취 시 주의할 점을 안내할 수 있습니다.\n\n실제 서비스에서는 이 부분에 챗봇 API 응답을 연결하면 됩니다."
    }

    override fun getRecommendProducts(limit: Int): List<Product> {
        return productRepository.getRecommendProducts(limit)
    }
}