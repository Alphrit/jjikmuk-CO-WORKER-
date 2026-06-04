package com.coworker.jjikmuk.data.remote.api

import com.coworker.jjikmuk.data.remote.dto.ChatApiRequest
import com.coworker.jjikmuk.data.remote.dto.ChatApiResponse
import retrofit2.http.Body
import retrofit2.http.POST

interface ChatApi {

    @POST("chat")
    suspend fun sendMessage(
        @Body request: ChatApiRequest
    ): ChatApiResponse
}
