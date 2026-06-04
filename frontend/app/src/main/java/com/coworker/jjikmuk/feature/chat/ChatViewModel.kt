package com.coworker.jjikmuk.feature.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.coworker.jjikmuk.domain.model.ChatMessage
import com.coworker.jjikmuk.domain.model.ChatProductCandidate
import com.coworker.jjikmuk.domain.model.ChatResponse
import com.coworker.jjikmuk.domain.model.UploadOption
import com.coworker.jjikmuk.domain.repository.ChatHistoryRepository
import com.coworker.jjikmuk.domain.repository.ChatRepository
import com.coworker.jjikmuk.domain.repository.MealContextRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

@HiltViewModel
class ChatViewModel @Inject constructor(
    private val chatRepository: ChatRepository,
    private val chatHistoryRepository: ChatHistoryRepository,
    private val mealContextRepository: MealContextRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    private val _uploadOptionEvent = MutableSharedFlow<UploadOption>()
    val uploadOptionEvent: SharedFlow<UploadOption> = _uploadOptionEvent

    private val _productDetailEvent = MutableSharedFlow<ProductDetailEvent>()
    val productDetailEvent: SharedFlow<ProductDetailEvent> = _productDetailEvent

    private var nextMessageId: Long = 0L
    private var currentChatHistoryId: Long? = null
    private var currentChatHistoryTitle: String = ""

    fun startChat(initialMessage: String) {
        val trimmedMessage = initialMessage.trim()
        if (trimmedMessage.isBlank()) return

        val mealContext = mealContextRepository.mealContext.value
        val userMessage = createUserMessage(trimmedMessage)
        currentChatHistoryTitle = makeHistoryTitle(trimmedMessage)
        saveChatHistory(
            subtitle = trimmedMessage,
            messages = listOf(userMessage)
        )

        _uiState.update { state ->
            state.copy(
                title = makeTitle(trimmedMessage),
                messages = listOf(userMessage),
                productCandidates = emptyList(),
                shouldShowRecommendSheet = false,
                isLoading = true,
                errorMessage = null
            )
        }

        viewModelScope.launch {
            val chatResponse = chatRepository.sendMessage(trimmedMessage, mealContext)
            val botMessage = createBotMessage(chatResponse.answer)
            saveChatHistory(
                subtitle = chatResponse.answer,
                messages = _uiState.value.messages + botMessage
            )

            _uiState.update { state ->
                state.copy(
                    messages = state.messages + botMessage,
                    productCandidates = chatResponse.productCandidates,
                    shouldShowRecommendSheet = chatResponse.productCandidates.isNotEmpty(),
                    isLoading = false,
                    errorMessage = null
                )
            }
            emitProductDetailIfResolved(chatResponse)
        }
    }

    fun sendMessage(message: String) {
        val trimmedMessage = message.trim()
        if (trimmedMessage.isBlank()) return

        val mealContext = mealContextRepository.mealContext.value
        val userMessage = createUserMessage(trimmedMessage)
        saveChatHistory(
            titleSource = trimmedMessage,
            subtitle = trimmedMessage,
            messages = _uiState.value.messages + userMessage
        )

        _uiState.update { state ->
            state.copy(
                messages = state.messages + userMessage,
                productCandidates = emptyList(),
                shouldShowRecommendSheet = false,
                isLoading = true,
                errorMessage = null
            )
        }

        viewModelScope.launch {
            val chatResponse = chatRepository.sendMessage(trimmedMessage, mealContext)
            val botMessage = createBotMessage(chatResponse.answer)
            saveChatHistory(
                subtitle = chatResponse.answer,
                messages = _uiState.value.messages + botMessage
            )

            _uiState.update { state ->
                state.copy(
                    messages = state.messages + botMessage,
                    productCandidates = chatResponse.productCandidates,
                    shouldShowRecommendSheet = chatResponse.productCandidates.isNotEmpty(),
                    isLoading = false,
                    errorMessage = null
                )
            }
            emitProductDetailIfResolved(chatResponse)
        }
    }

    fun selectProductCandidate(productCandidate: ChatProductCandidate) {
        val mealContext = mealContextRepository.mealContext.value
        val selectedProductName = productCandidate.productName.ifBlank {
            productCandidate.barcode.ifBlank { "선택한 상품" }
        }
        val selectedProductMessage = createUserMessage(
            "$selectedProductName 먹어도 괜찮아?"
        )
        saveChatHistory(
            subtitle = selectedProductMessage.text,
            messages = _uiState.value.messages + selectedProductMessage
        )

        _uiState.update { state ->
            state.copy(
                messages = state.messages + selectedProductMessage,
                productCandidates = emptyList(),
                shouldShowRecommendSheet = false,
                isLoading = true,
                errorMessage = null
            )
        }

        viewModelScope.launch {
            val chatResponse = chatRepository.sendMessage(
                userMessage = selectedProductMessage.text,
                mealContext = mealContext,
                selectedProduct = productCandidate
            )
            val botMessage = createBotMessage(chatResponse.answer)
            saveChatHistory(
                subtitle = chatResponse.answer,
                messages = _uiState.value.messages + botMessage
            )

            _uiState.update { state ->
                state.copy(
                    messages = state.messages + botMessage,
                    productCandidates = chatResponse.productCandidates,
                    shouldShowRecommendSheet = chatResponse.productCandidates.isNotEmpty(),
                    isLoading = false,
                    errorMessage = null
                )
            }

            emitProductDetailIfResolved(
                chatResponse = chatResponse,
                fallbackBarcode = productCandidate.barcode
            )
        }
    }

    private suspend fun emitProductDetailIfResolved(
        chatResponse: ChatResponse,
        fallbackBarcode: String? = null
    ) {
        if (chatResponse.productCandidates.isNotEmpty()) return

        val barcode = chatResponse.currentProductBarcode
            ?.takeIf { it.isNotBlank() }
            ?: fallbackBarcode?.takeIf { it.isNotBlank() }
            ?: return

        _productDetailEvent.emit(
            ProductDetailEvent(
                barcode = barcode,
                answer = chatResponse.answer,
                riskLevel = chatResponse.riskLevel
            )
        )
    }

    fun loadChatHistory(historyId: Long) {
        val history = chatHistoryRepository.getChatHistory(historyId) ?: return

        currentChatHistoryId = history.id
        currentChatHistoryTitle = history.title
        nextMessageId = history.messages.maxOfOrNull { message -> message.id + 1 } ?: 0L

        _uiState.update { state ->
            state.copy(
                title = history.title,
                messages = history.messages,
                productCandidates = emptyList(),
                shouldShowRecommendSheet = false,
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

    fun onUploadOptionSelected(option: UploadOption) {
        viewModelScope.launch {
            _uploadOptionEvent.emit(option)
        }
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

    private fun makeHistoryTitle(message: String): String {
        return message.trim()
            .replace(Regex("\\s+"), " ")
            .ifBlank { "새 채팅" }
            .let { title ->
                if (title.length > HISTORY_TITLE_MAX_LENGTH) {
                    title.take(HISTORY_TITLE_MAX_LENGTH) + ".."
                } else {
                    title
                }
            }
    }

    private fun makeHistorySubtitle(message: String): String {
        return message.trim()
            .replace(Regex("\\s+"), " ")
            .ifBlank { "대화를 시작했어요." }
            .let { subtitle ->
                if (subtitle.length > HISTORY_SUBTITLE_MAX_LENGTH) {
                    subtitle.take(HISTORY_SUBTITLE_MAX_LENGTH) + ".."
                } else {
                    subtitle
                }
            }
    }

    private fun saveChatHistory(
        titleSource: String? = null,
        subtitle: String,
        messages: List<ChatMessage>
    ) {
        if (currentChatHistoryTitle.isBlank()) {
            currentChatHistoryTitle = makeHistoryTitle(titleSource.orEmpty())
        }

        val savedHistory = chatHistoryRepository.upsertChatHistory(
            id = currentChatHistoryId,
            title = currentChatHistoryTitle,
            subtitle = makeHistorySubtitle(subtitle),
            lastMessageTime = makeCurrentTimeText(),
            messages = messages
        )
        currentChatHistoryId = savedHistory.id
    }

    private fun makeCurrentTimeText(): String {
        return SimpleDateFormat("h:mma", Locale.US)
            .format(Date())
            .lowercase(Locale.US)
    }

    companion object {
        private const val HISTORY_TITLE_MAX_LENGTH = 24
        private const val HISTORY_SUBTITLE_MAX_LENGTH = 48
    }
}

data class ProductDetailEvent(
    val barcode: String,
    val answer: String,
    val riskLevel: String?
)
