package com.coworker.frontend

import android.os.Bundle
import android.util.Log
import android.view.View
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import com.coworker.frontend.barcode.BarcodeScanContract
import com.coworker.frontend.network.ApiClient
import com.coworker.frontend.network.ProductScanResponse
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {
    private lateinit var scanButton: MaterialButton
    private lateinit var loadingProgress: ProgressBar
    private lateinit var statusText: TextView
    private lateinit var productResultText: TextView

    private val barcodeScannerLauncher = registerForActivityResult(BarcodeScanContract()) { barcode ->
        if (barcode == null) {
            Log.d(TAG, "Barcode scan canceled or no barcode detected.")
            statusText.text = "스캔이 취소되었거나 바코드를 인식하지 못했습니다."
            return@registerForActivityResult
        }

        Log.d(TAG, "Barcode scan completed. barcode=$barcode")
        statusText.text = "스캔 완료: $barcode\n제품 조회 API로 바코드 숫자를 전송합니다."
        submitBarcode(barcode)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_main)
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.main)) { v, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(systemBars.left, systemBars.top, systemBars.right, systemBars.bottom)
            insets
        }

        scanButton = findViewById(R.id.scanButton)
        loadingProgress = findViewById(R.id.loadingProgress)
        statusText = findViewById(R.id.statusText)
        productResultText = findViewById(R.id.productResultText)

        scanButton.setOnClickListener {
            barcodeScannerLauncher.launch(Unit)
        }
    }

    private fun submitBarcode(barcode: String) {
        Log.d(
            TAG,
            "Submitting scanned barcode to backend. method=GET path=/api/products/$barcode"
        )

        setLoading(true)
        lifecycleScope.launch {
            try {
                val response = ApiClient.productApi.scanProduct(barcode)
                Log.d(
                    TAG,
                    "Backend product lookup request completed. code=${response.code()} success=${response.isSuccessful}"
                )
                if (response.isSuccessful) {
                    val body = response.body()
                    Log.d(
                        TAG,
                        "Backend product lookup response body. message=${body?.message} barcode=${body?.barcode}"
                    )
                    statusText.text = buildStatusText(body, barcode)
                    productResultText.text = buildProductResultText(body)
                } else {
                    val errorBody = response.errorBody()?.string()
                    Log.d(TAG, "Backend product lookup request failed with HTTP error. body=$errorBody")
                    statusText.text = "서버 응답 실패: HTTP ${response.code()}"
                    productResultText.text = errorBody ?: "오류 응답 본문이 없습니다."
                }
            } catch (exception: Exception) {
                Log.e(TAG, "Backend product lookup request attempt failed.", exception)
                statusText.text = "API 호출 실패"
                productResultText.text = exception.message ?: "알 수 없는 오류가 발생했습니다."
            } finally {
                setLoading(false)
            }
        }
    }

    private fun buildStatusText(response: ProductScanResponse?, fallbackBarcode: String): String {
        return buildString {
            appendLine(response?.message ?: "응답 메시지가 없습니다.")
            append("전송 바코드: ${response?.barcode ?: fallbackBarcode}")
        }
    }

    private fun buildProductResultText(response: ProductScanResponse?): String {
        val product = response?.data?.product
        if (product == null) {
            return "제품 정보를 찾지 못했습니다."
        }

        return buildString {
            appendLine("제품명: ${product.productName ?: "-"}")
            appendLine("제조사: ${product.manufacturer ?: "-"}")
            appendLine("바코드: ${product.barcode ?: "-"}")
            appendLine("알레르기 정보: ${product.allergy ?: "-"}")
            appendLine("위험 여부: ${if (response.data.analysis?.isDangerous == true) "주의" else "안전"}")
            appendLine("칼로리: ${product.calories ?: "-"}")
            appendLine("당류: ${product.sugar ?: "-"}")
            append("나트륨: ${product.sodium ?: "-"}")
        }
    }

    private fun setLoading(isLoading: Boolean) {
        loadingProgress.visibility = if (isLoading) View.VISIBLE else View.GONE
        scanButton.isEnabled = !isLoading
    }

    private companion object {
        const val TAG = "BarcodeScanFlow"
    }
}
