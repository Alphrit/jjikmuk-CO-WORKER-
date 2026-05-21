package com.coworker.jjikmuk.feature.chat

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.widget.EditText
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
import com.coworker.jjikmuk.feature.chat.adapter.ChatMessageAdapter
import com.coworker.jjikmuk.feature.product.detail.ProductDetailFragment
import com.coworker.jjikmuk.feature.product.model.ProductUiModel
import com.coworker.jjikmuk.feature.product.search.ProductSearchFragment
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

        val initialMessage = arguments?.getString(ARG_INITIAL_MESSAGE).orEmpty()

        if (initialMessage.isNotBlank()) {
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

        btnChatBack.setOnClickListener {
            parentFragmentManager.popBackStack()
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
                viewModel.uiState.collect { state ->
                    tvChatTitle.text = state.title

                    chatMessageAdapter.submitList(state.messages) {
                        scrollToBottom()
                    }

                    if (state.shouldShowRecommendSheet) {
                        rvChatMessages.postDelayed({
                            if (!isAdded) return@postDelayed

                            showRecommendProductBottomSheet(state.recommendedProducts)
                            viewModel.onRecommendSheetShown()
                        }, RECOMMEND_SHEET_DELAY_MILLIS)
                    }
                }
            }
        }
    }

    private fun showRecommendProductBottomSheet(products: List<ProductUiModel>) {
        RecommendProductBottomSheet(
            context = requireContext(),
            layoutInflater = layoutInflater,
            products = products,
            onProductClick = { product ->
                parentFragmentManager.beginTransaction()
                    .replace(R.id.mainContainer, ProductDetailFragment.newInstance(product.id))
                    .addToBackStack(null)
                    .commit()
            },
            onMoreClick = {
                parentFragmentManager.beginTransaction()
                    .replace(R.id.mainContainer, ProductSearchFragment())
                    .addToBackStack(null)
                    .commit()
            }
        ).show()
    }

    private fun scrollToBottom() {
        val lastIndex = chatMessageAdapter.itemCount - 1

        if (lastIndex >= 0) {
            rvChatMessages.scrollToPosition(lastIndex)
        }
    }

    companion object {
        private const val ARG_INITIAL_MESSAGE = "initial_message"
        private const val RECOMMEND_SHEET_DELAY_MILLIS = 500L

        fun newInstance(initialMessage: String): ChatFragment {
            return ChatFragment().apply {
                arguments = Bundle().apply {
                    putString(ARG_INITIAL_MESSAGE, initialMessage)
                }
            }
        }
    }
}