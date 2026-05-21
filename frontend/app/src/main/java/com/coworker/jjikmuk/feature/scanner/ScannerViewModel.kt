package com.coworker.jjikmuk.feature.scanner

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.coworker.jjikmuk.BuildConfig
import com.coworker.jjikmuk.core.common.ApiResult
import com.coworker.jjikmuk.domain.model.ProductAnalysis
import com.coworker.jjikmuk.domain.model.ScannedProduct
import com.coworker.jjikmuk.domain.repository.ProductRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

@HiltViewModel
class ScannerViewModel @Inject constructor(
    private val productRepository: ProductRepository
) : ViewModel() {
    private val _uiState = MutableStateFlow(ScannerUiState())
    val uiState: StateFlow<ScannerUiState> = _uiState.asStateFlow()

    private val _effect = MutableSharedFlow<ScannerUiEffect>()
    val effect: SharedFlow<ScannerUiEffect> = _effect.asSharedFlow()

    fun submitBarcode(barcode: String) {
        viewModelScope.launch {
            Log.d(TAG, "Product lookup started: $barcode")
            _uiState.update {
                it.copy(
                    isLoading = true,
                    scannedBarcode = barcode,
                    scanResult = null
                )
            }

            if (BuildConfig.USE_MOCK_SCAN) {
                Log.d(TAG, "Using mock scan result")
                delay(MOCK_LOOKUP_DELAY_MS)
                val mockResult = createMockResult(barcode)
                _uiState.update {
                    it.copy(
                        isLoading = false,
                        scannedBarcode = barcode,
                        scanResult = mockResult,
                        resultSequence = it.resultSequence + 1
                    )
                }
                return@launch
            }

            when (val result = productRepository.scanProduct(barcode, scanUserId())) {
                is ApiResult.Success -> {
                    val product = result.data.product
                    Log.d(
                        TAG,
                        "Product lookup success: barcode=${result.data.barcode}, hasProduct=${product != null}"
                    )
                    _uiState.update {
                        it.copy(
                            isLoading = false,
                            scannedBarcode = result.data.barcode,
                            scanResult = ScannerResult(
                                barcode = result.data.barcode,
                                product = product,
                                nutrientPercents = result.data.nutrientPercents,
                                analysis = result.data.analysis,
                                requiresRegistration = product == null
                            ),
                            resultSequence = it.resultSequence + 1
                        )
                    }
                }

                is ApiResult.Error -> {
                    Log.d(TAG, "Product lookup error: ${result.message}", result.throwable)
                    if (result.statusCode != HTTP_NOT_FOUND) {
                        _uiState.update {
                            it.copy(
                                isLoading = false,
                                scannedBarcode = barcode,
                                scanResult = null
                            )
                        }
                        _effect.emit(ScannerUiEffect.ShowToast(result.message))
                        return@launch
                    }

                    _uiState.update {
                        it.copy(
                            isLoading = false,
                            scannedBarcode = barcode,
                            scanResult = ScannerResult(
                                barcode = barcode,
                                product = null,
                                nutrientPercents = null,
                                analysis = null,
                                requiresRegistration = true
                            ),
                            resultSequence = it.resultSequence + 1
                        )
                    }
                }
            }
        }
    }

    private fun createMockResult(barcode: String): ScannerResult {
        val shouldShowRegistrationSheet = barcode.endsWith(MOCK_UNREGISTERED_SUFFIX)
        if (shouldShowRegistrationSheet) {
            return ScannerResult(
                barcode = barcode,
                product = null,
                nutrientPercents = null,
                analysis = null,
                requiresRegistration = true
            )
        }

        return ScannerResult(
            barcode = barcode,
            product = ScannedProduct(
                reportNo = "mock-report",
                barcode = barcode,
                productName = "농심 새우깡",
                manufacturer = "농심",
                allergy = null,
                nutrientText = null,
                imageUrl = null,
                source = "mock",
                rawMaterials = null,
                calories = 220.0,
                carbs = null,
                protein = null,
                fat = 7.0,
                sugar = 25.0,
                sodium = 180.0,
                cholesterol = null,
                allergyWarning = null
            ),
            nutrientPercents = null,
            analysis = ProductAnalysis(
                isDangerous = false,
                dangerousIngredients = emptyList(),
                message = "Mock safe product"
            ),
            requiresRegistration = false
        )
    }

    private fun scanUserId(): Long? {
        return BuildConfig.SCAN_USER_ID.toLongOrNull()
    }

    private companion object {
        const val TAG = "ScannerViewModel"
        const val HTTP_NOT_FOUND = 404
        const val MOCK_LOOKUP_DELAY_MS = 500L
        const val MOCK_UNREGISTERED_SUFFIX = "0"
    }
}
