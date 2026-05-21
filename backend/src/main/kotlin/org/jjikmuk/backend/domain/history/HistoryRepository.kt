package org.jjikmuk.backend.domain.history

import org.springframework.data.jpa.repository.JpaRepository
import org.jjikmuk.backend.domain.product.Product
import java.time.LocalDateTime

data class HistoryResponse(
    val id: Long,
    val actionType: String,
    val scannedAt: LocalDateTime,
    val product: Product? // 바코드를 기반으로 찾아낸 실제 상품 정보 전체
)

interface HistoryRepository : JpaRepository<History, Long> {
    // 💡 특정 유저의 기록을 최신순으로 가져옵니다.
    fun findByUserIdOrderByCreatedAtDesc(userId: Long): List<History>

    // 💡 특정 유저의 '스캔(SCAN)' 기록만 최신순으로 가져오고 싶을 때 사용합니다.
    fun findByUserIdAndActionTypeOrderByCreatedAtDesc(userId: Long, actionType: String): List<History>
}