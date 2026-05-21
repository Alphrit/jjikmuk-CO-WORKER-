package com.coworker.jjikmuk.di

import com.coworker.jjikmuk.data.repository.ChatRepositoryImpl
import com.coworker.jjikmuk.data.repository.FakeUserProfileRepository
import com.coworker.jjikmuk.data.repository.ProductRepositoryImpl
import com.coworker.jjikmuk.domain.repository.ChatRepository
import com.coworker.jjikmuk.domain.repository.ProductRepository
import com.coworker.jjikmuk.domain.repository.UserProfileRepository
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
abstract class RepositoryModule {

    @Binds
    @Singleton
    abstract fun bindProductRepository(
        impl: ProductRepositoryImpl
    ): ProductRepository

    @Binds
    @Singleton
    abstract fun bindChatRepository(
        impl: ChatRepositoryImpl
    ): ChatRepository

    @Binds
    @Singleton
    abstract fun bindUserProfileRepository(
        impl: FakeUserProfileRepository
    ): UserProfileRepository
}
