package org.jjikmuk.backend.domain.product

import org.jjikmuk.backend.domain.user.User
import org.jjikmuk.backend.domain.user.UserRepository
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/products")
class ProductController(
    private val productRepository: ProductRepository,
    private val userRepository: UserRepository
) {
    @GetMapping("/{barcode}")
    fun getProductByBarcode(
        @PathVariable barcode: String,
        @RequestParam(required = false) userId: Long?
    ): ResponseEntity<*> {
        val normalizedBarcode = barcode.filter { it.isDigit() }
        if (normalizedBarcode.isBlank()) {
            return ResponseEntity.badRequest().body(mapOf("message" to "바코드 숫자를 입력해주세요."))
        }

        val product = productRepository.findFirstByBarcode(normalizedBarcode)
            ?: return ResponseEntity.status(404).body(
                mapOf(
                    "message" to "해당 바코드(${normalizedBarcode})의 제품을 찾을 수 없습니다.",
                    "barcode" to normalizedBarcode
                )
            )

        val user = userId?.let { userRepository.findById(it).orElse(null) }
        val responseData = mapOf(
            "product" to product,
            "analysis" to analyzeAllergies(product, user)
        )

        return ResponseEntity.ok(
            mapOf(
                "message" to "조회 성공",
                "barcode" to normalizedBarcode,
                "data" to responseData
            )
        )
    }

    @GetMapping("/search")
    fun searchProducts(
        @RequestParam keyword: String,
        @RequestParam(required = false) userId: Long?
    ): ResponseEntity<*> {
        if (keyword.trim().length < 2) {
            return ResponseEntity.badRequest().body(mapOf("message" to "검색어는 2글자 이상 입력해주세요."))
        }

        val products = productRepository.findTop50ByProductNameContaining(keyword)
        if (products.isEmpty()) {
            return ResponseEntity.ok(
                mapOf(
                    "message" to "'$keyword'에 해당하는 제품을 찾을 수 없습니다.",
                    "data" to emptyList<Any>()
                )
            )
        }

        val user = userId?.let { userRepository.findById(it).orElse(null) }
        val responseData = products.map { product ->
            mapOf(
                "product" to product,
                "analysis" to analyzeAllergies(product, user)
            )
        }

        return ResponseEntity.ok(mapOf("message" to "검색 성공", "data" to responseData))
    }

    private fun analyzeAllergies(product: Product, user: User?): Map<String, Any> {
        val dangerousIngredients = mutableListOf<String>()
        val userAllergies = user?.allergies
            ?.takeIf { it.isNotBlank() }
            ?.split(",")
            ?.map { it.trim() }
            ?.filter { it.isNotBlank() }
            ?: emptyList()

        if (userAllergies.isNotEmpty()) {
            val productAllergyInfo = (product.allergy ?: "") + (product.rawmtrlNm ?: "")
            for (allergy in userAllergies) {
                if (productAllergyInfo.contains(allergy)) {
                    dangerousIngredients.add(allergy)
                }
            }
        }

        val isDangerous = dangerousIngredients.isNotEmpty()
        return mapOf(
            "isDangerous" to isDangerous,
            "dangerousIngredients" to dangerousIngredients,
            "message" to if (isDangerous) {
                "주의! 알레르기 유발 성분(${dangerousIngredients.joinToString(", ")})이 포함되어 있습니다."
            } else {
                "안전하게 먹을 수 있습니다."
            }
        )
    }
}
