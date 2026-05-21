package com.coworker.jjikmuk.feature.history

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.coworker.jjikmuk.data.repository.FavoriteRepositoryImpl
import com.coworker.jjikmuk.domain.repository.FavoriteRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

class HistoryViewModel(
    application: Application
) : AndroidViewModel(application) {

    private val favoriteRepository: FavoriteRepository = FavoriteRepositoryImpl(
        context = application.applicationContext
    )

    private val _uiState = MutableStateFlow(HistoryUiState())
    val uiState: StateFlow<HistoryUiState> = _uiState

    fun loadFavoriteProducts() {
        viewModelScope.launch {
            _uiState.update { state ->
                state.copy(isLoading = true, errorMessage = null)
            }

            val favoriteProducts = favoriteRepository.getFavoriteProducts()

            _uiState.update { state ->
                state.copy(
                    favoriteProducts = favoriteProducts,
                    isLoading = false,
                    errorMessage = null
                )
            }
        }
    }
}