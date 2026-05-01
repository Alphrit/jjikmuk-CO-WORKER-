package org.jjikmuk.backend.domain.product

import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/products")
class ProductController(
    private val productRepository: ProductRepository
) {

    @GetMapping("/{barcode}")
    fun getProductByBarcode(@PathVariable barcode: String): ResponseEntity<Any> {
        println("🔍 바코드 검색 요청 들어옴: $barcode")
        val product = productRepository.findFirstByBarcode(barcode)
        return if (product != null) {
            ResponseEntity.ok(product)
        } else {
            ResponseEntity.status(404).body(
                mapOf("message" to "해당 바코드($barcode)의 제품을 찾을 수 없습니다.")
            )
        }
    }
}