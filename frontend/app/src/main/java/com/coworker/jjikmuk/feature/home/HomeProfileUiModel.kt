package com.coworker.jjikmuk.feature.home

data class HomeProfileUiModel(
    val id: String,
    val name: String,
    val relationText: String,
    val imageResId: Int,
    val isSelected: Boolean
)