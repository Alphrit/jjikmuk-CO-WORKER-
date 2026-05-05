package org.jjikmuk.backend.domain.product

import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.bind.annotation.*
import org.jjikmuk.backend.domain.user.UserRepository

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
        val product = productRepository.findFirstByBarcode(barcode)
            ?: return ResponseEntity.status(404).body(mapOf("message" to "해당 바코드(${barcode})의 제품을 찾을 수 없습니다."))

        var isDangerous = false
        val dangerousIngredients = mutableListOf<String>()
        if (userId != null) {
            val user = userRepository.findById(userId).orElse(null)
            if (user != null && !user.allergies.isNullOrBlank()) {
                val userAllergies = user.allergies!!.split(",").map { it.trim() }
                val productAllergyInfo = (product.allergy ?: "") + (product.rawmtrlNm ?: "")
                for (allergy in userAllergies) {
                    if (productAllergyInfo.contains(allergy)) {
                        isDangerous = true
                        dangerousIngredients.add(allergy)
                    }
                }
            }
        }
        val responseData = mapOf(
            "product" to product,
            "analysis" to mapOf(
                "isDangerous" to isDangerous,
                "dangerousIngredients" to dangerousIngredients,
                "message" to if (isDangerous) "위험! 알레르기 유발 성분(${dangerousIngredients.joinToString(", ")})이 포함되어 있습니다." else "안전하게 섭취할 수 있습니다."
            )
        )

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
        val products = productRepository.findTop50ByProductNameContaining(keyword)

        if (products.isEmpty()) {
            return ResponseEntity.ok(mapOf("message" to "'$keyword'에 해당하는 제품을 찾을 수 없습니다.", "data" to emptyList<Any>()))
        }

        val user = if (userId != null) userRepository.findById(userId).orElse(null) else null
        val userAllergies = user?.allergies?.takeIf { it.isNotBlank() }?.split(",")?.map { it.trim() } ?: emptyList()
        val responseData = products.map { product ->
            var isDangerous = false
            val dangerousIngredients = mutableListOf<String>()
            if (userAllergies.isNotEmpty()) {
                val productAllergyInfo = (product.allergy ?: "") + (product.rawmtrlNm ?: "")
                for (allergy in userAllergies) {
                    if (productAllergyInfo.contains(allergy)) {
                        isDangerous = true
                        dangerousIngredients.add(allergy)
                    }
                }
            }
            mapOf(
                "product" to product,
                "analysis" to mapOf(
                    "isDangerous" to isDangerous,
                    "dangerousIngredients" to dangerousIngredients,
                    "message" to if (isDangerous) "위험! 알레르기 유발 성분(${dangerousIngredients.joinToString(", ")})이 포함되어 있습니다." else "안전하게 섭취할 수 있습니다."
                )
            )
        }

        return ResponseEntity.ok(mapOf("message" to "검색 성공", "data" to responseData))
    }
}