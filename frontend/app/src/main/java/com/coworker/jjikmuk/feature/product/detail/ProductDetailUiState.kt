package com.coworker.jjikmuk.feature.product.detail

import com.coworker.jjikmuk.domain.model.Product

data class ProductDetailUiState(
    val product: Product? = null,
    val isFavorite: Boolean = false,
    val isLoading: Boolean = false,
    val errorMessage: String? = null
)