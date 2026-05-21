package com.coworker.jjikmuk.feature.home

import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.domain.model.ProfileRelation
import com.coworker.jjikmuk.domain.model.UserProfile

fun UserProfile.toHomeProfileUiModel(
    isSelected: Boolean
): HomeProfileUiModel {
    return HomeProfileUiModel(
        id = id,
        name = name,
        relationText = relation.toDisplayText(),
        imageResId = relation.toImageResId(),
        isSelected = isSelected
    )
}

private fun ProfileRelation.toDisplayText(): String {
    return when (this) {
        ProfileRelation.ME -> "나"
        ProfileRelation.COWORKER -> "코워커"
        ProfileRelation.FAMILY -> "가족"
    }
}

private fun ProfileRelation.toImageResId(): Int {
    return when (this) {
        ProfileRelation.ME -> R.drawable.ic_launcher_foreground
        ProfileRelation.COWORKER -> R.drawable.ic_launcher_foreground
        ProfileRelation.FAMILY -> R.drawable.ic_launcher_foreground
    }
}