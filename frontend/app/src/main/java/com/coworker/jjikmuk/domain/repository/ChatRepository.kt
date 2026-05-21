package com.coworker.jjikmuk.domain.repository

import com.coworker.jjikmuk.domain.model.Product

interface ChatRepository {
    fun makeDummyResponse(userMessage: String): String

    fun getRecommendProducts(limit: Int): List<Product>
}