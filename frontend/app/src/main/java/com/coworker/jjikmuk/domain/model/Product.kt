package com.coworker.jjikmuk.domain.model

data class ProductScanResult(
    val message: String?,
    val barcode: String,
    val product: Product?,
    val analysis: ProductAnalysis?
)

data class Product(
    val reportNo: String?,
    val barcode: String?,
    val productName: String?,
    val manufacturer: String?,
    val allergy: String?,
    val calories: Double?,
    val sugar: Double?,
    val sodium: Double?
)

data class ProductAnalysis(
    val isDangerous: Boolean?,
    val dangerousIngredients: List<String>?,
    val message: String?
)
