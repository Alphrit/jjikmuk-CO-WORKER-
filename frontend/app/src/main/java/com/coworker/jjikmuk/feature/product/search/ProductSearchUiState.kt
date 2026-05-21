package com.coworker.jjikmuk.feature.product.search

import com.coworker.jjikmuk.domain.model.Product

data class ProductSearchUiState(
    val products: List<Product> = emptyList(),
    val isLoading: Boolean = false,
    val errorMessage: String? = null
)