package com.coworker.jjikmuk.domain.model

data class Product(
    val id: String,
    val category: String,
    val name: String,
    val allergyTags: List<String> = emptyList()
)

data class ProductScanResult(
    val message: String?,
    val barcode: String,
    val product: ScannedProduct?,
    val nutrientPercents: NutrientPercents?,
    val analysis: ProductAnalysis?
)

data class ScannedProduct(
    val reportNo: String?,
    val barcode: String?,
    val productName: String?,
    val manufacturer: String?,
    val allergy: String?,
    val nutrientText: String?,
    val imageUrl: String?,
    val source: String?,
    val rawMaterials: String?,
    val calories: Double?,
    val carbs: Double?,
    val protein: Double?,
    val fat: Double?,
    val sugar: Double?,
    val sodium: Double?,
    val cholesterol: Double?,
    val allergyWarning: String?
)

data class NutrientPercents(
    val energyPercent: Int?,
    val carbsPercent: Int?,
    val proteinPercent: Int?,
    val fatPercent: Int?,
    val sugarPercent: Int?,
    val sodiumPercent: Int?,
    val cholesterolPercent: Int?
)

data class ProductAnalysis(
    val isDangerous: Boolean?,
    val dangerousIngredients: List<String>?,
    val message: String?
)
