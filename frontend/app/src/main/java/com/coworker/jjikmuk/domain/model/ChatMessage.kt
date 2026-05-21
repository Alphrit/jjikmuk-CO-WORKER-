package com.coworker.jjikmuk.domain.model

data class ChatMessage(
    val id: Long,
    val text: String,
    val senderType: SenderType
) {
    enum class SenderType {
        USER,
        BOT
    }
}