package com.coworker.jjikmuk.data.repository

import com.coworker.jjikmuk.core.common.ApiResult
import com.coworker.jjikmuk.data.remote.api.ProductApi
import com.coworker.jjikmuk.data.remote.dto.product.ProductAnalysisDto
import com.coworker.jjikmuk.data.remote.dto.product.ProductInfoDto
import com.coworker.jjikmuk.data.remote.dto.product.ProductScanResponseDto
import com.coworker.jjikmuk.domain.model.Product
import com.coworker.jjikmuk.domain.model.ProductAnalysis
import com.coworker.jjikmuk.domain.model.ProductScanResult
import com.coworker.jjikmuk.domain.repository.ProductRepository
import javax.inject.Inject

class ProductRepositoryImpl @Inject constructor(
    private val productApi: ProductApi
) : ProductRepository {

    override suspend fun scanProduct(barcode: String): ApiResult<ProductScanResult> {
        return try {
            val response = productApi.scanProduct(barcode)
            if (response.isSuccessful) {
                val body = response.body()
                ApiResult.Success(body.toDomain(barcode))
            } else {
                ApiResult.Error("Product lookup failed: HTTP ${response.code()}")
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
            barcode = this?.barcode ?: fallbackBarcode,
            product = this?.data?.product?.toDomain(),
            analysis = this?.data?.analysis?.toDomain()
        )
    }

    private fun ProductInfoDto.toDomain(): Product {
        return Product(
            reportNo = reportNo,
            barcode = barcode,
            productName = productName,
            manufacturer = manufacturer,
            allergy = allergy,
            calories = calories,
            sugar = sugar,
            sodium = sodium
        )
    }

    private fun ProductAnalysisDto.toDomain(): ProductAnalysis {
        return ProductAnalysis(
            isDangerous = isDangerous,
            dangerousIngredients = dangerousIngredients,
            message = message
        )
    }
}
