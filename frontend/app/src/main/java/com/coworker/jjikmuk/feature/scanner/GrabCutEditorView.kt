package com.coworker.jjikmuk.feature.scanner

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import kotlin.math.max
import kotlin.math.min

class GrabCutEditorView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {
    private val imagePaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val dimPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(70, 0, 0, 0)
    }
    private val contourFillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(55, 200, 247, 154)
        style = Paint.Style.FILL
    }
    private val contourStrokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#C8F79A")
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
        strokeWidth = dp(5f)
    }

    private var sourceBitmap: Bitmap? = null
    private var resultBitmap: Bitmap? = null
    private val imageMatrix = Matrix()
    private val inverseMatrix = Matrix()
    private val imageDisplayRect = RectF()
    private val contourPath = Path()
    private var contourPointCount = 0
    private var isDrawing = false

    fun setSourceBitmap(bitmap: Bitmap) {
        sourceBitmap = bitmap
        resultBitmap = null
        updateImageMatrix()
        resetSelection()
    }

    fun setResultBitmap(bitmap: Bitmap) {
        resultBitmap = bitmap
        invalidate()
    }

    fun showSelectionMode() {
        resultBitmap = null
        invalidate()
    }

    fun getSelectionMaskBitmap(): Bitmap? {
        val bitmap = sourceBitmap ?: return null
        if (contourPointCount < MIN_CONTOUR_POINTS) return null

        val bitmapPath = Path(contourPath)
        bitmapPath.close()
        bitmapPath.transform(inverseMatrix)

        val maskBitmap = Bitmap.createBitmap(bitmap.width, bitmap.height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(maskBitmap)
        canvas.drawColor(Color.BLACK)

        val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.WHITE
            style = Paint.Style.FILL
        }
        canvas.drawPath(bitmapPath, fillPaint)

        return maskBitmap
    }

    fun getSelectionBoundsInBitmap(): RectF? {
        val bitmap = sourceBitmap ?: return null
        if (contourPointCount < MIN_CONTOUR_POINTS) return null

        val bitmapPath = Path(contourPath)
        bitmapPath.close()
        bitmapPath.transform(inverseMatrix)

        val bounds = RectF()
        bitmapPath.computeBounds(bounds, true)
        bounds.left = bounds.left.coerceIn(0f, bitmap.width - 1f)
        bounds.top = bounds.top.coerceIn(0f, bitmap.height - 1f)
        bounds.right = bounds.right.coerceIn(bounds.left + 1f, bitmap.width.toFloat())
        bounds.bottom = bounds.bottom.coerceIn(bounds.top + 1f, bitmap.height.toFloat())
        return bounds
    }

    fun resetSelection() {
        resultBitmap = null
        contourPath.reset()
        contourPointCount = 0
        isDrawing = false
        invalidate()
    }

    override fun onSizeChanged(width: Int, height: Int, oldWidth: Int, oldHeight: Int) {
        super.onSizeChanged(width, height, oldWidth, oldHeight)
        updateImageMatrix()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val bitmap = resultBitmap ?: sourceBitmap ?: return
        canvas.drawBitmap(bitmap, imageMatrix, imagePaint)

        if (resultBitmap != null) return

        canvas.save()
        canvas.clipRect(imageDisplayRect)
        canvas.drawRect(imageDisplayRect, dimPaint)
        if (contourPointCount > 0) {
            val closedPath = Path(contourPath)
            closedPath.close()
            canvas.drawPath(closedPath, contourFillPaint)
            canvas.drawPath(contourPath, contourStrokePaint)
        }
        canvas.restore()
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (resultBitmap != null || sourceBitmap == null) return false

        val x = event.x.coerceIn(imageDisplayRect.left, imageDisplayRect.right)
        val y = event.y.coerceIn(imageDisplayRect.top, imageDisplayRect.bottom)

        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                if (!imageDisplayRect.contains(event.x, event.y)) return false
                parent.requestDisallowInterceptTouchEvent(true)
                contourPath.reset()
                contourPath.moveTo(x, y)
                contourPointCount = 1
                isDrawing = true
                invalidate()
                return true
            }

            MotionEvent.ACTION_MOVE -> {
                if (!isDrawing) return false
                for (index in 0 until event.historySize) {
                    val hx = event.getHistoricalX(index).coerceIn(imageDisplayRect.left, imageDisplayRect.right)
                    val hy = event.getHistoricalY(index).coerceIn(imageDisplayRect.top, imageDisplayRect.bottom)
                    contourPath.lineTo(hx, hy)
                    contourPointCount += 1
                }
                contourPath.lineTo(x, y)
                contourPointCount += 1
                invalidate()
                return true
            }

            MotionEvent.ACTION_UP,
            MotionEvent.ACTION_CANCEL -> {
                isDrawing = false
                parent.requestDisallowInterceptTouchEvent(false)
                invalidate()
                return true
            }
        }
        return false
    }

    private fun updateImageMatrix() {
        val bitmap = sourceBitmap ?: return
        if (width <= 0 || height <= 0) return

        imageMatrix.reset()
        val scale = min(width / bitmap.width.toFloat(), height / bitmap.height.toFloat())
        val dx = (width - bitmap.width * scale) / 2f
        val dy = (height - bitmap.height * scale) / 2f
        imageMatrix.postScale(scale, scale)
        imageMatrix.postTranslate(dx, dy)
        imageMatrix.invert(inverseMatrix)

        imageDisplayRect.set(0f, 0f, bitmap.width.toFloat(), bitmap.height.toFloat())
        imageMatrix.mapRect(imageDisplayRect)
    }

    private fun dp(value: Float): Float {
        return value * resources.displayMetrics.density
    }

    private companion object {
        const val MIN_CONTOUR_POINTS = 6
    }
}
