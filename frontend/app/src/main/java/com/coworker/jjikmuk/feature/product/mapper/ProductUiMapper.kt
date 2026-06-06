package com.coworker.jjikmuk.feature.product.mapper

import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.data.local.dummy.ProductDummyData
import com.coworker.jjikmuk.domain.model.Product
import com.coworker.jjikmuk.feature.product.model.ProductUiModel

fun Product.toUiModel(): ProductUiModel {
    return ProductUiModel(
        id = id,
        category = category,
        name = name,
        imageResId = if (id == ProductDummyData.DEMO_SHRIMP_CRACKER_ID) {
            R.drawable.ic_product_shrimp_cracker_demo
        } else {
            R.drawable.ic_launcher_foreground
        },
        allergyTags = allergyTags
    )
}
