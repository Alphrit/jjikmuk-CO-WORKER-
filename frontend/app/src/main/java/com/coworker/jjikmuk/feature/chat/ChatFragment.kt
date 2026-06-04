package com.coworker.jjikmuk.feature.chat

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.widget.EditText
import android.widget.ImageButton
import android.widget.TextView
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.fragment.app.viewModels
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.core.common.showUploadOptionBottomSheet
import com.coworker.jjikmuk.domain.model.ChatProductCandidate
import com.coworker.jjikmuk.domain.model.UploadOption
import com.coworker.jjikmuk.feature.chat.adapter.ChatMessageAdapter
import com.coworker.jjikmuk.feature.product.model.ProductUiModel
import com.coworker.jjikmuk.feature.product.detail.ProductDetailFragment
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch

@AndroidEntryPoint
class ChatFragment : Fragment() {

    private val viewModel: ChatViewModel by viewModels()

    private lateinit var rvChatMessages: RecyclerView
    private lateinit var chatMessageAdapter: ChatMessageAdapter
    private lateinit var etChatMessage: EditText
    private lateinit var btnChatSend: ImageButton
    private lateinit var tvChatTitle: TextView

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        return inflater.inflate(R.layout.fragment_chat, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        initViews(view)
        setupChatMessageRecyclerView()
        setupClickListeners(view)
        observeViewModel()

        val chatHistoryId = arguments?.getLong(ARG_CHAT_HISTORY_ID, UNKNOWN_CHAT_HISTORY_ID)
            ?: UNKNOWN_CHAT_HISTORY_ID
        val initialMessage = arguments?.getString(ARG_INITIAL_MESSAGE).orEmpty()

        if (chatHistoryId != UNKNOWN_CHAT_HISTORY_ID) {
            viewModel.loadChatHistory(chatHistoryId)
        } else if (initialMessage.isNotBlank()) {
            viewModel.startChat(initialMessage)
        }
    }

    private fun initViews(view: View) {
        rvChatMessages = view.findViewById(R.id.rvChatMessages)
        etChatMessage = view.findViewById(R.id.etChatMessage)
        btnChatSend = view.findViewById(R.id.btnChatSend)
        tvChatTitle = view.findViewById(R.id.tvChatTitle)
    }

    private fun setupChatMessageRecyclerView() {
        chatMessageAdapter = ChatMessageAdapter()

        rvChatMessages.layoutManager = LinearLayoutManager(requireContext()).apply {
            stackFromEnd = true
        }
        rvChatMessages.adapter = chatMessageAdapter
        rvChatMessages.isNestedScrollingEnabled = false
    }

    private fun setupClickListeners(view: View) {
        val btnChatBack = view.findViewById<ImageButton>(R.id.btnChatBack)
        val btnChatPlus = view.findViewById<ImageButton>(R.id.btnChatPlus)

        btnChatBack.setOnClickListener {
            parentFragmentManager.popBackStack()
        }

        btnChatPlus.setOnClickListener {
            etChatMessage.clearFocus()
            showUploadOptionBottomSheet(viewModel::onUploadOptionSelected)
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

        etChatMessage.text.clear()
        viewModel.sendMessage(message)
    }

    private fun observeViewModel() {
        viewLifecycleOwner.lifecycleScope.launch {
            viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch {
                    viewModel.uiState.collect { state ->
                        tvChatTitle.text = state.title

                        chatMessageAdapter.submitList(state.messages) {
                            scrollToBottom()
                        }

                        if (state.shouldShowRecommendSheet) {
                            rvChatMessages.postDelayed({
                                if (!isAdded) return@postDelayed

                                showProductCandidateBottomSheet(
                                    title = state.productCandidateSheetTitle,
                                    productCandidates = state.productCandidates
                                )
                                viewModel.onRecommendSheetShown()
                            }, RECOMMEND_SHEET_DELAY_MILLIS)
                        }
                    }
                }

                launch {
                    viewModel.uploadOptionEvent.collect { option ->
                        handleUploadOption(option)
                    }
                }

                launch {
                    viewModel.productDetailEvent.collect { event ->
                        parentFragmentManager.beginTransaction()
                            .replace(
                                R.id.mainContainer,
                                ProductDetailFragment.newInstance(
                                    productId = event.barcode,
                                    safetyAnswer = event.answer,
                                    riskLevel = event.riskLevel
                                )
                            )
                            .addToBackStack(null)
                            .commit()
                    }
                }
            }
        }
    }

    private fun handleUploadOption(option: UploadOption) {
        when (option) {
            UploadOption.TAKE_PHOTO -> {
                Toast.makeText(requireContext(), "사진찍기 기능을 준비 중입니다.", Toast.LENGTH_SHORT).show()
            }

            UploadOption.UPLOAD_IMAGE -> {
                Toast.makeText(requireContext(), "이미지 업로드 기능을 준비 중입니다.", Toast.LENGTH_SHORT).show()
            }

            UploadOption.UPLOAD_FILE -> {
                Toast.makeText(requireContext(), "파일 업로드 기능을 준비 중입니다.", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun showProductCandidateBottomSheet(
        title: String,
        productCandidates: List<ChatProductCandidate>
    ) {
        if (productCandidates.isEmpty()) return

        val candidateItems = productCandidates.mapIndexed { index, candidate ->
            val id = candidate.makeUiId(index)
            CandidateSheetItem(
                candidate = candidate,
                uiModel = candidate.toUiModel(id)
            )
        }
        val candidateById = candidateItems.associate { item ->
            item.uiModel.id to item.candidate
        }

        RecommendProductBottomSheet(
            context = requireContext(),
            layoutInflater = layoutInflater,
            title = title,
            products = candidateItems.map { item -> item.uiModel },
            bottomOffsetPx = calculateChatInputBottomOffset(),
            showMoreButton = false,
            onProductClick = { product ->
                candidateById[product.id]?.let { candidate ->
                    viewModel.selectProductCandidate(candidate)
                }
            }
        ).show()
    }

    private fun calculateChatInputBottomOffset(): Int {
        val layoutParams = etChatMessage.rootView
            .findViewById<View>(R.id.layoutChatInput)
            .layoutParams as? ViewGroup.MarginLayoutParams
        val bottomMargin = layoutParams?.bottomMargin ?: 0

        return etChatMessage.rootView
            .findViewById<View>(R.id.layoutChatInput)
            .height + bottomMargin
    }

    private fun ChatProductCandidate.makeUiId(index: Int): String {
        return barcode.ifBlank { productName }.ifBlank { index.toString() } + "_$index"
    }

    private fun ChatProductCandidate.toUiModel(id: String): ProductUiModel {
        return ProductUiModel(
            id = id,
            category = category.ifBlank { "상품 후보" },
            name = productName.ifBlank { "이름 없는 상품" },
            imageResId = R.drawable.ic_launcher_foreground,
            allergyTags = allergy.take(2)
        )
    }

    private data class CandidateSheetItem(
        val candidate: ChatProductCandidate,
        val uiModel: ProductUiModel
    )

    private fun scrollToBottom() {
        val lastIndex = chatMessageAdapter.itemCount - 1

        if (lastIndex >= 0) {
            rvChatMessages.scrollToPosition(lastIndex)
        }
    }

    companion object {
        private const val ARG_INITIAL_MESSAGE = "initial_message"
        private const val ARG_CHAT_HISTORY_ID = "chat_history_id"
        private const val UNKNOWN_CHAT_HISTORY_ID = -1L
        private const val RECOMMEND_SHEET_DELAY_MILLIS = 500L

        fun newInstance(initialMessage: String): ChatFragment {
            return ChatFragment().apply {
                arguments = Bundle().apply {
                    putString(ARG_INITIAL_MESSAGE, initialMessage)
                }
            }
        }

        fun newInstance(chatHistoryId: Long): ChatFragment {
            return ChatFragment().apply {
                arguments = Bundle().apply {
                    putLong(ARG_CHAT_HISTORY_ID, chatHistoryId)
                }
            }
        }
    }
}
