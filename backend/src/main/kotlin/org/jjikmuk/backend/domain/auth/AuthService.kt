package org.jjikmuk.backend.domain.auth

import org.jjikmuk.backend.domain.user.User
import org.jjikmuk.backend.domain.user.UserRepository
import org.jjikmuk.backend.global.config.JwtProvider // 💡 추가됨!
import org.jjikmuk.backend.global.exception.CustomException
import org.springframework.http.HttpStatus
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional

@Service
class AuthService(
    private val userRepository: UserRepository,
    private val passwordEncoder: BCryptPasswordEncoder,
    private val jwtProvider: JwtProvider // 🚀 1. 토큰 발급기 주입!
) {
    @Transactional
    fun signup(request: SignupRequest): User {
        if (userRepository.findByEmail(request.email) != null) {
            throw CustomException(HttpStatus.BAD_REQUEST, "이미 가입된 이메일입니다.")
        }
        val encodedPassword = passwordEncoder.encode(request.password)
        val user = User(
            email = request.email,
            password = encodedPassword!!,
            nickname = request.nickname,
            allergies = request.allergies,
            diseases = request.diseases
        )
        return userRepository.save(user)
    }

    @Transactional(readOnly = true)
    fun login(request: LoginRequest): String {
        val user = userRepository.findByEmail(request.email)
            ?: throw CustomException(HttpStatus.UNAUTHORIZED, "이메일 또는 비밀번호가 일치하지 않습니다.")

        if (!passwordEncoder.matches(request.password, user.password)) {
            throw CustomException(HttpStatus.UNAUTHORIZED, "이메일 또는 비밀번호가 일치하지 않습니다.")
        }
        return jwtProvider.createToken(user.id!!, user.email, user.role.name)

    }
}