package com.coworker.jjikmuk.feature.chat

import android.content.Context
import android.view.LayoutInflater
import android.view.View
import android.widget.TextView
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.feature.product.adapter.RecommendProductAdapter
import com.coworker.jjikmuk.feature.product.model.ProductUiModel
import com.google.android.material.bottomsheet.BottomSheetDialog

class RecommendProductBottomSheet(
    private val context: Context,
    private val layoutInflater: LayoutInflater,
    private val products: List<ProductUiModel>,
    private val onProductClick: (ProductUiModel) -> Unit,
    private val onMoreClick: () -> Unit
) {
    fun show() {
        val dialog = BottomSheetDialog(context)

        val bottomSheetView = layoutInflater.inflate(
            R.layout.bottom_sheet_recommend_products,
            null
        )

        val rvRecommendProducts =
            bottomSheetView.findViewById<RecyclerView>(R.id.rvRecommendProducts)

        val btnMoreProducts =
            bottomSheetView.findViewById<TextView>(R.id.btnMoreProducts)

        val adapter = RecommendProductAdapter { product ->
            dialog.dismiss()
            onProductClick(product)
        }

        rvRecommendProducts.layoutManager = LinearLayoutManager(bottomSheetView.context)
        rvRecommendProducts.adapter = adapter
        rvRecommendProducts.isNestedScrollingEnabled = false
        adapter.submitList(products)

        btnMoreProducts.setOnClickListener {
            dialog.dismiss()
            onMoreClick()
        }

        dialog.setContentView(bottomSheetView)
        dialog.show()
    }
}