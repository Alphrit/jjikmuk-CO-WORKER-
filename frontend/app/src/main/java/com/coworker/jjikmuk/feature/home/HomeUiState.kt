package com.coworker.jjikmuk.feature.home

data class HomeUiState(
    val profiles: List<HomeProfileUiModel> = emptyList(),
    val selectedProfiles: List<HomeProfileUiModel> = emptyList(),
    val inputMessage: String = "",
    val errorMessage: String? = null
)