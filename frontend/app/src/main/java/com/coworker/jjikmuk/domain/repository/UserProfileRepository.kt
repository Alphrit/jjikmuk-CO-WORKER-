package com.coworker.jjikmuk.domain.repository

import com.coworker.jjikmuk.domain.model.UserProfile

interface UserProfileRepository {
    fun getProfiles(): List<UserProfile>
}