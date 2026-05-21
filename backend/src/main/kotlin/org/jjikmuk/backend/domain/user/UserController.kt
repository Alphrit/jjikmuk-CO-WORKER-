package org.jjikmuk.backend.domain.user

import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.*

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

    @PostMapping
    fun createUserProfile(@RequestBody request: UserProfileRequest): ResponseEntity<*> {
        val savedUser = userService.createUserProfile(request)
        return ResponseEntity.ok(mapOf("message" to "프로필 저장 성공", "data" to savedUser))
    }

    @GetMapping("/{id}")
    fun getUserProfile(@PathVariable id: Long): ResponseEntity<*> {
        val user = userService.getUserProfile(id)
        return if (user != null) {
            ResponseEntity.ok(mapOf("message" to "조회 성공", "data" to user))
        } else {
            ResponseEntity.status(404).body(mapOf("message" to "사용자를 찾을 수 없습니다.", "data" to null))
        }
    }

    @PutMapping("/{id}")
    fun updateUserProfile(@PathVariable id: Long, @RequestBody request: UserProfileRequest): ResponseEntity<*> {
        val updatedUser = userService.updateUserProfile(id, request)
            ?: return ResponseEntity.status(404).body(mapOf("message" to "사용자를 찾을 수 없습니다.", "data" to null))

        return ResponseEntity.ok(mapOf("message" to "프로필 수정 성공", "data" to updatedUser))
    }
}