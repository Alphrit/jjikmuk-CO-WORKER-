package com.coworker.jjikmuk.domain.repository

import com.coworker.jjikmuk.core.common.ApiResult
import com.coworker.jjikmuk.domain.model.Product
import com.coworker.jjikmuk.domain.model.ProductScanResult

interface ProductRepository {
    fun getAllProducts(): List<Product>

    fun getRecommendProducts(): List<Product>

    fun getRecommendProducts(limit: Int): List<Product>

    fun findProductById(productId: String): Product?

    suspend fun scanProduct(barcode: String, userId: Long? = null): ApiResult<ProductScanResult>
}
