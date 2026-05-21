package com.coworker.jjikmuk.feature.product.model

data class ProductUiModel(
    val id: String,
    val category: String,
    val name: String,
    val imageResId: Int,
    val allergyTags: List<String> = emptyList()
)