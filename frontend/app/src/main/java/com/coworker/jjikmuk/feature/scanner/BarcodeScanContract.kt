package com.coworker.jjikmuk.feature.scanner

import android.app.Activity
import android.content.Context
import android.content.Intent
import androidx.activity.result.contract.ActivityResultContract

object BarcodeScanResult {
    const val EXTRA_BARCODE = "com.coworker.jjikmuk.extra.BARCODE"
}

class BarcodeScanContract : ActivityResultContract<Unit, String?>() {
    override fun createIntent(context: Context, input: Unit): Intent {
        return Intent(context, BarcodeScannerActivity::class.java)
    }

    override fun parseResult(resultCode: Int, intent: Intent?): String? {
        if (resultCode != Activity.RESULT_OK) return null
        return intent?.getStringExtra(BarcodeScanResult.EXTRA_BARCODE)
    }
}
