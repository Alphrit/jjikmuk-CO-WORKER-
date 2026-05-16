package com.coworker.jjikmuk.feature.home

import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.PopupWindow
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.data.remote.api.ApiClient
import com.coworker.jjikmuk.data.remote.api.ProductScanResponse
import com.coworker.jjikmuk.feature.chat.ChatFragment
import com.coworker.jjikmuk.feature.scanner.BarcodeScanContract
import kotlinx.coroutines.launch

class HomeFragment : Fragment(R.layout.fragment_home) {

    private data class TempProfile(
        val name: String,
        val relation: String,
        val imageResId: Int,
        var isSelected: Boolean
    )

    private val tempProfiles = mutableListOf(
        TempProfile(
            name = "코워커",
            relation = "나",
            imageResId = R.drawable.ic_launcher_foreground,
            isSelected = true
        ),
        TempProfile(
            name = "김철수",
            relation = "배우자",
            imageResId = R.drawable.ic_launcher_foreground,
            isSelected = false
        ),
        TempProfile(
            name = "김아기",
            relation = "자녀",
            imageResId = R.drawable.ic_launcher_foreground,
            isSelected = false
        )
    )

    private lateinit var layoutSelectedProfiles: FrameLayout

    private val barcodeScannerLauncher = registerForActivityResult(BarcodeScanContract()) { barcode ->
        if (barcode == null) {
            Log.d(TAG, "Barcode scan canceled or no barcode detected.")
            Toast.makeText(requireContext(), "Barcode scan canceled.", Toast.LENGTH_SHORT).show()
            return@registerForActivityResult
        }

        Log.d(TAG, "Barcode scan completed. barcode=$barcode")
        submitBarcode(barcode)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        layoutSelectedProfiles = view.findViewById(R.id.layoutSelectedProfiles)

        val etHomeMessage = view.findViewById<EditText>(R.id.etHomeMessage)
        val btnPlus = view.findViewById<ImageButton>(R.id.btnPlus)
        val btnSend = view.findViewById<ImageButton>(R.id.btnSend)

        updateSelectedProfileImages()

        layoutSelectedProfiles.setOnClickListener {
            showScanTargetPopup(layoutSelectedProfiles)
        }

        btnPlus.setOnClickListener {
            barcodeScannerLauncher.launch(Unit)
        }

        btnSend.setOnClickListener {
            val message = etHomeMessage.text.toString().trim()

            if (message.isEmpty()) return@setOnClickListener

            etHomeMessage.text.clear()
            etHomeMessage.clearFocus()

            parentFragmentManager.beginTransaction()
                .replace(R.id.mainContainer, ChatFragment.newInstance(message))
                .addToBackStack(null)
                .commit()
        }
    }

    private fun submitBarcode(barcode: String) {
        Log.d(TAG, "Submitting scanned barcode to backend. path=/api/products/$barcode")
        Toast.makeText(requireContext(), "Scanned: $barcode", Toast.LENGTH_SHORT).show()

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val response = ApiClient.productApi.scanProduct(barcode)
                Log.d(
                    TAG,
                    "Product lookup completed. code=${response.code()} success=${response.isSuccessful}"
                )

                if (response.isSuccessful) {
                    val body = response.body()
                    Log.d(TAG, "Product lookup response. ${buildProductLog(body, barcode)}")
                    Toast.makeText(
                        requireContext(),
                        body?.data?.product?.productName ?: "Product lookup completed.",
                        Toast.LENGTH_SHORT
                    ).show()
                } else {
                    val errorBody = response.errorBody()?.string()
                    Log.d(TAG, "Product lookup failed. body=$errorBody")
                    Toast.makeText(
                        requireContext(),
                        "Product lookup failed: HTTP ${response.code()}",
                        Toast.LENGTH_SHORT
                    ).show()
                }
            } catch (exception: Exception) {
                Log.e(TAG, "Product lookup request failed.", exception)
                Toast.makeText(
                    requireContext(),
                    exception.message ?: "Product lookup failed.",
                    Toast.LENGTH_SHORT
                ).show()
            }
        }
    }

    private fun buildProductLog(response: ProductScanResponse?, fallbackBarcode: String): String {
        val product = response?.data?.product
        return "message=${response?.message}, barcode=${response?.barcode ?: fallbackBarcode}, " +
            "productName=${product?.productName}, manufacturer=${product?.manufacturer}"
    }

    private fun showScanTargetPopup(anchorView: View) {
        val popupView = layoutInflater.inflate(R.layout.dialog_scan_target, null)

        val layoutScanProfileList =
            popupView.findViewById<LinearLayout>(R.id.layoutScanProfileList)
        val btnCloseScanTarget =
            popupView.findViewById<ImageButton>(R.id.btnCloseScanTarget)

        layoutScanProfileList.removeAllViews()

        tempProfiles.forEach { profile ->
            val itemView = layoutInflater.inflate(
                R.layout.item_scan_profile,
                layoutScanProfileList,
                false
            )

            val ivProfileImage = itemView.findViewById<ImageView>(R.id.ivProfileImage)
            val tvProfileName = itemView.findViewById<TextView>(R.id.tvProfileName)
            val tvProfileRelation = itemView.findViewById<TextView>(R.id.tvProfileRelation)
            val switchProfile = itemView.findViewById<Switch>(R.id.switchProfile)

            ivProfileImage.setImageResource(profile.imageResId)
            tvProfileName.text = profile.name
            tvProfileRelation.text = profile.relation
            switchProfile.isChecked = profile.isSelected

            switchProfile.setOnCheckedChangeListener { _, isChecked ->
                profile.isSelected = isChecked
                updateSelectedProfileImages()
            }

            itemView.setOnClickListener {
                switchProfile.isChecked = !switchProfile.isChecked
            }

            layoutScanProfileList.addView(itemView)
        }

        val popupWidth = dp(240)
        val popupWindow = PopupWindow(
            popupView,
            popupWidth,
            WindowManager.LayoutParams.WRAP_CONTENT,
            true
        )

        popupWindow.isOutsideTouchable = true
        popupWindow.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        popupWindow.elevation = dp(8).toFloat()

        btnCloseScanTarget.setOnClickListener {
            popupWindow.dismiss()
        }

        popupWindow.showAsDropDown(
            anchorView,
            -popupWidth + anchorView.width,
            dp(8)
        )
    }

    private fun updateSelectedProfileImages() {
        if (!::layoutSelectedProfiles.isInitialized) return

        layoutSelectedProfiles.removeAllViews()

        val selectedProfiles = tempProfiles
            .filter { it.isSelected }
            .take(5)

        val myProfile = selectedProfiles.firstOrNull { it.relation == "나" }
        val otherProfiles = selectedProfiles.filterNot { it.relation == "나" }

        // FrameLayout은 나중에 addView 된 View가 더 위에 그려집니다.
        // 따라서 다른 가족 프로필을 먼저 그리고, 마지막에 '나' 프로필을 추가해서
        // 코워커가 항상 가장 위층에 보이도록 합니다.
        //
        // rightMargin이 작을수록 오른쪽에 붙고, 클수록 왼쪽으로 밀립니다.
        // 다른 가족들은 오른쪽에 쌓고, '나' 프로필은 가장 왼쪽/상단에 오도록 배치합니다.
        otherProfiles.asReversed().forEachIndexed { index, profile ->
            addSelectedProfileImage(
                profile = profile,
                rightMarginDp = index * 15
            )
        }

        myProfile?.let { profile ->
            addSelectedProfileImage(
                profile = profile,
                rightMarginDp = otherProfiles.size * 15
            )
        }
    }

    private fun addSelectedProfileImage(
        profile: TempProfile,
        rightMarginDp: Int
    ) {
        val imageView = ImageView(requireContext()).apply {
            layoutParams = FrameLayout.LayoutParams(
                dp(40),
                dp(40)
            ).apply {
                rightMargin = dp(rightMarginDp)
                gravity = Gravity.END or Gravity.CENTER_VERTICAL
            }

            setImageResource(profile.imageResId)
            background = ContextCompat.getDrawable(
                requireContext(),
                R.drawable.bg_profile_circle
            )
            scaleType = ImageView.ScaleType.CENTER_CROP
            setPadding(dp(2), dp(2), dp(2), dp(2))
        }

        layoutSelectedProfiles.addView(imageView)
    }

    private fun dp(value: Int): Int {
        return (value * resources.displayMetrics.density).toInt()
    }

    private companion object {
        const val TAG = "BarcodeScanFlow"
    }
}
