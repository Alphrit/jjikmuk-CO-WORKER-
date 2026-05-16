package com.coworker.jjikmuk.data.remote.api

import com.coworker.jjikmuk.data.remote.dto.product.ProductScanResponseDto
import retrofit2.Response
import retrofit2.http.GET
import retrofit2.http.Path

interface ProductApi {
    @GET("api/products/{barcode}")
    suspend fun scanProduct(
        @Path("barcode") barcode: String
    ): Response<ProductScanResponseDto>
}
