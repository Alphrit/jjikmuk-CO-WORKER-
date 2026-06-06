package com.coworker.jjikmuk.data.local.preference

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import java.io.File

class RegisteredProductImageStore(
    private val context: Context
) {
    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun saveProductImage(productId: String, bitmap: Bitmap): String {
        val directory = File(context.filesDir, IMAGE_DIRECTORY).apply {
            mkdirs()
        }
        val imageFile = File(directory, "$productId.png")
        val imageToSave = cropTransparentPadding(bitmap)
        imageFile.outputStream().use { outputStream ->
            imageToSave.compress(Bitmap.CompressFormat.PNG, 100, outputStream)
        }
        if (imageToSave != bitmap) {
            imageToSave.recycle()
        }

        prefs.edit()
            .putString(key(productId), imageFile.absolutePath)
            .apply()

        return imageFile.absolutePath
    }

    fun getProductImagePath(productId: String): String? {
        val path = prefs.getString(key(productId), null) ?: return null
        return if (File(path).exists()) path else null
    }

    fun deleteProductImage(productId: String): Boolean {
        val path = prefs.getString(key(productId), null)
        val deleted = path?.let { File(it).delete() } ?: false

        prefs.edit()
            .remove(key(productId))
            .apply()

        return deleted
    }

    private fun cropTransparentPadding(bitmap: Bitmap): Bitmap {
        var left = bitmap.width
        var top = bitmap.height
        var right = -1
        var bottom = -1

        for (y in 0 until bitmap.height) {
            for (x in 0 until bitmap.width) {
                if (Color.alpha(bitmap.getPixel(x, y)) > MIN_VISIBLE_ALPHA) {
                    if (x < left) left = x
                    if (x > right) right = x
                    if (y < top) top = y
                    if (y > bottom) bottom = y
                }
            }
        }

        if (right < left || bottom < top) return bitmap

        val paddingX = ((right - left + 1) * CROP_PADDING_RATIO).toInt()
        val paddingY = ((bottom - top + 1) * CROP_PADDING_RATIO).toInt()
        val cropLeft = (left - paddingX).coerceAtLeast(0)
        val cropTop = (top - paddingY).coerceAtLeast(0)
        val cropRight = (right + paddingX).coerceAtMost(bitmap.width - 1)
        val cropBottom = (bottom + paddingY).coerceAtMost(bitmap.height - 1)

        return Bitmap.createBitmap(
            bitmap,
            cropLeft,
            cropTop,
            cropRight - cropLeft + 1,
            cropBottom - cropTop + 1
        )
    }

    private fun key(productId: String): String {
        return "product_image_$productId"
    }

    private companion object {
        const val PREFS_NAME = "registered_product_images"
        const val IMAGE_DIRECTORY = "registered_product_images"
        const val MIN_VISIBLE_ALPHA = 8
        const val CROP_PADDING_RATIO = 0.20f
    }
}
