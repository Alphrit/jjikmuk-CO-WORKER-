package com.coworker.jjikmuk.feature.scanner

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.coworker.jjikmuk.data.local.dummy.ProductDummyData
import com.coworker.jjikmuk.domain.model.NutrientPercents
import com.coworker.jjikmuk.domain.model.ProductAnalysis
import com.coworker.jjikmuk.domain.model.ScannedProduct
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
class ScannerViewModel @Inject constructor() : ViewModel() {
    private val _uiState = MutableStateFlow(ScannerUiState())
    val uiState: StateFlow<ScannerUiState> = _uiState.asStateFlow()

    private val _effect = MutableSharedFlow<ScannerUiEffect>()
    val effect: SharedFlow<ScannerUiEffect> = _effect.asSharedFlow()

    fun submitBarcode(barcode: String) {
        viewModelScope.launch {
            Log.d(TAG, "Demo product lookup started: $barcode")
            _uiState.update {
                it.copy(
                    isLoading = true,
                    scannedBarcode = barcode,
                    scanResult = null
                )
            }

            delay(DEMO_LOOKUP_DELAY_MS)
            _uiState.update {
                it.copy(
                    isLoading = false,
                    scannedBarcode = barcode,
                    scanResult = createDemoShrimpCrackerResult(scannedBarcode = barcode),
                    resultSequence = it.resultSequence + 1
                )
            }
        }
    }

    private fun createDemoShrimpCrackerResult(scannedBarcode: String): ScannerResult {
        val product = ProductDummyData.demoShrimpCracker

        return ScannerResult(
            barcode = scannedBarcode,
            product = ScannedProduct(
                reportNo = "demo-shrimp-cracker",
                barcode = ProductDummyData.DEMO_SHRIMP_CRACKER_BARCODE,
                productName = product.name,
                manufacturer = product.category,
                allergy = product.allergyTags.joinToString(", "),
                nutrientText = null,
                imageUrl = null,
                source = "demo",
                rawMaterials = product.rawMaterials,
                calories = product.caloriesKcal,
                carbs = product.carbohydrateG,
                protein = product.proteinG,
                fat = product.fatG,
                sugar = 0.0,
                sodium = 230.0,
                cholesterol = null,
                allergyWarning = product.allergyTags.joinToString(", ")
            ),
            nutrientPercents = NutrientPercents(
                energyPercent = product.energyPercent?.toInt(),
                carbsPercent = product.carbohydratePercent?.toInt(),
                proteinPercent = product.proteinPercent?.toInt(),
                fatPercent = product.fatPercent?.toInt(),
                sugarPercent = 0,
                sodiumPercent = 12,
                cholesterolPercent = null
            ),
            analysis = ProductAnalysis(
                isDangerous = true,
                dangerousIngredients = product.matchedAllergyTags,
                message = ProductDummyData.DEMO_SAFETY_ANSWER
            ),
            requiresRegistration = false
        )
    }

    private companion object {
        const val TAG = "ScannerViewModel"
        const val DEMO_LOOKUP_DELAY_MS = 500L
    }
}
