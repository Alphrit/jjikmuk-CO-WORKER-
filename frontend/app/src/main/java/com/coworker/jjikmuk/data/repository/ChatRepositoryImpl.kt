package com.coworker.jjikmuk.data.repository

import com.coworker.jjikmuk.data.remote.api.ChatApi
import com.coworker.jjikmuk.data.remote.dto.ChatApiProfile
import com.coworker.jjikmuk.data.remote.dto.ChatApiProduct
import com.coworker.jjikmuk.data.remote.dto.ChatApiRecommendedProduct
import com.coworker.jjikmuk.data.remote.dto.ChatApiRequest
import com.coworker.jjikmuk.domain.model.ChatProductCandidate
import com.coworker.jjikmuk.domain.model.ChatResponse
import com.coworker.jjikmuk.domain.model.MealContext
import com.coworker.jjikmuk.domain.model.Product
import com.coworker.jjikmuk.domain.repository.ChatRepository
import com.coworker.jjikmuk.domain.repository.ProductRepository
import com.google.gson.JsonElement
import javax.inject.Inject

class ChatRepositoryImpl @Inject constructor(
    private val productRepository: ProductRepository,
    private val chatApi: ChatApi
) : ChatRepository {

    override suspend fun sendMessage(
        userMessage: String,
        mealContext: MealContext,
        selectedProduct: ChatProductCandidate?
    ): ChatResponse {
        return runCatching {
            val request = ChatApiRequest(
                barcode = selectedProduct?.barcode?.takeIf { barcode -> barcode.isNotBlank() },
                profiles = mealContext.toChatApiProfiles(),
                product = selectedProduct?.toChatApiProduct(),
                message = userMessage,
                chat_history = emptyList()
            )

            val response = chatApi.sendMessage(request)

            ChatResponse(
                answer = response.answer,
                riskLevel = response.risk_level,
                currentProductBarcode = response.current_product?.barcode,
                productCandidates = response.recommended_products
                    .filter { product ->
                        product.selection_type == PRODUCT_CANDIDATE_SELECTION_TYPE
                    }
                    .map { product -> product.toChatProductCandidate() }
            )
        }.getOrElse {
            ChatResponse(
                answer = makeFallbackResponse(userMessage, mealContext)
            )
        }
    }

    override fun getRecommendProducts(limit: Int): List<Product> {
        return productRepository.getRecommendProducts(limit)
    }

    private fun MealContext.toChatApiProfiles(): List<ChatApiProfile> {
        return selectedProfiles.mapIndexed { index, profile ->
            ChatApiProfile(
                id = index + 1L,
                nickname = profile.name,
                allergies = profile.allergies.joinToString(",")
            )
        }
    }

    private fun ChatProductCandidate.toChatApiProduct(): ChatApiProduct {
        return ChatApiProduct(
            productName = productName,
            barcode = barcode,
            allergy = allergy,
            rawMaterials = rawMaterials
        )
    }

    private fun ChatApiRecommendedProduct.toChatProductCandidate(): ChatProductCandidate {
        return ChatProductCandidate(
            productName = product_name.orEmpty(),
            barcode = barcode.orEmpty(),
            category = prdlst_dcnm.orEmpty(),
            rawMaterials = rawmtrl_nm.orEmpty(),
            allergy = allergy.toStringList(),
            allergyInfo = allergy_info.toDisplayText(),
            nutrition = nutrition.toDisplayText()
        )
    }

    private fun JsonElement?.toStringList(): List<String> {
        if (this == null || isJsonNull) return emptyList()

        return when {
            isJsonArray -> asJsonArray.mapNotNull { element ->
                element.toDisplayText()?.takeIf { text -> text.isNotBlank() }
            }

            else -> toDisplayText()
                ?.split(",")
                ?.map { value -> value.trim() }
                ?.filter { value -> value.isNotBlank() }
                .orEmpty()
        }
    }

    private fun JsonElement?.toDisplayText(): String? {
        if (this == null || isJsonNull) return null

        return when {
            isJsonPrimitive -> asJsonPrimitive.asString
            else -> toString()
        }
    }

    private fun makeFallbackResponse(
        userMessage: String,
        mealContext: MealContext
    ): String {
        val participantText = mealContext.selectedProfiles
            .joinToString(", ") { profile -> profile.name }
            .ifBlank { "선택된 식사 참여자 없음" }

        val allergyText = mealContext.allergyNames
            .joinToString(", ")
            .ifBlank { "등록된 알레르기 없음" }

        return "'$userMessage'에 대해 확인해볼게요. 현재 같이 식사하는 사람은 $participantText 이고, 주의해야 할 알레르기는 $allergyText 입니다.\n\n현재 AI 서버 연결에 실패해서 임시 안내로 보여드리고 있어요."
    }

    companion object {
        private const val PRODUCT_CANDIDATE_SELECTION_TYPE = "product_candidate"
    }
}
