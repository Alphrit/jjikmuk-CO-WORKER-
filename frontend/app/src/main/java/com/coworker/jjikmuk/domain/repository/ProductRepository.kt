package com.coworker.jjikmuk.domain.repository

import com.coworker.jjikmuk.domain.model.Product

interface ProductRepository {
    fun getAllProducts(): List<Product>

    fun getRecommendProducts(): List<Product>

    fun getRecommendProducts(limit: Int): List<Product>

    fun findProductById(productId: String): Product?
}
