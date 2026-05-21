package org.jjikmuk.backend.domain.auth

import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.*

@RestController
@RequestMapping("/api/auth")
class AuthController(
    private val authService: AuthService
) {
    @PostMapping("/signup")
    fun signup(@RequestBody request: SignupRequest): ResponseEntity<*> {
        val savedUser = authService.signup(request)
        return ResponseEntity.ok(mapOf("message" to "회원가입 성공", "data" to savedUser.id))
    }

    @PostMapping("/login")
    fun login(@RequestBody request: LoginRequest): ResponseEntity<*> {
        val token = authService.login(request)
        return ResponseEntity.ok(mapOf("message" to "로그인 성공", "token" to token))
    }
}