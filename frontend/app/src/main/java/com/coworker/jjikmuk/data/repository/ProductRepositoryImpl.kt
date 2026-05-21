package com.coworker.jjikmuk.data.repository

import com.coworker.jjikmuk.core.common.ApiResult
import com.coworker.jjikmuk.data.local.dummy.ProductDummyData
import com.coworker.jjikmuk.data.remote.api.ProductApi
import com.coworker.jjikmuk.data.remote.dto.product.NutrientPercentsDto
import com.coworker.jjikmuk.data.remote.dto.product.ProductAnalysisDto
import com.coworker.jjikmuk.data.remote.dto.product.ProductErrorResponseDto
import com.coworker.jjikmuk.data.remote.dto.product.ProductInfoDto
import com.coworker.jjikmuk.data.remote.dto.product.ProductScanResponseDto
import com.coworker.jjikmuk.domain.model.NutrientPercents
import com.coworker.jjikmuk.domain.model.Product
import com.coworker.jjikmuk.domain.model.ProductAnalysis
import com.coworker.jjikmuk.domain.model.ProductScanResult
import com.coworker.jjikmuk.domain.model.ScannedProduct
import com.coworker.jjikmuk.domain.repository.ProductRepository
import com.google.gson.Gson
import javax.inject.Inject

class ProductRepositoryImpl @Inject constructor(
    private val productApi: ProductApi
) : ProductRepository {
    private val gson = Gson()

    override fun getAllProducts(): List<Product> {
        return ProductDummyData.recommendProducts
    }

    override fun getRecommendProducts(): List<Product> {
        return getAllProducts()
    }

    override fun getRecommendProducts(limit: Int): List<Product> {
        return getAllProducts().take(limit)
    }

    override fun findProductById(productId: String): Product? {
        return ProductDummyData.findProductById(productId)
    }

    override suspend fun scanProduct(
        barcode: String,
        userId: Long?
    ): ApiResult<ProductScanResult> {
        return try {
            val response = productApi.scanProduct(barcode, userId)
            if (response.isSuccessful) {
                val body = response.body()
                ApiResult.Success(body.toDomain(barcode))
            } else {
                val errorMessage = response.errorMessage()
                ApiResult.Error(
                    message = errorMessage ?: "Product lookup failed: HTTP ${response.code()}",
                    statusCode = response.code()
                )
            }
        } catch (exception: Exception) {
            ApiResult.Error(
                message = exception.message ?: "Product lookup failed.",
                throwable = exception
            )
        }
    }

    private fun ProductScanResponseDto?.toDomain(fallbackBarcode: String): ProductScanResult {
        return ProductScanResult(
            message = this?.message,
            barcode = this?.data?.product?.barcode ?: fallbackBarcode,
            product = this?.data?.product?.toDomain(),
            nutrientPercents = this?.data?.nutrientPercents?.toDomain(),
            analysis = this?.data?.analysis?.toDomain()
        )
    }

    private fun ProductInfoDto.toDomain(): ScannedProduct {
        return ScannedProduct(
            reportNo = reportNo,
            barcode = barcode,
            productName = productName,
            manufacturer = manufacturer,
            allergy = allergy,
            nutrientText = nutrientText,
            imageUrl = imageUrl,
            source = source,
            rawMaterials = rawMaterials,
            calories = energyKcal ?: calories,
            carbs = carbsG,
            protein = proteinG,
            fat = fatG,
            sugar = sugarG ?: sugar,
            sodium = sodiumMg ?: sodium,
            cholesterol = cholesterolMg,
            allergyWarning = allergyWarning
        )
    }

    private fun NutrientPercentsDto.toDomain(): NutrientPercents {
        return NutrientPercents(
            energyPercent = energyPercent,
            carbsPercent = carbsPercent,
            proteinPercent = proteinPercent,
            fatPercent = fatPercent,
            sugarPercent = sugarPercent,
            sodiumPercent = sodiumPercent,
            cholesterolPercent = cholesterolPercent
        )
    }

    private fun ProductAnalysisDto.toDomain(): ProductAnalysis {
        return ProductAnalysis(
            isDangerous = isDangerous,
            dangerousIngredients = dangerousIngredients,
            message = message
        )
    }

    private fun retrofit2.Response<ProductScanResponseDto>.errorMessage(): String? {
        val rawError = errorBody()?.string() ?: return null
        return runCatching {
            gson.fromJson(rawError, ProductErrorResponseDto::class.java)?.message
        }.getOrNull()
    }
}
