package com.coworker.jjikmuk.feature.history

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.fragment.app.viewModels
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.feature.navigation.BottomNavController
import com.coworker.jjikmuk.feature.product.adapter.RecommendProductAdapter
import com.coworker.jjikmuk.feature.product.detail.ProductDetailFragment
import com.coworker.jjikmuk.feature.product.mapper.toUiModel
import kotlinx.coroutines.launch

class HistoryFragment : Fragment() {

    private val viewModel: HistoryViewModel by viewModels()

    private lateinit var favoriteAdapter: RecommendProductAdapter
    private lateinit var rvFavoriteProducts: RecyclerView
    private lateinit var tvEmptyFavoriteProducts: TextView

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        return inflater.inflate(R.layout.fragment_history, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        initViews(view)
        setupRecyclerView()
        setupClickListeners(view)
        observeViewModel()

        viewModel.loadFavoriteProducts()
    }

    override fun onResume() {
        super.onResume()
        viewModel.loadFavoriteProducts()
    }

    private fun initViews(view: View) {
        val btnHistoryBack = view.findViewById<ImageButton>(R.id.btnHistoryBack)

        rvFavoriteProducts = view.findViewById(R.id.rvFavoriteProducts)
        tvEmptyFavoriteProducts = view.findViewById(R.id.tvEmptyFavoriteProducts)

        btnHistoryBack.setOnClickListener {
            parentFragmentManager.popBackStack()
        }
    }

    private fun setupRecyclerView() {
        favoriteAdapter = RecommendProductAdapter { product ->
            parentFragmentManager.beginTransaction()
                .replace(R.id.mainContainer, ProductDetailFragment.newInstance(product.id))
                .addToBackStack(null)
                .commit()
        }

        rvFavoriteProducts.layoutManager = LinearLayoutManager(requireContext())
        rvFavoriteProducts.adapter = favoriteAdapter
        rvFavoriteProducts.isNestedScrollingEnabled = false
    }

    private fun setupClickListeners(view: View) {
        BottomNavController.bind(view, parentFragmentManager, requireContext())
    }

    private fun observeViewModel() {
        viewLifecycleOwner.lifecycleScope.launch {
            viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                viewModel.uiState.collect { state ->
                    val favoriteProductUiModels = state.favoriteProducts.map { product ->
                        product.toUiModel()
                    }

                    favoriteAdapter.submitList(favoriteProductUiModels)

                    tvEmptyFavoriteProducts.visibility = if (state.favoriteProducts.isEmpty()) {
                        View.VISIBLE
                    } else {
                        View.GONE
                    }
                }
            }
        }
    }
}