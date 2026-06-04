package org.jjikmuk.backend.domain.chat

import org.jjikmuk.backend.domain.product.ProductRepository
import org.jjikmuk.backend.domain.user.UserRepository
import org.springframework.stereotype.Service
import org.springframework.web.client.RestClient
import org.jjikmuk.backend.domain.chat.dto.ChatRequestDto
import org.jjikmuk.backend.domain.chat.dto.ProfileDto
import org.jjikmuk.backend.domain.chat.dto.ProductDto
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration

@Configuration
class RestClientConfig {
    @Bean
    fun restClient(): RestClient {
        return RestClient.create()
    }
}

@Service
class ChatService(
    private val userRepository: UserRepository,
    private val productRepository: ProductRepository,
    private val restClient: RestClient
) {
    fun askToAiChatbot(request: ChatRequestDto): String {
        // 1. DB에서 유저와 제품 정보 조회
        val user = request.userId?.let { userRepository.findById(it).orElse(null) }
        val product = request.barcode?.let { productRepository.findFirstByBarcode(it) }

        // 2. AI 서버에 보낼 JSON 모양으로 조립
        val profileDto = user?.let { ProfileDto(it.id, it.nickname, it.allergies, it.diseases, it.specialDiet, it.dislikedIngredients) }
        val profiles = request.profiles.ifEmpty {
            profileDto?.let { listOf(it) }.orEmpty()
        }
        val productDto = product?.let { ProductDto(it.reportNo, it.barcode, it.productName, it.allergy, it.rawMaterials, it.energyKcal?.toString(), it.sodiumMg?.toString()) }

        val aiRequest = mutableMapOf<String, Any>(
            "message" to request.message,
            "chat_history" to request.chatHistory
        )
        profileDto?.let { aiRequest["profile"] = it }
        if (profiles.isNotEmpty()) {
            aiRequest["profiles"] = profiles
        }
        productDto?.let { aiRequest["product"] = it }

        // 3. AI 서버(8000 포트)로 POST 요청 발사!
        val aiResponse = restClient.post()
            .uri("http://localhost:8000/chat")
            .header("Content-Type", "application/json")
            .body(aiRequest)
            .retrieve()
            .body(String::class.java)

        // 4. 프론트엔드로 응답 전달
        return aiResponse ?: throw RuntimeException("AI 서버 응답이 없습니다.")
    }
}
