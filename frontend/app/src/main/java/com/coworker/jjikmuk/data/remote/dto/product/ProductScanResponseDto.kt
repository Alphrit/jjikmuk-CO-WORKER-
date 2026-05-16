package com.coworker.jjikmuk.data.remote.dto.product

data class ProductScanResponseDto(
    val message: String?,
    val barcode: String?,
    val data: ProductScanDataDto?
)

data class ProductScanDataDto(
    val product: ProductInfoDto?,
    val analysis: ProductAnalysisDto?
)

data class ProductInfoDto(
    val reportNo: String?,
    val barcode: String?,
    val productName: String?,
    val manufacturer: String?,
    val allergy: String?,
    val calories: Double?,
    val sugar: Double?,
    val sodium: Double?
)

data class ProductAnalysisDto(
    val isDangerous: Boolean?,
    val dangerousIngredients: List<String>?,
    val message: String?
)
