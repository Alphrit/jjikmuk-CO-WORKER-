package com.coworker.jjikmuk.feature.product.detail

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.coworker.jjikmuk.domain.model.Product
import com.coworker.jjikmuk.domain.repository.FavoriteRepository
import com.coworker.jjikmuk.domain.repository.MealContextRepository
import com.coworker.jjikmuk.domain.repository.ProductRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

@HiltViewModel
class ProductDetailViewModel @Inject constructor(
    private val productRepository: ProductRepository,
    private val favoriteRepository: FavoriteRepository,
    private val mealContextRepository: MealContextRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(ProductDetailUiState())
    val uiState: StateFlow<ProductDetailUiState> = _uiState.asStateFlow()

    private var currentProductId: String = ""

    fun loadProduct(productId: String) {
        currentProductId = productId

        _uiState.update { state ->
            state.copy(isLoading = true, errorMessage = null)
        }

        viewModelScope.launch {
            val product = productRepository.findProductDetailByBarcode(productId)

            if (product == null) {
                _uiState.update { state ->
                    state.copy(
                        product = null,
                        isFavorite = false,
                        isLoading = false,
                        errorMessage = "상품을 찾을 수 없습니다."
                    )
                }
                return@launch
            }

            val isFavorite = favoriteRepository.isFavorite(productId)

            _uiState.update { state ->
                state.copy(
                    product = product.withMatchedAllergies(),
                    isFavorite = isFavorite,
                    isLoading = false,
                    errorMessage = null
                )
            }
        }
    }

    fun setSafetyResult(answer: String?, riskLevel: String?) {
        _uiState.update { state ->
            state.copy(
                safetyAnswer = answer?.takeIf { it.isNotBlank() },
                riskLevel = riskLevel?.takeIf { it.isNotBlank() }
            )
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

    private fun Product.withMatchedAllergies(): Product {
        if (matchedAllergyTags.isNotEmpty()) return this

        val userAllergies = mealContextRepository.mealContext.value.allergyNames
        if (userAllergies.isEmpty()) return this

        val productAllergyText = (allergyTags + rawMaterials.orEmpty())
            .joinToString(" ")
            .lowercase()

        val matchedAllergies = userAllergies.filter { allergy ->
            productAllergyText.contains(allergy.lowercase())
        }.distinct()

        return copy(matchedAllergyTags = matchedAllergies)
    }
}
