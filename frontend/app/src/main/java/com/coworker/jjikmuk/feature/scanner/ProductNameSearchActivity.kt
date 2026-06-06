package com.coworker.jjikmuk.feature.scanner

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.EditText
import android.widget.ImageButton
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.addTextChangedListener
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.data.repository.ProductRepositoryImpl
import com.coworker.jjikmuk.domain.model.Product
import com.coworker.jjikmuk.feature.product.adapter.RecommendProductAdapter
import com.coworker.jjikmuk.feature.product.mapper.toUiModel
import com.coworker.jjikmuk.feature.product.model.ProductUiModel

class ProductNameSearchActivity : AppCompatActivity() {
    private val productRepository = ProductRepositoryImpl()
    private val allProducts: List<Product> by lazy { productRepository.getAllProducts() }

    private lateinit var searchInput: EditText
    private lateinit var metaText: TextView
    private lateinit var emptyText: TextView
    private lateinit var confirmButton: TextView
    private lateinit var adapter: RecommendProductAdapter

    private var selectedProduct: ProductUiModel? = null
    private var scannedBarcode: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_product_name_search)

        scannedBarcode = intent.getStringExtra(EXTRA_BARCODE)
        searchInput = findViewById(R.id.etProductNameSearch)
        metaText = findViewById(R.id.tvProductNameSearchMeta)
        emptyText = findViewById(R.id.tvProductNameSearchEmpty)
        confirmButton = findViewById(R.id.btnProductNameSearchConfirm)
        adapter = RecommendProductAdapter { product ->
            selectedProduct = product
            updateConfirmButton()
            Toast.makeText(this, "${product.name} 선택됨", Toast.LENGTH_SHORT).show()
        }

        findViewById<ImageButton>(R.id.btnProductNameSearchBack).setOnClickListener {
            setResult(Activity.RESULT_CANCELED)
            finish()
        }

        findViewById<RecyclerView>(R.id.rvProductNameSearchResults).apply {
            layoutManager = LinearLayoutManager(this@ProductNameSearchActivity)
            adapter = this@ProductNameSearchActivity.adapter
        }

        confirmButton.setOnClickListener {
            val product = selectedProduct ?: return@setOnClickListener
            val intent = Intent(this, ProductPhotoCaptureActivity::class.java).apply {
                putExtra(ProductPhotoCaptureActivity.EXTRA_BARCODE, scannedBarcode)
                putExtra(ProductPhotoCaptureActivity.EXTRA_PRODUCT_ID, product.id)
                putExtra(ProductPhotoCaptureActivity.EXTRA_PRODUCT_NAME, product.name)
            }
            startActivity(intent)
        }

        searchInput.addTextChangedListener { text ->
            selectedProduct = null
            updateResults(text?.toString().orEmpty())
        }
        searchInput.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_SEARCH) {
                updateResults(searchInput.text?.toString().orEmpty())
                true
            } else {
                false
            }
        }

        updateResults("")
        searchInput.requestFocus()
    }

    private fun updateResults(query: String) {
        val normalizedQuery = query.trim()
        val products = if (normalizedQuery.isBlank()) {
            emptyList()
        } else {
            allProducts.filter { product ->
                product.name.contains(normalizedQuery, ignoreCase = true) ||
                    product.category.contains(normalizedQuery, ignoreCase = true) ||
                    product.allergyTags.any { tag -> tag.contains(normalizedQuery, ignoreCase = true) }
            }
        }
        val uiModels = products.map { it.toUiModel() }

        adapter.submitList(uiModels)
        metaText.text = if (normalizedQuery.isBlank()) {
            "검색어를 입력하면 후보 제품이 표시됩니다."
        } else {
            "검색 결과 ${uiModels.size}개"
        }
        emptyText.visibility = if (normalizedQuery.isNotBlank() && uiModels.isEmpty()) {
            View.VISIBLE
        } else {
            View.GONE
        }
        updateConfirmButton()
    }

    private fun updateConfirmButton() {
        val enabled = selectedProduct != null
        confirmButton.isEnabled = enabled
        confirmButton.alpha = if (enabled) 1f else DISABLED_BUTTON_ALPHA
    }

    companion object {
        const val EXTRA_BARCODE = "extra_barcode"
        private const val DISABLED_BUTTON_ALPHA = 0.45f
    }
}
