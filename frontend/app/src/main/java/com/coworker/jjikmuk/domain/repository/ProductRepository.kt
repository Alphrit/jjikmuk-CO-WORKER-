package com.coworker.jjikmuk.domain.repository

import com.coworker.jjikmuk.core.common.ApiResult
import com.coworker.jjikmuk.domain.model.ProductScanResult

interface ProductRepository {
    suspend fun scanProduct(barcode: String): ApiResult<ProductScanResult>
}
