package org.jjikmuk.backend.domain.history

import org.jjikmuk.backend.domain.product.ProductRepository
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional

@Service
@Transactional(readOnly = true)
class HistoryService(
    private val historyRepository: HistoryRepository,
    private val productRepository: ProductRepository // 상품 정보를 찾기 위해 주입!
) {
    fun getUserHistory(userId: Long): List<HistoryResponse> {
        // 1. 해당 유저의 기록을 최신순으로 가져옵니다.
        val histories = historyRepository.findByUserIdOrderByCreatedAtDesc(userId)

        // 2. 기록 속 바코드를 이용해 상품 정보를 찾고, 아까 만든 DTO(바구니)에 담아 반환합니다.
        return histories.map { history ->
            val product = productRepository.findFirstByBarcode(history.barcode)

            HistoryResponse(
                id = history.id!!,
                actionType = history.actionType,
                scannedAt = history.createdAt,
                product = product // 여기서 합체!
            )
        }
    }
}