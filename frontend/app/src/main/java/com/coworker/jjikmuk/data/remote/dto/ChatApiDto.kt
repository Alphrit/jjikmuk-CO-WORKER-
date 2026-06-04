package com.coworker.jjikmuk.data.remote.dto

import com.google.gson.JsonElement

data class ChatApiRequest(
    val barcode: String? = null,
    val profiles: List<ChatApiProfile> = emptyList(),
    val product: ChatApiProduct? = null,
    val message: String,
    val chat_history: List<ChatApiHistoryMessage> = emptyList()
)

data class ChatApiProfile(
    val id: Long?,
    val nickname: String,
    val allergies: String?,
    val diseases: String? = null,
    val specialDiet: String? = null,
    val dislikedIngredients: String? = null
)

data class ChatApiHistoryMessage(
    val role: String,
    val content: String
)

data class ChatApiProduct(
    val productName: String,
    val barcode: String,
    val allergy: List<String>,
    val rawMaterials: String
)

data class ChatApiResponse(
    val task_type: String? = null,
    val answer_source: String? = null,
    val pipeline_stage: String? = null,
    val intent: String? = null,
    val answer: String,
    val risk_level: String? = null,
    val reasons: List<String> = emptyList(),
    val profile_name: String? = null,
    val active_profile_names: List<String> = emptyList(),
    val recommended_questions: List<String> = emptyList(),
    val current_product: ChatApiCurrentProduct? = null,
    val recommended_products: List<ChatApiRecommendedProduct> = emptyList()
)

data class ChatApiCurrentProduct(
    val barcode: String? = null,
    val product_name: String? = null,
    val productName: String? = null
)

data class ChatApiRecommendedProduct(
    val product_name: String? = null,
    val barcode: String? = null,
    val prdlst_dcnm: String? = null,
    val rawmtrl_nm: String? = null,
    val allergy: JsonElement? = null,
    val allergy_info: JsonElement? = null,
    val nutrition: JsonElement? = null,
    val selection_type: String? = null,
    val reason: String? = null
)
