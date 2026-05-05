package org.jjikmuk.backend.domain.user

import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.*

// 💡 프론트엔드에서 날아올 JSON 데이터를 담을 바구니(DTO)입니다.
data class UserProfileRequest(
    val email: String,
    val nickname: String,
    val allergies: String?,
    val diseases: String?
)

@RestController
@RequestMapping("/api/users")
class UserController(
    private val userRepository: UserRepository
) {
    @PostMapping
    fun createUserProfile(@RequestBody request: UserProfileRequest): ResponseEntity<*> {
        val user = User(
            email = request.email,
            nickname = request.nickname,
            allergies = request.allergies,
            diseases = request.diseases
        )
        val savedUser = userRepository.save(user)

        return ResponseEntity.ok(mapOf("message" to "프로필 저장 성공", "data" to savedUser))
    }
    @GetMapping("/{id}")
    fun getUserProfile(@PathVariable id: Long): ResponseEntity<*> {
        val user = userRepository.findById(id).orElse(null)

        return if (user != null) {
            ResponseEntity.ok(mapOf("message" to "조회 성공", "data" to user))
        } else {
            ResponseEntity.status(404).body(mapOf("message" to "사용자를 찾을 수 없습니다.", "data" to null))
        }
    }

    @PutMapping("/{id}")
    fun updateUserProfile(@PathVariable id: Long, @RequestBody request: UserProfileRequest): ResponseEntity<*> {
        val user = userRepository.findById(id).orElse(null)
            ?: return ResponseEntity.status(404).body(mapOf("message" to "사용자를 찾을 수 없습니다.", "data" to null))

        user.updateProfile(request.nickname, request.allergies, request.diseases)
        val updatedUser = userRepository.save(user)

        return ResponseEntity.ok(mapOf("message" to "프로필 수정 성공", "data" to updatedUser))
    }
}