package com.coworker.jjikmuk.feature.scanner

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import kotlin.math.min

data class GalleryBarcodeCandidate(
    val rawValue: String,
    val imageBounds: RectF
)

class GalleryBarcodeOverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    var onBarcodeSelected: ((GalleryBarcodeCandidate?) -> Unit)? = null

    private var imageWidth = 0
    private var imageHeight = 0
    private var candidates = emptyList<GalleryBarcodeCandidate>()
    private var selectedIndex = NO_SELECTION

    private val boxPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        style = Paint.Style.STROKE
        strokeWidth = dp(2.5f)
    }

    private val selectedBoxPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#2F80FF")
        style = Paint.Style.STROKE
        strokeWidth = dp(3f)
    }

    private val checkCirclePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#2F80FF")
        style = Paint.Style.FILL
    }

    private val checkPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
        strokeWidth = dp(2.2f)
    }

    private val imageTransform: ImageTransform?
        get() {
            if (imageWidth <= 0 || imageHeight <= 0 || width <= 0 || height <= 0) return null
            val scale = min(width.toFloat() / imageWidth, height.toFloat() / imageHeight)
            val drawnWidth = imageWidth * scale
            val drawnHeight = imageHeight * scale
            return ImageTransform(
                scale = scale,
                offsetX = (width - drawnWidth) / 2f,
                offsetY = (height - drawnHeight) / 2f
            )
        }

    fun showBarcodes(
        imageWidth: Int,
        imageHeight: Int,
        candidates: List<GalleryBarcodeCandidate>
    ) {
        this.imageWidth = imageWidth
        this.imageHeight = imageHeight
        this.candidates = candidates
        selectedIndex = NO_SELECTION
        onBarcodeSelected?.invoke(null)
        invalidate()
    }

    fun clear() {
        imageWidth = 0
        imageHeight = 0
        candidates = emptyList()
        selectedIndex = NO_SELECTION
        onBarcodeSelected?.invoke(null)
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val transform = imageTransform ?: return

        candidates.forEachIndexed { index, candidate ->
            val bounds = candidate.imageBounds.toViewRect(transform)
            canvas.drawRect(bounds, if (index == selectedIndex) selectedBoxPaint else boxPaint)
            if (index == selectedIndex) {
                drawCheck(canvas, bounds)
            }
        }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.actionMasked != MotionEvent.ACTION_UP) return true
        val transform = imageTransform ?: return true

        val touchedIndex = candidates.indexOfLast { candidate ->
            candidate.imageBounds.toViewRect(transform).apply {
                inset(-TOUCH_SLOP_DP.toPx(), -TOUCH_SLOP_DP.toPx())
            }.contains(event.x, event.y)
        }

        if (touchedIndex != NO_SELECTION) {
            selectedIndex = touchedIndex
            onBarcodeSelected?.invoke(candidates[touchedIndex])
            invalidate()
        }
        return true
    }

    private fun drawCheck(canvas: Canvas, bounds: RectF) {
        val radius = dp(11f)
        val centerX = (bounds.right - radius).coerceAtLeast(bounds.left + radius)
        val centerY = (bounds.top + radius).coerceAtMost(bounds.bottom - radius)

        canvas.drawCircle(centerX, centerY, radius, checkCirclePaint)
        canvas.drawLine(centerX - dp(5f), centerY, centerX - dp(1.5f), centerY + dp(4f), checkPaint)
        canvas.drawLine(centerX - dp(1.5f), centerY + dp(4f), centerX + dp(6f), centerY - dp(5f), checkPaint)
    }

    private fun RectF.toViewRect(transform: ImageTransform): RectF {
        return RectF(
            transform.offsetX + left * transform.scale,
            transform.offsetY + top * transform.scale,
            transform.offsetX + right * transform.scale,
            transform.offsetY + bottom * transform.scale
        )
    }

    private fun Float.toPx(): Float = this * resources.displayMetrics.density

    private fun dp(value: Float): Float = value.toPx()

    private data class ImageTransform(
        val scale: Float,
        val offsetX: Float,
        val offsetY: Float
    )

    private companion object {
        const val NO_SELECTION = -1
        const val TOUCH_SLOP_DP = 18f
    }
}
