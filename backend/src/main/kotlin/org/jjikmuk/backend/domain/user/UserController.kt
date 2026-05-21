package org.jjikmuk.backend.domain.user

import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.*
import org.springframework.security.core.Authentication
import org.jjikmuk.backend.global.exception.CustomException
import org.springframework.http.HttpStatus

data class UserProfileRequest(
    val email: String,
    val nickname: String,
    val allergies: String?,
    val diseases: String?
)

@RestController
@RequestMapping("/api/users")
class UserController(
    private val userService: UserService // 💡 Service 주입
) {
    @GetMapping
    fun getAllUsers(): ResponseEntity<*> {
        val users = userService.getAllUsers()
        return ResponseEntity.ok(mapOf("message" to "전체 유저 목록 조회 성공", "data" to users))
    }
    
    @GetMapping("/{userId}")
    fun getUserInfo(@PathVariable userId: Long, authentication: Authentication): ResponseEntity<*> {
        val currentUserId = authentication.principal.toString().toLong()
        val isAdmin = authentication.authorities.any { it.authority == "ROLE_ADMIN" }
        if (currentUserId != userId && !isAdmin) {
            throw CustomException(HttpStatus.FORBIDDEN, "권한이 없습니다.")
        }

        val user = userService.getUserProfile(userId)
            ?: throw CustomException(HttpStatus.NOT_FOUND, "사용자를 찾을 수 없습니다.")

        return ResponseEntity.ok(mapOf("message" to "조회 성공", "data" to user))
    }

    @PutMapping("/{id}")
    fun updateUserProfile(@PathVariable id: Long, @RequestBody request: UserProfileRequest): ResponseEntity<*> {
        val updatedUser = userService.updateUserProfile(id, request)
            ?: return ResponseEntity.status(404).body(mapOf("message" to "사용자를 찾을 수 없습니다.", "data" to null))

        return ResponseEntity.ok(mapOf("message" to "프로필 수정 성공", "data" to updatedUser))
    }
}