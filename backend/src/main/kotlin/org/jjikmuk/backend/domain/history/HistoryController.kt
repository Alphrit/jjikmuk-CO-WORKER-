package org.jjikmuk.backend.domain.history

import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/histories")
class HistoryController(
    private val historyService: HistoryService
) {
    // 💡 GET /api/histories/user/{userId} 형태로 호출합니다.
    @GetMapping("/user/{userId}")
    fun getUserHistory(@PathVariable userId: Long): ResponseEntity<*> {
        val historyList = historyService.getUserHistory(userId)

        return ResponseEntity.ok(mapOf(
            "message" to "사용자 히스토리 조회 성공",
            "data" to historyList
        ))
    }
}