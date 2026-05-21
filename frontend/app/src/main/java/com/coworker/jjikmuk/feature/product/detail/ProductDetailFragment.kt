package com.coworker.jjikmuk.feature.product.detail

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.fragment.app.viewModels
import androidx.lifecycle.lifecycleScope
import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.feature.navigation.BottomNavController
import com.coworker.jjikmuk.domain.model.Product
import com.coworker.jjikmuk.feature.product.mapper.toUiModel
import kotlinx.coroutines.launch

class ProductDetailFragment : Fragment() {

    private val viewModel: ProductDetailViewModel by viewModels()

    private lateinit var btnProductFavorite: ImageButton
    private lateinit var ivProductDetailImage: ImageView
    private lateinit var tvProductDetailCategory: TextView
    private lateinit var tvProductDetailName: TextView
    private lateinit var tvDetailAllergyTag1: TextView
    private lateinit var tvDetailAllergyTag2: TextView

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        return inflater.inflate(R.layout.fragment_product_detail, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        initViews(view)
        setupClickListeners(view)
        observeViewModel()

        val productId = arguments?.getString(ARG_PRODUCT_ID).orEmpty()
        viewModel.loadProduct(productId)
    }

    private fun initViews(view: View) {
        val btnProductDetailBack = view.findViewById<ImageButton>(R.id.btnProductDetailBack)

        btnProductFavorite = view.findViewById(R.id.btnProductFavorite)
        ivProductDetailImage = view.findViewById(R.id.ivProductDetailImage)
        tvProductDetailCategory = view.findViewById(R.id.tvProductDetailCategory)
        tvProductDetailName = view.findViewById(R.id.tvProductDetailName)
        tvDetailAllergyTag1 = view.findViewById(R.id.tvDetailAllergyTag1)
        tvDetailAllergyTag2 = view.findViewById(R.id.tvDetailAllergyTag2)

        btnProductDetailBack.setOnClickListener {
            parentFragmentManager.popBackStack()
        }
    }

    private fun setupClickListeners(view: View) {
        btnProductFavorite.setOnClickListener {
            viewModel.toggleFavorite()
        }

        BottomNavController.bind(view, parentFragmentManager, requireContext())
    }

    private fun observeViewModel() {
        viewLifecycleOwner.lifecycleScope.launch {
            viewModel.uiState.collect { state ->
                renderProduct(state.product)
                renderFavoriteIcon(state.isFavorite)
            }
        }
    }

    private fun renderProduct(product: Product?) {
        if (product == null) {
            tvProductDetailCategory.text = "상품 정보 없음"
            tvProductDetailName.text = "상품을 찾을 수 없습니다."
            tvDetailAllergyTag1.visibility = View.GONE
            tvDetailAllergyTag2.visibility = View.GONE
            btnProductFavorite.visibility = View.GONE
            return
        }

        val productUiModel = product.toUiModel()

        btnProductFavorite.visibility = View.VISIBLE
        ivProductDetailImage.setImageResource(productUiModel.imageResId)
        tvProductDetailCategory.text = productUiModel.category
        tvProductDetailName.text = productUiModel.name
        renderAllergyTags(productUiModel.allergyTags)
    }

    private fun renderAllergyTags(allergyTags: List<String>) {
        val firstTag = allergyTags.getOrNull(0)
        val secondTag = allergyTags.getOrNull(1)

        if (firstTag != null) {
            tvDetailAllergyTag1.visibility = View.VISIBLE
            tvDetailAllergyTag1.text = firstTag
        } else {
            tvDetailAllergyTag1.visibility = View.GONE
        }

        if (secondTag != null) {
            tvDetailAllergyTag2.visibility = View.VISIBLE
            tvDetailAllergyTag2.text = secondTag
        } else {
            tvDetailAllergyTag2.visibility = View.GONE
        }
    }

    private fun renderFavoriteIcon(isFavorite: Boolean) {
        val iconResId = if (isFavorite) {
            R.drawable.ic_heart_filled
        } else {
            R.drawable.ic_heart_outline
        }

        btnProductFavorite.setImageResource(iconResId)
    }

    companion object {
        private const val ARG_PRODUCT_ID = "product_id"

        fun newInstance(productId: String): ProductDetailFragment {
            return ProductDetailFragment().apply {
                arguments = Bundle().apply {
                    putString(ARG_PRODUCT_ID, productId)
                }
            }
        }
    }
}