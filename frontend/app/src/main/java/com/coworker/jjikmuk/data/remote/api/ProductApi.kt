package com.coworker.jjikmuk.data.remote.api

import com.coworker.jjikmuk.data.remote.dto.ProductDetailApiResponse
import retrofit2.http.GET
import retrofit2.http.Path

interface ProductApi {

    @GET("api/products/{barcode}")
    suspend fun getProductByBarcode(
        @Path("barcode") barcode: String
    ): ProductDetailApiResponse
}
