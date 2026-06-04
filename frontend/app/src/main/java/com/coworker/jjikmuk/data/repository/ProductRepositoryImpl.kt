package com.coworker.jjikmuk.data.repository

import com.coworker.jjikmuk.data.local.dummy.ProductDummyData
import com.coworker.jjikmuk.data.remote.api.ProductApi
import com.coworker.jjikmuk.data.remote.dto.toDomainProduct
import com.coworker.jjikmuk.domain.model.Product
import com.coworker.jjikmuk.domain.repository.ProductRepository
import javax.inject.Inject

class ProductRepositoryImpl @Inject constructor(
    private val productApi: ProductApi
) : ProductRepository {

    override fun getAllProducts(): List<Product> {
        return ProductDummyData.recommendProducts
    }

    override fun getRecommendProducts(): List<Product> {
        return getAllProducts()
    }

    override fun getRecommendProducts(limit: Int): List<Product> {
        return getAllProducts().take(limit)
    }

    override fun findProductById(productId: String): Product? {
        return ProductDummyData.findProductById(productId)
    }

    override suspend fun findProductDetailByBarcode(barcode: String): Product? {
        return runCatching {
            productApi.getProductByBarcode(barcode).data?.toDomainProduct()
        }.getOrNull() ?: findProductById(barcode)
    }
}
