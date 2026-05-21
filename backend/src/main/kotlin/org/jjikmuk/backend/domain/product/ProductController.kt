package org.jjikmuk.backend.domain.product

import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.*
import org.springframework.security.core.Authentication
import org.jjikmuk.backend.global.exception.CustomException
import org.springframework.http.HttpStatus

@RestController
@RequestMapping("/api/products")
class ProductController(
    private val productService: ProductService
) {
    // 🚀 보안과 편의성을 모두 잡은 마법의 검증 함수!
    private fun getValidatedUserId(requestedUserId: Long?, authentication: Authentication?): Long? {
        // 비로그인 사용자 처리
        if (authentication == null || authentication.principal == "anonymousUser") {
            if (requestedUserId != null) {
                throw CustomException(HttpStatus.UNAUTHORIZED, "특정 사용자의 기준으로 조회하려면 로그인이 필요합니다.")
            }
            return null // 비로그인이면 그냥 범용 데이터 반환
        }

        val currentUserId = authentication.principal.toString().toLong()
        val isAdmin = authentication.authorities.any { it.authority == "ROLE_ADMIN" }

        // 프론트에서 userId를 안 보냈다면 내 ID로 자동 셋팅, 보냈다면 그 ID 사용
        val targetId = requestedUserId ?: currentUserId

        // 핵심 방어 로직: "요청한 ID가 내 ID도 아니고, 내가 관리자도 아니라면?" -> 차단!
        if (targetId != currentUserId && !isAdmin) {
            throw CustomException(HttpStatus.FORBIDDEN, "다른 사용자의 기준으로 검색할 수 없습니다.")
        }

        return targetId
    }

    @GetMapping("/{barcode}")
    fun getProductByBarcode(
        @PathVariable barcode: String,
        @RequestParam(required = false) userId: Long?, // 다시 부활!
        authentication: Authentication?
    ): ResponseEntity<*> {

        // 🚀 검증 함수 통과 (실패 시 여기서 에러 터지고 끝남)
        val targetUserId = getValidatedUserId(userId, authentication)

        val responseData = productService.getProductAnalysis(barcode, targetUserId)
            ?: return ResponseEntity.status(404).body(mapOf("message" to "해당 바코드(${barcode})의 제품을 찾을 수 없습니다."))

        return ResponseEntity.ok(mapOf("message" to "조회 성공", "data" to responseData))
    }

    @GetMapping("/search")
    fun searchProducts(
        @RequestParam keyword: String,
        @RequestParam(required = false) userId: Long?, // 다시 부활!
        authentication: Authentication?
    ): ResponseEntity<*> {
        if (keyword.trim().length < 2) {
            return ResponseEntity.badRequest().body(mapOf("message" to "검색어는 2글자 이상 입력해주세요."))
        }

        // 🚀 검증 함수 통과
        val targetUserId = getValidatedUserId(userId, authentication)

        val responseData = productService.searchProductsAnalysis(keyword, targetUserId)

        if (responseData.isEmpty()) {
            return ResponseEntity.ok(
                mapOf(
                    "message" to "'$keyword'에 해당하는 제품을 찾을 수 없습니다.",
                    "data" to emptyList<Any>()
                )
            )
        }

        return ResponseEntity.ok(mapOf("message" to "검색 성공", "data" to responseData))
    }
}
