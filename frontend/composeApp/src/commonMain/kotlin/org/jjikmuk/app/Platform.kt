package org.jjikmuk.app

interface Platform {
    val name: String
}

expect fun getPlatform(): Platform