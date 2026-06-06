package com.coworker.jjikmuk

import android.content.Intent
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.coworker.jjikmuk.feature.home.HomeFragment
import com.coworker.jjikmuk.feature.product.detail.ProductDetailFragment
import dagger.hilt.android.AndroidEntryPoint

@AndroidEntryPoint
class MainActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContentView(R.layout.activity_main)
        showSystemBarsConsistently()
        applySystemBarInsets()

        if (intent.getBooleanExtra(EXTRA_SHOW_PRODUCT_DETAIL, false)) {
            showProductDetail(intent)
        } else if (savedInstanceState == null) {
            showHome()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (intent.getBooleanExtra(EXTRA_SHOW_HOME, false)) {
            showHome()
        } else if (intent.getBooleanExtra(EXTRA_SHOW_PRODUCT_DETAIL, false)) {
            showProductDetail(intent)
        }
    }

    private fun showHome() {
        supportFragmentManager.popBackStack(
            null,
            androidx.fragment.app.FragmentManager.POP_BACK_STACK_INCLUSIVE
        )
        supportFragmentManager.beginTransaction()
            .replace(R.id.mainContainer, HomeFragment())
            .commit()
    }

    private fun showProductDetail(intent: Intent) {
        val productId = intent.getStringExtra(EXTRA_PRODUCT_ID).orEmpty()
        supportFragmentManager.popBackStack(
            null,
            androidx.fragment.app.FragmentManager.POP_BACK_STACK_INCLUSIVE
        )
        supportFragmentManager.beginTransaction()
            .replace(
                R.id.mainContainer,
                ProductDetailFragment.newInstance(
                    productId = productId,
                    safetyAnswer = intent.getStringExtra(EXTRA_SAFETY_ANSWER),
                    riskLevel = intent.getStringExtra(EXTRA_RISK_LEVEL)
                )
            )
            .commit()
    }

    private fun applySystemBarInsets() {
        val mainContainer = findViewById<View>(R.id.mainContainer)
        ViewCompat.setOnApplyWindowInsetsListener(mainContainer) { view, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(
                systemBars.left,
                systemBars.top,
                systemBars.right,
                systemBars.bottom
            )
            insets
        }
    }

    private fun showSystemBarsConsistently() {
        window.clearFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)

        window.statusBarColor = Color.WHITE
        window.navigationBarColor = Color.WHITE

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.setDecorFitsSystemWindows(true)

            window.decorView.post {
                window.insetsController?.show(
                    WindowInsets.Type.statusBars() or WindowInsets.Type.navigationBars()
                )

                window.insetsController?.setSystemBarsAppearance(
                    WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS,
                    WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                )
            }
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
        }
    }

    companion object {
        const val EXTRA_SHOW_HOME = "extra_show_home"
        const val EXTRA_SHOW_PRODUCT_DETAIL = "extra_show_product_detail"
        const val EXTRA_PRODUCT_ID = "extra_product_id"
        const val EXTRA_SAFETY_ANSWER = "extra_safety_answer"
        const val EXTRA_RISK_LEVEL = "extra_risk_level"
    }
}
