package com.coworker.jjikmuk.feature.product.search

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.feature.chat.adapter.RecommendProductAdapter
import com.coworker.jjikmuk.feature.product.dummy.ProductDummyData

class ProductSearchFragment : Fragment() {

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        return inflater.inflate(R.layout.fragment_product_search, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val btnProductSearchBack = view.findViewById<View>(R.id.btnProductSearchBack)
        val rvProductSearchResults =
            view.findViewById<RecyclerView>(R.id.rvProductSearchResults)

        btnProductSearchBack.setOnClickListener {
            parentFragmentManager.popBackStack()
        }

        val adapter = RecommendProductAdapter { product ->
            // 나중에 상품 상세 페이지가 생기면 여기에서 ProductDetailFragment로 이동하면 됩니다.
            // 예: ProductDetailFragment.newInstance(product.id)
        }

        rvProductSearchResults.layoutManager = LinearLayoutManager(requireContext())
        rvProductSearchResults.adapter = adapter
        adapter.submitList(ProductDummyData.recommendProducts)
    }
}