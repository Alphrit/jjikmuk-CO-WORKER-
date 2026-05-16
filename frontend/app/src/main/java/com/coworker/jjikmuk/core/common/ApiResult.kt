package com.coworker.jjikmuk.core.common

sealed interface ApiResult<out T> {
    data class Success<T>(val data: T) : ApiResult<T>
    data class Error(val message: String, val throwable: Throwable? = null) : ApiResult<Nothing>
}
