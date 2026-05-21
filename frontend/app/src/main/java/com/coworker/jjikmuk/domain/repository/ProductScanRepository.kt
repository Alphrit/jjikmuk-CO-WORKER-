package com.coworker.jjikmuk.domain.repository

import com.coworker.jjikmuk.core.common.ApiResult
import com.coworker.jjikmuk.domain.model.ProductScanResult

interface ProductScanRepository {
    suspend fun scanProduct(barcode: String, userId: Long? = null): ApiResult<ProductScanResult>
}
