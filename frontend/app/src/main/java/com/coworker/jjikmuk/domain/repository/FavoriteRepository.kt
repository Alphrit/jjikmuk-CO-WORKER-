package com.coworker.jjikmuk.domain.repository

import com.coworker.jjikmuk.domain.model.Product

interface FavoriteRepository {
    suspend fun isFavorite(productId: String): Boolean

    suspend fun toggleFavorite(productId: String): Boolean

    suspend fun getFavoriteProducts(): List<Product>
}