package com.coworker.jjikmuk.feature.chat

import androidx.lifecycle.ViewModel
import com.coworker.jjikmuk.domain.model.ChatMessage
import com.coworker.jjikmuk.domain.repository.ChatRepository
import com.coworker.jjikmuk.feature.product.mapper.toUiModel
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

@HiltViewModel
class ChatViewModel @Inject constructor(
    private val chatRepository: ChatRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    private var nextMessageId: Long = 0L

    fun startChat(initialMessage: String) {
        if (initialMessage.isBlank()) return

        val userMessage = createUserMessage(initialMessage)
        val botMessage = createBotMessage(chatRepository.makeDummyResponse(initialMessage))
        val recommendProducts = getRecommendProductUiModels()

        _uiState.update { state ->
            state.copy(
                title = makeTitle(initialMessage),
                messages = listOf(userMessage, botMessage),
                recommendedProducts = recommendProducts,
                shouldShowRecommendSheet = true,
                isLoading = false,
                errorMessage = null
            )
        }
    }

    fun sendMessage(message: String) {
        val trimmedMessage = message.trim()
        if (trimmedMessage.isBlank()) return

        val userMessage = createUserMessage(trimmedMessage)
        val botMessage = createBotMessage(chatRepository.makeDummyResponse(trimmedMessage))
        val recommendProducts = getRecommendProductUiModels()

        _uiState.update { state ->
            state.copy(
                messages = state.messages + userMessage + botMessage,
                recommendedProducts = recommendProducts,
                shouldShowRecommendSheet = true,
                isLoading = false,
                errorMessage = null
            )
        }
    }

    fun onRecommendSheetShown() {
        _uiState.update { state ->
            state.copy(shouldShowRecommendSheet = false)
        }
    }

    private fun getRecommendProductUiModels() =
        chatRepository.getRecommendProducts(limit = 2)
            .map { product ->
                product.toUiModel()
            }

    private fun createUserMessage(message: String): ChatMessage {
        return ChatMessage(
            id = nextMessageId++,
            text = message,
            senderType = ChatMessage.SenderType.USER
        )
    }

    private fun createBotMessage(message: String): ChatMessage {
        return ChatMessage(
            id = nextMessageId++,
            text = message,
            senderType = ChatMessage.SenderType.BOT
        )
    }

    private fun makeTitle(message: String): String {
        return if (message.length > 12) {
            message.take(12) + ".."
        } else {
            message
        }
    }
}