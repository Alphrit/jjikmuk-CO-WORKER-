package com.coworker.jjikmuk.feature.product.detail

import android.os.Bundle
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.fragment.app.viewModels
import androidx.lifecycle.lifecycleScope
import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.feature.navigation.BottomNavController
import com.coworker.jjikmuk.domain.model.Product
import com.coworker.jjikmuk.feature.product.mapper.toUiModel
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch

@AndroidEntryPoint
class ProductDetailFragment : Fragment() {

    private val viewModel: ProductDetailViewModel by viewModels()

    private lateinit var btnProductFavorite: ImageButton
    private lateinit var ivProductDetailImage: ImageView
    private lateinit var tvProductDetailCategory: TextView
    private lateinit var tvProductDetailName: TextView
    private lateinit var layoutAllergyTags: LinearLayout
    private lateinit var layoutSafetyWarning: LinearLayout
    private lateinit var layoutSafetyIconBox: View
    private lateinit var tvSafetyIcon: TextView
    private lateinit var tvSafetyTitle: TextView
    private lateinit var tvSafetyDescription: TextView
    private lateinit var viewNutritionDonutChart: NutritionDonutChartView
    private lateinit var tvCarbohydratePercent: TextView
    private lateinit var tvProteinPercent: TextView
    private lateinit var tvFatPercent: TextView
    private lateinit var tvTotalCalories: TextView
    private lateinit var tvProtein: TextView
    private lateinit var tvFat: TextView
    private lateinit var tvCarbohydrate: TextView
    private lateinit var tvIngredientDescription: TextView

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
        viewModel.setSafetyResult(
            answer = arguments?.getString(ARG_SAFETY_ANSWER),
            riskLevel = arguments?.getString(ARG_RISK_LEVEL)
        )
        viewModel.loadProduct(productId)
    }

    private fun initViews(view: View) {
        val btnProductDetailBack = view.findViewById<ImageButton>(R.id.btnProductDetailBack)

        btnProductFavorite = view.findViewById(R.id.btnProductFavorite)
        ivProductDetailImage = view.findViewById(R.id.ivProductDetailImage)
        tvProductDetailCategory = view.findViewById(R.id.tvProductDetailCategory)
        tvProductDetailName = view.findViewById(R.id.tvProductDetailName)
        layoutAllergyTags = view.findViewById(R.id.layoutAllergyTags)
        layoutSafetyWarning = view.findViewById(R.id.layoutSafetyWarning)
        layoutSafetyIconBox = view.findViewById(R.id.layoutSafetyIconBox)
        tvSafetyIcon = view.findViewById(R.id.tvSafetyIcon)
        tvSafetyTitle = view.findViewById(R.id.tvSafetyTitle)
        tvSafetyDescription = view.findViewById(R.id.tvSafetyDescription)
        viewNutritionDonutChart = view.findViewById(R.id.viewNutritionDonutChart)
        tvCarbohydratePercent = view.findViewById(R.id.tvCarbohydratePercent)
        tvProteinPercent = view.findViewById(R.id.tvProteinPercent)
        tvFatPercent = view.findViewById(R.id.tvFatPercent)
        tvTotalCalories = view.findViewById(R.id.tvTotalCalories)
        tvProtein = view.findViewById(R.id.tvProtein)
        tvFat = view.findViewById(R.id.tvFat)
        tvCarbohydrate = view.findViewById(R.id.tvCarbohydrate)
        tvIngredientDescription = view.findViewById(R.id.tvIngredientDescription)

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
                renderProduct(state.product, state.safetyAnswer, state.riskLevel)
                renderFavoriteIcon(state.isFavorite)
            }
        }
    }

    private fun renderProduct(product: Product?, safetyAnswer: String?, riskLevel: String?) {
        if (product == null) {
            tvProductDetailCategory.text = "상품 정보 없음"
            tvProductDetailName.text = "상품을 찾을 수 없습니다."
            layoutAllergyTags.visibility = View.GONE
            layoutSafetyWarning.visibility = View.GONE
            btnProductFavorite.visibility = View.GONE
            return
        }

        val productUiModel = product.toUiModel()

        btnProductFavorite.visibility = View.VISIBLE
        ivProductDetailImage.setImageResource(productUiModel.imageResId)
        tvProductDetailCategory.text = productUiModel.category
        tvProductDetailName.text = productUiModel.name
        renderAllergyTags(
            matchedAllergyTags = product.matchedAllergyTags,
            allAllergyTags = productUiModel.allergyTags
        )
        renderSafetyResult(product, safetyAnswer, riskLevel)
        renderNutrition(product)
        tvIngredientDescription.text = product.rawMaterials.orEmpty()
            .ifBlank { "원재료 정보가 없습니다." }
    }

    private fun renderAllergyTags(
        matchedAllergyTags: List<String>,
        allAllergyTags: List<String>
    ) {
        layoutAllergyTags.removeAllViews()
        val displayTags = matchedAllergyTags.ifEmpty { allAllergyTags }

        if (displayTags.isEmpty()) {
            layoutAllergyTags.visibility = View.VISIBLE
            layoutAllergyTags.addView(createAllergyTagView("나와 매칭되는 알레르기 성분 없음"))
            return
        }

        layoutAllergyTags.visibility = View.VISIBLE
        displayTags.take(MAX_ALLERGY_TAG_COUNT).forEach { allergyTag ->
            layoutAllergyTags.addView(createAllergyTagView(allergyTag))
        }
    }

    private fun renderSafetyResult(
        product: Product,
        safetyAnswer: String?,
        riskLevel: String?
    ) {
        val style = RiskStyle.from(riskLevel)
        val matchedAllergyText = product.matchedAllergyTags.joinToString(", ")
        val fallbackAnswer = if (matchedAllergyText.isBlank()) {
            "현재 나와 매칭되는 알레르기 성분은 없습니다."
        } else {
            "나와 매칭되는 주의 성분: $matchedAllergyText"
        }

        layoutSafetyWarning.visibility = View.VISIBLE
        layoutSafetyWarning.background = createRoundedBackground(style.backgroundColor)
        layoutSafetyIconBox.background = createOvalBackground(style.iconBackgroundColor)
        tvSafetyIcon.text = style.emoji
        tvSafetyIcon.setTextColor(style.textColor)
        tvSafetyTitle.text = style.title
        tvSafetyTitle.setTextColor(style.textColor)
        tvSafetyDescription.text = (safetyAnswer ?: fallbackAnswer).toSingleLineText()
        tvSafetyDescription.setTextColor(style.textColor)
    }

    private fun renderNutrition(product: Product) {
        val carbohydrateMacroPercent = product.carbohydrateMacroPercent
        val proteinMacroPercent = product.proteinMacroPercent
        val fatMacroPercent = product.fatMacroPercent

        viewNutritionDonutChart.setPercents(
            carbohydratePercent = carbohydrateMacroPercent,
            proteinPercent = proteinMacroPercent,
            fatPercent = fatMacroPercent
        )

        tvCarbohydratePercent.text = formatPercent(carbohydrateMacroPercent)
        tvProteinPercent.text = formatPercent(proteinMacroPercent)
        tvFatPercent.text = formatPercent(fatMacroPercent)
        tvTotalCalories.text = formatNutrient(
            value = product.caloriesKcal,
            unit = "kcal",
            percent = product.energyPercent
        )
        tvProtein.text = formatNutrient(product.proteinG, "g", product.proteinPercent)
        tvFat.text = formatNutrient(product.fatG, "g", product.fatPercent)
        tvCarbohydrate.text = formatNutrient(
            value = product.carbohydrateG,
            unit = "g",
            percent = product.carbohydratePercent
        )
    }

    private fun createAllergyTagView(text: String): TextView {
        return TextView(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                requireContext().dpToPx(28)
            ).apply {
                marginEnd = requireContext().dpToPx(8)
            }
            background = requireContext().getDrawable(R.drawable.bg_recommend_tag)
            gravity = android.view.Gravity.CENTER
            setPadding(
                requireContext().dpToPx(12),
                0,
                requireContext().dpToPx(12),
                0
            )
            setTextColor(Color.parseColor("#E84242"))
            textSize = 12f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            this.text = text
        }
    }

    private fun createRoundedBackground(color: Int): GradientDrawable {
        return GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = requireContext().dpToPx(8).toFloat()
            setColor(color)
        }
    }

    private fun createOvalBackground(color: Int): GradientDrawable {
        return GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(color)
        }
    }

    private fun formatPercent(value: Double?): String {
        return if (value == null) "-" else "${value.toInt()}%"
    }

    private fun formatNutrient(value: Double?, unit: String, percent: Long?): String {
        if (value == null) return "-"
        val displayValue = if (value % 1.0 == 0.0) {
            value.toInt().toString()
        } else {
            String.format("%.1f", value)
        }
        val percentText = percent?.let { " (${it}%)" }.orEmpty()
        return "$displayValue $unit$percentText"
    }

    private fun String.toSingleLineText(): String {
        return trim()
            .replace(Regex("\\s*\\n\\s*"), " ")
            .replace(Regex("\\s{2,}"), " ")
    }

    private fun android.content.Context.dpToPx(dp: Int): Int {
        return (dp * resources.displayMetrics.density).toInt()
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
        private const val ARG_SAFETY_ANSWER = "safety_answer"
        private const val ARG_RISK_LEVEL = "risk_level"
        private const val MAX_ALLERGY_TAG_COUNT = 8

        fun newInstance(
            productId: String,
            safetyAnswer: String? = null,
            riskLevel: String? = null
        ): ProductDetailFragment {
            return ProductDetailFragment().apply {
                arguments = Bundle().apply {
                    putString(ARG_PRODUCT_ID, productId)
                    putString(ARG_SAFETY_ANSWER, safetyAnswer)
                    putString(ARG_RISK_LEVEL, riskLevel)
                }
            }
        }
    }

    private data class RiskStyle(
        val title: String,
        val emoji: String,
        val backgroundColor: Int,
        val iconBackgroundColor: Int,
        val textColor: Int
    ) {
        companion object {
            fun from(riskLevel: String?): RiskStyle {
                return when (riskLevel?.lowercase()) {
                    "high" -> RiskStyle(
                        title = "위험도가 높아요",
                        emoji = "☹",
                        backgroundColor = Color.parseColor("#FFF0F0"),
                        iconBackgroundColor = Color.parseColor("#FFE0E0"),
                        textColor = Color.parseColor("#9F3F3D")
                    )

                    "middle", "medium" -> RiskStyle(
                        title = "주의가 필요해요",
                        emoji = "😐",
                        backgroundColor = Color.parseColor("#FFF8E1"),
                        iconBackgroundColor = Color.parseColor("#FFE9A8"),
                        textColor = Color.parseColor("#8A6500")
                    )

                    else -> RiskStyle(
                        title = "비교적 안전해요",
                        emoji = "☺",
                        backgroundColor = Color.parseColor("#ECFFF7"),
                        iconBackgroundColor = Color.parseColor("#D7F7EB"),
                        textColor = Color.parseColor("#007A5E")
                    )
                }
            }
        }
    }
}
