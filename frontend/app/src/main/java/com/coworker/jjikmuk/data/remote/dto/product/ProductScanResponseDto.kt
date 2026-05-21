package com.coworker.jjikmuk.data.remote.dto.product

data class ProductScanResponseDto(
    val message: String?,
    val data: ProductScanDataDto?
)

data class ProductScanDataDto(
    val product: ProductInfoDto?,
    val nutrientPercents: NutrientPercentsDto?,
    val analysis: ProductAnalysisDto?
)

data class ProductInfoDto(
    val barcode: String?,
    val productName: String?,
    val manufacturer: String?,
    val reportNo: String?,
    val allergy: String?,
    val nutrientText: String?,
    val imageUrl: String?,
    val source: String?,
    val rawMaterials: String?,
    val energyKcal: Double?,
    val carbsG: Double?,
    val proteinG: Double?,
    val fatG: Double?,
    val sugarG: Double?,
    val sodiumMg: Double?,
    val cholesterolMg: Double?,
    val allergyWarning: String?,
    val calories: Double?,
    val sugar: Double?,
    val sodium: Double?
)

data class NutrientPercentsDto(
    val energyPercent: Int?,
    val carbsPercent: Int?,
    val proteinPercent: Int?,
    val fatPercent: Int?,
    val sugarPercent: Int?,
    val sodiumPercent: Int?,
    val cholesterolPercent: Int?
)

data class ProductAnalysisDto(
    val isDangerous: Boolean?,
    val dangerousIngredients: List<String>?,
    val message: String?
)

data class ProductErrorResponseDto(
    val status: Int?,
    val message: String?
)
