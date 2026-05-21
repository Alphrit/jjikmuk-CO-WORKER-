package com.coworker.jjikmuk.feature.home

import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
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
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.fragment.app.viewModels
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.feature.chat.ChatFragment
import com.coworker.jjikmuk.feature.navigation.BottomNavController
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch

@AndroidEntryPoint
class HomeFragment : Fragment(R.layout.fragment_home) {

    private val viewModel: HomeViewModel by viewModels()

    private lateinit var layoutSelectedProfiles: FrameLayout
    private lateinit var etHomeMessage: EditText
    private var currentProfiles: List<HomeProfileUiModel> = emptyList()

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        layoutSelectedProfiles = view.findViewById(R.id.layoutSelectedProfiles)
        etHomeMessage = view.findViewById(R.id.etHomeMessage)
        val btnSend = view.findViewById<ImageButton>(R.id.btnSend)

        observeViewModel()
        setupInputWatcher()

        BottomNavController.bind(view, parentFragmentManager, requireContext())

        layoutSelectedProfiles.setOnClickListener {
            showScanTargetPopup(layoutSelectedProfiles)
        }

        btnSend.setOnClickListener {
            val message = viewModel.getCurrentMessage()

            if (message.isEmpty()) return@setOnClickListener

            etHomeMessage.text.clear()
            etHomeMessage.clearFocus()
            viewModel.clearInputMessage()

            parentFragmentManager.beginTransaction()
                .replace(R.id.mainContainer, ChatFragment.newInstance(message))
                .addToBackStack(null)
                .commit()
        }
    }

    private fun observeViewModel() {
        viewLifecycleOwner.lifecycleScope.launch {
            viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                viewModel.uiState.collect { state ->
                    currentProfiles = state.profiles
                    updateSelectedProfileImages(state.selectedProfiles)
                }
            }
        }
    }

    private fun setupInputWatcher() {
        etHomeMessage.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(
                s: CharSequence?,
                start: Int,
                count: Int,
                after: Int
            ) = Unit

            override fun onTextChanged(
                s: CharSequence?,
                start: Int,
                before: Int,
                count: Int
            ) {
                viewModel.updateInputMessage(s?.toString().orEmpty())
            }

            override fun afterTextChanged(s: Editable?) = Unit
        })
    }

    private fun showScanTargetPopup(anchorView: View) {
        val popupView = layoutInflater.inflate(R.layout.dialog_scan_target, null)

        val layoutScanProfileList =
            popupView.findViewById<LinearLayout>(R.id.layoutScanProfileList)
        val btnCloseScanTarget =
            popupView.findViewById<ImageButton>(R.id.btnCloseScanTarget)

        layoutScanProfileList.removeAllViews()

        currentProfiles.forEach { profile ->
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
            tvProfileRelation.text = profile.relationText
            switchProfile.isChecked = profile.isSelected

            switchProfile.setOnCheckedChangeListener { _, _ ->
                viewModel.toggleProfile(profile.id)
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

    private fun updateSelectedProfileImages(selectedProfiles: List<HomeProfileUiModel>) {
        if (!::layoutSelectedProfiles.isInitialized) return

        layoutSelectedProfiles.removeAllViews()

        val displayProfiles = selectedProfiles.take(5)
        val myProfile = displayProfiles.firstOrNull { profile -> profile.id == "me" }
        val otherProfiles = displayProfiles.filterNot { profile -> profile.id == "me" }

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
        profile: HomeProfileUiModel,
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
}
