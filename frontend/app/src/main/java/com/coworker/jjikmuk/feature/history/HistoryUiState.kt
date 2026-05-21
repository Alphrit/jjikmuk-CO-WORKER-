package com.coworker.jjikmuk.feature.history

import com.coworker.jjikmuk.domain.model.Product

data class HistoryUiState(
    val favoriteProducts: List<Product> = emptyList(),
    val isLoading: Boolean = false,
    val errorMessage: String? = null
)