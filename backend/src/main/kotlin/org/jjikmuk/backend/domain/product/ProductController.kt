package org.jjikmuk.backend.domain.product

import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/products")
class ProductController(
    private val productService: ProductService
) {
    @GetMapping("/{barcode}")
    fun getProductByBarcode(
        @PathVariable barcode: String,
        @RequestParam(required = false) userId: Long?
    ): ResponseEntity<*> {
        val responseData = productService.getProductAnalysis(barcode, userId)
            ?: return ResponseEntity.status(404)
                .body(mapOf("message" to "해당 바코드(${barcode})의 제품을 찾을 수 없습니다."))

        return ResponseEntity.ok(mapOf("message" to "조회 성공", "data" to responseData))
    }

    @GetMapping("/search")
    fun searchProducts(
        @RequestParam keyword: String,
        @RequestParam(required = false) userId: Long?
    ): ResponseEntity<*> {
        if (keyword.trim().length < 2) {
            return ResponseEntity.badRequest().body(mapOf("message" to "검색어는 2글자 이상 입력해주세요."))
        }

        val responseData = productService.searchProductsAnalysis(keyword, userId)
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
