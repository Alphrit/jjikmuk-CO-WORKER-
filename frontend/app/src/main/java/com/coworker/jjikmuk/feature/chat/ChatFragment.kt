package com.coworker.jjikmuk.feature.chat

import android.os.Bundle
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.widget.EditText
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.feature.chat.adapter.RecommendProductAdapter
import com.coworker.jjikmuk.feature.product.dummy.ProductDummyData
import com.coworker.jjikmuk.feature.product.search.ProductSearchFragment
import com.google.android.material.bottomsheet.BottomSheetDialog

class ChatFragment : Fragment() {

    private lateinit var scrollChatMessages: ScrollView
    private lateinit var layoutChatMessages: LinearLayout
    private lateinit var etChatMessage: EditText
    private lateinit var btnChatSend: ImageButton

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        return inflater.inflate(R.layout.fragment_chat, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        scrollChatMessages = view.findViewById(R.id.scrollChatMessages)
        layoutChatMessages = view.findViewById(R.id.layoutChatMessages)
        etChatMessage = view.findViewById(R.id.etChatMessage)
        btnChatSend = view.findViewById(R.id.btnChatSend)

        val btnChatBack = view.findViewById<ImageButton>(R.id.btnChatBack)
        val tvChatTitle = view.findViewById<TextView>(R.id.tvChatTitle)

        btnChatBack.setOnClickListener {
            parentFragmentManager.popBackStack()
        }

        val initialMessage = arguments?.getString(ARG_INITIAL_MESSAGE).orEmpty()

        if (initialMessage.isNotBlank()) {
            tvChatTitle.text = makeTitle(initialMessage)
            addUserMessage(initialMessage)
            addBotMessage(makeDummyResponse(initialMessage))

            scrollChatMessages.postDelayed({
                showRecommendProductBottomSheet()
            }, 500)
        }

        btnChatSend.setOnClickListener {
            sendCurrentMessage()
        }

        etChatMessage.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_SEND) {
                sendCurrentMessage()
                true
            } else {
                false
            }
        }
    }

    private fun sendCurrentMessage() {
        val message = etChatMessage.text.toString().trim()

        if (message.isEmpty()) return

        addUserMessage(message)
        etChatMessage.text.clear()

        addBotMessage(makeDummyResponse(message))

        scrollChatMessages.postDelayed({
            showRecommendProductBottomSheet()
        }, 500)
    }

    private fun addUserMessage(message: String) {
        val row = LinearLayout(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply {
                topMargin = dp(12)
            }
            gravity = Gravity.END
            orientation = LinearLayout.HORIZONTAL
        }

        val bubble = TextView(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                (resources.displayMetrics.widthPixels * 0.68f).toInt(),
                LinearLayout.LayoutParams.WRAP_CONTENT
            )
            background = requireContext().getDrawable(R.drawable.bg_chat_user)
            gravity = Gravity.CENTER
            minHeight = dp(44)
            setPadding(dp(18), dp(10), dp(18), dp(10))
            text = message
            setTextColor(0xFFFFFFFF.toInt())
            textSize = 12f
        }

        row.addView(bubble)
        layoutChatMessages.addView(row)
        scrollToBottom()
    }

    private fun addBotMessage(message: String) {
        val row = LinearLayout(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply {
                topMargin = dp(20)
            }
            gravity = Gravity.START
            orientation = LinearLayout.HORIZONTAL
        }

        val dot = View(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(dp(18), dp(18)).apply {
                rightMargin = dp(8)
                topMargin = dp(26)
            }
            background = requireContext().getDrawable(R.drawable.bg_camera_circle)
            alpha = 0.35f
        }

        val bubble = TextView(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                (resources.displayMetrics.widthPixels * 0.62f).toInt(),
                LinearLayout.LayoutParams.WRAP_CONTENT
            )
            background = requireContext().getDrawable(R.drawable.bg_chat_bot)
            minHeight = dp(44)
            setPadding(dp(16), dp(12), dp(16), dp(12))
            text = message
            setTextColor(0xFF555555.toInt())
            textSize = 12f
        }

        row.addView(dot)
        row.addView(bubble)
        layoutChatMessages.addView(row)
        scrollToBottom()
    }

    private fun showRecommendProductBottomSheet() {
        val dialog = BottomSheetDialog(requireContext())

        val bottomSheetView = layoutInflater.inflate(
            R.layout.bottom_sheet_recommend_products,
            null
        )

        val rvRecommendProducts =
            bottomSheetView.findViewById<RecyclerView>(R.id.rvRecommendProducts)

        val btnMoreProducts =
            bottomSheetView.findViewById<TextView>(R.id.btnMoreProducts)

        val adapter = RecommendProductAdapter { product ->
            // 나중에 상품 상세 페이지 이동 연결
            // 예: ProductDetailFragment.newInstance(product.id)
            dialog.dismiss()
        }

        rvRecommendProducts.layoutManager = LinearLayoutManager(requireContext())
        rvRecommendProducts.adapter = adapter
        rvRecommendProducts.isNestedScrollingEnabled = false

        adapter.submitList(ProductDummyData.recommendProducts.take(2))

        btnMoreProducts.setOnClickListener {
            dialog.dismiss()

            parentFragmentManager.beginTransaction()
                .replace(R.id.mainContainer, ProductSearchFragment())
                .addToBackStack(null)
                .commit()
        }

        dialog.setContentView(bottomSheetView)
        dialog.show()
    }

    private fun scrollToBottom() {
        scrollChatMessages.post {
            scrollChatMessages.fullScroll(View.FOCUS_DOWN)
        }
    }

    private fun makeTitle(message: String): String {
        return if (message.length > 12) {
            message.take(12) + ".."
        } else {
            message
        }
    }

    private fun makeDummyResponse(userMessage: String): String {
        return "'$userMessage'에 대해 확인해볼게요. 입력한 음식명이나 제품명을 기준으로 성분, 알레르기 가능성, 섭취 시 주의할 점을 안내할 수 있습니다. 실제 서비스에서는 이 부분에 챗봇 API 응답을 연결하면 됩니다."
    }

    private fun dp(value: Int): Int {
        return (value * resources.displayMetrics.density).toInt()
    }

    companion object {
        private const val ARG_INITIAL_MESSAGE = "initial_message"

        fun newInstance(initialMessage: String): ChatFragment {
            return ChatFragment().apply {
                arguments = Bundle().apply {
                    putString(ARG_INITIAL_MESSAGE, initialMessage)
                }
            }
        }
    }
}