package com.coworker.frontend

import android.os.Bundle
import android.widget.Button
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.coworker.frontend.barcode.BarcodeScanContract

class MainActivity : AppCompatActivity() {
    private val barcodeScannerLauncher = registerForActivityResult(BarcodeScanContract()) { barcode ->
        if (barcode != null) {
            Toast.makeText(this, "스캔 완료! 바코드: $barcode", Toast.LENGTH_LONG).show()

            // 나중에 여기에 API (GET /api/products/{barcode}) 호출 코드가 들어감
        } else {
            Toast.makeText(this, "스캔이 취소되었습니다.", Toast.LENGTH_SHORT).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val scanButton = findViewById<Button>(R.id.btn_scan)
        scanButton.setOnClickListener {
            barcodeScannerLauncher.launch(Unit)
        }
    }
}