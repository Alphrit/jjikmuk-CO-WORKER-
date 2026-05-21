package com.coworker.jjikmuk.feature.scanner

import com.coworker.jjikmuk.domain.model.NutrientPercents
import com.coworker.jjikmuk.domain.model.ProductAnalysis
import com.coworker.jjikmuk.domain.model.ScannedProduct

data class ScannerUiState(
    val isLoading: Boolean = false,
    val scannedBarcode: String? = null,
    val scanResult: ScannerResult? = null,
    val resultSequence: Int = 0
)

data class ScannerResult(
    val barcode: String,
    val product: ScannedProduct?,
    val nutrientPercents: NutrientPercents?,
    val analysis: ProductAnalysis?,
    val requiresRegistration: Boolean
)

sealed interface ScannerUiEffect {
    data class ShowToast(val message: String) : ScannerUiEffect
}
