package com.coworker.jjikmuk.feature.scanner

import com.coworker.jjikmuk.domain.model.Product
import com.coworker.jjikmuk.domain.model.ProductAnalysis

data class ScannerUiState(
    val isLoading: Boolean = false,
    val scannedBarcode: String? = null,
    val scanResult: ScannerResult? = null,
    val resultSequence: Int = 0
)

data class ScannerResult(
    val barcode: String,
    val product: Product?,
    val analysis: ProductAnalysis?,
    val requiresRegistration: Boolean
)

sealed interface ScannerUiEffect {
    data class ShowToast(val message: String) : ScannerUiEffect
}
