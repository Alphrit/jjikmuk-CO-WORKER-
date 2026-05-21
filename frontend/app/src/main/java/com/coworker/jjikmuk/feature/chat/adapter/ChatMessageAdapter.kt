package com.coworker.jjikmuk.feature.chat.adapter

import android.content.Context

import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.domain.model.ChatMessage

class ChatMessageAdapter : ListAdapter<ChatMessage, ChatMessageAdapter.ChatMessageViewHolder>(
    ChatMessageDiffCallback()
) {

    override fun getItemViewType(position: Int): Int {
        return when (getItem(position).senderType) {
            ChatMessage.SenderType.USER -> VIEW_TYPE_USER
            ChatMessage.SenderType.BOT -> VIEW_TYPE_BOT
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ChatMessageViewHolder {
        val row = LinearLayout(parent.context).apply {
            layoutParams = RecyclerView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply {
                topMargin = parent.context.dp(if (viewType == VIEW_TYPE_USER) 12 else 20)
            }
            orientation = LinearLayout.HORIZONTAL
            gravity = if (viewType == VIEW_TYPE_USER) Gravity.END else Gravity.START
        }

        return when (viewType) {
            VIEW_TYPE_USER -> ChatMessageViewHolder.UserMessageViewHolder(row)
            else -> ChatMessageViewHolder.BotMessageViewHolder(row)
        }
    }

    override fun onBindViewHolder(holder: ChatMessageViewHolder, position: Int) {
        holder.bind(getItem(position))
    }

    sealed class ChatMessageViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        abstract fun bind(message: ChatMessage)

        class UserMessageViewHolder(
            private val row: LinearLayout
        ) : ChatMessageViewHolder(row) {

            private val bubble: TextView = TextView(row.context).apply {
                layoutParams = LinearLayout.LayoutParams(
                    (row.resources.displayMetrics.widthPixels * 0.68f).toInt(),
                    LinearLayout.LayoutParams.WRAP_CONTENT
                )
                background = row.context.getDrawable(R.drawable.bg_chat_user)
                gravity = Gravity.CENTER
                minHeight = row.context.dp(44)
                setPadding(
                    row.context.dp(18),
                    row.context.dp(10),
                    row.context.dp(18),
                    row.context.dp(10)
                )
                setTextColor(0xFFFFFFFF.toInt())
                textSize = 12f
            }

            init {
                row.addView(bubble)
            }

            override fun bind(message: ChatMessage) {
                bubble.text = message.text
            }
        }

        class BotMessageViewHolder(
            private val row: LinearLayout
        ) : ChatMessageViewHolder(row) {

            private val dot: View = View(row.context).apply {
                layoutParams = LinearLayout.LayoutParams(
                    row.context.dp(18),
                    row.context.dp(18)
                ).apply {
                    rightMargin = row.context.dp(8)
                    topMargin = row.context.dp(26)
                }
                background = row.context.getDrawable(R.drawable.bg_camera_circle)
                alpha = 0.35f
            }

            private val bubble: TextView = TextView(row.context).apply {
                layoutParams = LinearLayout.LayoutParams(
                    (row.resources.displayMetrics.widthPixels * 0.62f).toInt(),
                    LinearLayout.LayoutParams.WRAP_CONTENT
                )
                background = row.context.getDrawable(R.drawable.bg_chat_bot)
                minHeight = row.context.dp(44)
                setPadding(
                    row.context.dp(16),
                    row.context.dp(12),
                    row.context.dp(16),
                    row.context.dp(12)
                )
                setTextColor(0xFF555555.toInt())
                textSize = 12f
            }

            init {
                row.addView(dot)
                row.addView(bubble)
            }

            override fun bind(message: ChatMessage) {
                bubble.text = message.text
            }
        }
    }

    private class ChatMessageDiffCallback : DiffUtil.ItemCallback<ChatMessage>() {
        override fun areItemsTheSame(
            oldItem: ChatMessage,
            newItem: ChatMessage
        ): Boolean {
            return oldItem.id == newItem.id
        }

        override fun areContentsTheSame(
            oldItem: ChatMessage,
            newItem: ChatMessage
        ): Boolean {
            return oldItem == newItem
        }
    }


    companion object {
        private const val VIEW_TYPE_USER = 1
        private const val VIEW_TYPE_BOT = 2
    }
}

private fun Context.dp(value: Int): Int {
    return (value * resources.displayMetrics.density).toInt()
}