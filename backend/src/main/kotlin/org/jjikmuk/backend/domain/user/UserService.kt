package org.jjikmuk.backend.domain.user

import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional

@Service
class UserService(
    private val userRepository: UserRepository
) {
    @Transactional(readOnly = true)
    fun getAllUsers(): List<User> {
        return userRepository.findAll()
    }

    @Transactional
    fun createUserProfile(request: UserProfileRequest): User {
        val user = User(
            email = request.email,
            nickname = request.nickname,
            allergies = request.allergies,
            diseases = request.diseases
        )
        return userRepository.save(user)
    }

    @Transactional(readOnly = true)
    fun getUserProfile(id: Long): User? {
        return userRepository.findById(id).orElse(null)
    }

    @Transactional
    fun updateUserProfile(id: Long, request: UserProfileRequest): User? {
        val user = userRepository.findById(id).orElse(null) ?: return null
        user.updateProfile(request.nickname, request.allergies, request.diseases)
        return userRepository.save(user)
    }
}