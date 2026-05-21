package com.coworker.jjikmuk.feature.product.detail

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.coworker.jjikmuk.data.repository.FavoriteRepositoryImpl
import com.coworker.jjikmuk.data.repository.ProductRepositoryImpl
import com.coworker.jjikmuk.domain.repository.FavoriteRepository
import com.coworker.jjikmuk.domain.repository.ProductRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

class ProductDetailViewModel(
    application: Application
) : AndroidViewModel(application) {

    private val productRepository: ProductRepository = ProductRepositoryImpl()
    private val favoriteRepository: FavoriteRepository = FavoriteRepositoryImpl(
        context = application.applicationContext
    )

    private val _uiState = MutableStateFlow(ProductDetailUiState())
    val uiState: StateFlow<ProductDetailUiState> = _uiState

    private var currentProductId: String = ""

    fun loadProduct(productId: String) {
        currentProductId = productId

        _uiState.update { state ->
            state.copy(isLoading = true, errorMessage = null)
        }

        val product = productRepository.findProductById(productId)

        if (product == null) {
            _uiState.update { state ->
                state.copy(
                    product = null,
                    isFavorite = false,
                    isLoading = false,
                    errorMessage = "상품을 찾을 수 없습니다."
                )
            }
            return
        }

        viewModelScope.launch {
            val isFavorite = favoriteRepository.isFavorite(productId)

            _uiState.update { state ->
                state.copy(
                    product = product,
                    isFavorite = isFavorite,
                    isLoading = false,
                    errorMessage = null
                )
            }
        }
    }

    fun toggleFavorite() {
        val productId = currentProductId
        if (productId.isBlank()) return

        viewModelScope.launch {
            val isNowFavorite = favoriteRepository.toggleFavorite(productId)

            _uiState.update { state ->
                state.copy(isFavorite = isNowFavorite)
            }
        }
    }
}