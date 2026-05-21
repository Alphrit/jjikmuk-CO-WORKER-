package org.jjikmuk.backend

import org.jjikmuk.backend.domain.product.Product
import org.jjikmuk.backend.domain.product.ProductController
import org.jjikmuk.backend.domain.product.ProductRepository
import org.jjikmuk.backend.domain.user.User
import org.jjikmuk.backend.domain.user.UserRepository
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.test.context.ActiveProfiles

@ActiveProfiles("test")
@SpringBootTest
class BackendApplicationTests @Autowired constructor(
    private val productController: ProductController,
    private val productRepository: ProductRepository,
    private val userRepository: UserRepository
) {
    @Test
    fun contextLoads() {
    }

    @Test
    fun `product scan api normalizes barcode and looks up product`() {
        val barcode = "8801234567890"
        productRepository.save(
            Product(
                reportNo = "REPORT-1",
                barcode = barcode,
                foodCode = "FOOD-1",
                productName = "Test Snack",
                manufacturer = "Test Maker",
                prdlstDcnm = "Snack",
                imageUrl = null,
                allergy = null,
                rawmtrlNm = null,
                servingSize = null,
                calories = null,
                carbs = null,
                protein = null,
                fat = null,
                sugar = null,
                sodium = null,
                cholesterol = null,
                saturatedFat = null,
                transFat = null
            )
        )

        val response = productController.getProductByBarcode("880-1234-567890", null)

        assertEquals(200, response.statusCode.value())
        val body = response.body as Map<*, *>
        val data = body["data"] as Map<*, *>
        val product = data["product"] as Product
        assertEquals(barcode, body["barcode"])
        assertEquals("Test Snack", product.productName)
    }

    @Test
    fun `product lookup keeps optional user allergy analysis`() {
        val barcode = "8801234567891"
        val user = userRepository.save(
            User(
                email = "allergy-test@example.com",
                nickname = "allergy-test",
                allergies = "Milk"
            )
        )
        productRepository.save(
            Product(
                reportNo = "REPORT-2",
                barcode = barcode,
                foodCode = "FOOD-2",
                productName = "Milk Snack",
                manufacturer = "Test Maker",
                prdlstDcnm = "Snack",
                imageUrl = null,
                allergy = "Milk",
                rawmtrlNm = null,
                servingSize = null,
                calories = null,
                carbs = null,
                protein = null,
                fat = null,
                sugar = null,
                sodium = null,
                cholesterol = null,
                saturatedFat = null,
                transFat = null
            )
        )

        val response = productController.getProductByBarcode(barcode, user.id)

        assertEquals(200, response.statusCode.value())
        val body = response.body as Map<*, *>
        val data = body["data"] as Map<*, *>
        val analysis = data["analysis"] as Map<*, *>
        assertEquals(true, analysis["isDangerous"])
    }
}
