package com.coworker.jjikmuk.data.local.preference

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringSetPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.coworker.jjikmuk.domain.model.Product
import com.coworker.jjikmuk.data.local.dummy.ProductDummyData
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.favoriteProductDataStore by preferencesDataStore(
    name = "favorite_product_preferences"
)

object FavoriteProductStore {

    private val FAVORITE_PRODUCT_IDS = stringSetPreferencesKey("favorite_product_ids")

    fun getFavoriteProductIds(context: Context): Flow<Set<String>> {
        return context.favoriteProductDataStore.data.map { preferences ->
            preferences[FAVORITE_PRODUCT_IDS] ?: emptySet()
        }
    }

    suspend fun isFavorite(context: Context, productId: String): Boolean {
        val favoriteIds = getFavoriteProductIds(context).first()
        return favoriteIds.contains(productId)
    }

    suspend fun toggleFavorite(context: Context, productId: String): Boolean {
        var isNowFavorite = false

        context.favoriteProductDataStore.edit { preferences ->
            val currentFavoriteIds = preferences[FAVORITE_PRODUCT_IDS] ?: emptySet()

            val updatedFavoriteIds = if (currentFavoriteIds.contains(productId)) {
                isNowFavorite = false
                currentFavoriteIds - productId
            } else {
                isNowFavorite = true
                currentFavoriteIds + productId
            }

            preferences[FAVORITE_PRODUCT_IDS] = updatedFavoriteIds
        }

        return isNowFavorite
    }

    suspend fun getFavoriteProducts(context: Context): List<Product> {
        val favoriteIds = getFavoriteProductIds(context).first()

        return ProductDummyData.recommendProducts.filter { product ->
            favoriteIds.contains(product.id)
        }
    }
}