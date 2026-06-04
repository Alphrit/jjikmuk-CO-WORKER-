package com.coworker.jjikmuk.domain.model

data class Product(
    val id: String,
    val category: String,
    val name: String,
    val allergyTags: List<String> = emptyList(),
    val matchedAllergyTags: List<String> = emptyList(),
    val rawMaterials: String? = null,
    val caloriesKcal: Double? = null,
    val proteinG: Double? = null,
    val fatG: Double? = null,
    val carbohydrateG: Double? = null,
    val energyPercent: Long? = null,
    val proteinPercent: Long? = null,
    val fatPercent: Long? = null,
    val carbohydratePercent: Long? = null,
    val proteinMacroPercent: Double? = null,
    val fatMacroPercent: Double? = null,
    val carbohydrateMacroPercent: Double? = null
)
