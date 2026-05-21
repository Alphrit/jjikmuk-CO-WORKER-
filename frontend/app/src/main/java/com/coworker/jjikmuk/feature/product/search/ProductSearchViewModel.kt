package com.coworker.jjikmuk.feature.product.search

import androidx.lifecycle.ViewModel
import com.coworker.jjikmuk.data.repository.ProductRepositoryImpl
import com.coworker.jjikmuk.domain.repository.ProductRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

class ProductSearchViewModel : ViewModel() {

    private val productRepository: ProductRepository = ProductRepositoryImpl()

    private val _uiState = MutableStateFlow(ProductSearchUiState())
    val uiState: StateFlow<ProductSearchUiState> = _uiState

    fun loadProducts() {
        _uiState.update { state ->
            state.copy(isLoading = true, errorMessage = null)
        }

        val products = productRepository.getAllProducts()

        _uiState.update { state ->
            state.copy(
                products = products,
                isLoading = false,
                errorMessage = null
            )
        }
    }
}