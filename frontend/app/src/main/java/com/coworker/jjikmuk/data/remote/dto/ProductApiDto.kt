package com.coworker.jjikmuk.data.remote.dto

import com.coworker.jjikmuk.domain.model.Product

data class ProductDetailApiResponse(
    val message: String? = null,
    val data: ProductDetailData? = null
)

data class ProductDetailData(
    val product: ProductApiProduct? = null,
    val nutrientPercents: ProductNutrientPercents? = null,
    val analysis: ProductAnalysis? = null
)

data class ProductApiProduct(
    val barcode: String? = null,
    val productName: String? = null,
    val manufacturer: String? = null,
    val allergy: String? = null,
    val rawMaterials: String? = null,
    val energyKcal: Double? = null,
    val carbsG: Double? = null,
    val proteinG: Double? = null,
    val fatG: Double? = null
)

data class ProductNutrientPercents(
    val energyPercent: Long? = null,
    val carbsPercent: Long? = null,
    val proteinPercent: Long? = null,
    val fatPercent: Long? = null,
    val carbsMacroPercent: Double? = null,
    val proteinMacroPercent: Double? = null,
    val fatMacroPercent: Double? = null
)

data class ProductAnalysis(
    val isDangerous: Boolean = false,
    val dangerousIngredients: List<String> = emptyList(),
    val message: String? = null
)

fun ProductDetailData.toDomainProduct(): Product? {
    val apiProduct = product ?: return null
    val barcode = apiProduct.barcode.orEmpty()
    if (barcode.isBlank()) return null

    return Product(
        id = barcode,
        category = apiProduct.manufacturer.toDisplayManufacturer(),
        name = apiProduct.productName.orEmpty().ifBlank { "이름 없는 상품" },
        allergyTags = apiProduct.allergy.toAllergyTags(),
        matchedAllergyTags = analysis?.dangerousIngredients.orEmpty(),
        rawMaterials = apiProduct.rawMaterials,
        caloriesKcal = apiProduct.energyKcal,
        proteinG = apiProduct.proteinG,
        fatG = apiProduct.fatG,
        carbohydrateG = apiProduct.carbsG,
        energyPercent = nutrientPercents?.energyPercent,
        proteinPercent = nutrientPercents?.proteinPercent,
        fatPercent = nutrientPercents?.fatPercent,
        carbohydratePercent = nutrientPercents?.carbsPercent,
        proteinMacroPercent = nutrientPercents?.proteinMacroPercent,
        fatMacroPercent = nutrientPercents?.fatMacroPercent,
        carbohydrateMacroPercent = nutrientPercents?.carbsMacroPercent
    )
}

private fun String?.toDisplayManufacturer(): String {
    val manufacturer = this?.trim().orEmpty()
    return when {
        manufacturer.isBlank() -> "제조사 정보 없음"
        manufacturer == "알수없음" -> "제조사 정보 없음"
        manufacturer.equals("unknown", ignoreCase = true) -> "제조사 정보 없음"
        else -> manufacturer
    }
}

private fun String?.toAllergyTags(): List<String> {
    return this
        ?.split(",", "/", "·", "ㆍ")
        ?.map { value -> value.trim() }
        ?.filter { value -> value.isNotBlank() }
        .orEmpty()
}
