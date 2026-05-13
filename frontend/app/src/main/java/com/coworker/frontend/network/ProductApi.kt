package com.coworker.frontend.network

import retrofit2.Response
import retrofit2.http.GET
import retrofit2.http.Path

data class ProductScanResponse(
    val message: String?,
    val barcode: String?,
    val data: ProductScanData?
)

data class ProductScanData(
    val product: ProductInfo?,
    val analysis: ProductAnalysis?
)

data class ProductInfo(
    val reportNo: String?,
    val barcode: String?,
    val productName: String?,
    val manufacturer: String?,
    val allergy: String?,
    val calories: Double?,
    val sugar: Double?,
    val sodium: Double?
)

data class ProductAnalysis(
    val isDangerous: Boolean?,
    val dangerousIngredients: List<String>?,
    val message: String?
)

interface ProductApi {
    @GET("api/products/{barcode}")
    suspend fun scanProduct(
        @Path("barcode") barcode: String
    ): Response<ProductScanResponse>
}
