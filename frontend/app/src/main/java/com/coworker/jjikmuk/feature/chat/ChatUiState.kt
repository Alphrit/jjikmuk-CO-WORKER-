package com.coworker.jjikmuk.feature.chat

import com.coworker.jjikmuk.domain.model.ChatMessage
import com.coworker.jjikmuk.feature.product.model.ProductUiModel

data class ChatUiState(
    val title: String = "",
    val messages: List<ChatMessage> = emptyList(),
    val recommendedProducts: List<ProductUiModel> = emptyList(),
    val shouldShowRecommendSheet: Boolean = false,
    val isLoading: Boolean = false,
    val errorMessage: String? = null
)