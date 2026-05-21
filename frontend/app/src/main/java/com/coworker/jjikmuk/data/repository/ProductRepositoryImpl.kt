package com.coworker.jjikmuk.data.repository

import com.coworker.jjikmuk.data.local.dummy.ProductDummyData
import com.coworker.jjikmuk.domain.model.Product
import com.coworker.jjikmuk.domain.repository.ProductRepository
import javax.inject.Inject

class ProductRepositoryImpl @Inject constructor() : ProductRepository {

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
}
