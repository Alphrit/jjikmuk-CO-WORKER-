package com.coworker.jjikmuk.feature.home

import androidx.lifecycle.ViewModel
import com.coworker.jjikmuk.domain.repository.UserProfileRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

@HiltViewModel
class HomeViewModel @Inject constructor(
    userProfileRepository: UserProfileRepository
) : ViewModel() {

    private val initialProfiles: List<HomeProfileUiModel> =
        userProfileRepository.getProfiles()
            .mapIndexed { index, profile ->
                profile.toHomeProfileUiModel(
                    isSelected = index == 0
                )
            }

    private val _uiState = MutableStateFlow(
        HomeUiState(
            profiles = initialProfiles,
            selectedProfiles = initialProfiles.filter { profile ->
                profile.isSelected
            }
        )
    )

    val uiState: StateFlow<HomeUiState> = _uiState.asStateFlow()

    fun updateInputMessage(message: String) {
        _uiState.update { state ->
            state.copy(inputMessage = message)
        }
    }

    fun toggleProfile(profileId: String) {
        _uiState.update { state ->
            val updatedProfiles = state.profiles.map { profile ->
                if (profile.id == profileId) {
                    profile.copy(isSelected = !profile.isSelected)
                } else {
                    profile
                }
            }

            state.copy(
                profiles = updatedProfiles,
                selectedProfiles = updatedProfiles.filter { profile ->
                    profile.isSelected
                }
            )
        }
    }

    fun getCurrentMessage(): String {
        return uiState.value.inputMessage.trim()
    }

    fun clearInputMessage() {
        _uiState.update { state ->
            state.copy(inputMessage = "")
        }
    }
}