package com.coworker.jjikmuk.feature.scanner

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.util.AttributeSet
import android.view.View

class ScanGuideView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private val strokeWidth = dp(6f)
    private val cornerRadius = dp(16f)
    private val horizontalLength = dp(70f)
    private val verticalLength = dp(42f)
    private var scanLineProgress = 0f
    private var scanLineAnimator: ValueAnimator? = null

    private val guidePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#C8F79A")
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
        strokeWidth = this@ScanGuideView.strokeWidth
    }

    private val scanLinePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#18C64A")
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeWidth = dp(4f)
    }

    fun startScanAnimation() {
        if (scanLineAnimator?.isStarted == true) return

        scanLineProgress = 0f
        scanLineAnimator = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 900L
            repeatCount = ValueAnimator.INFINITE
            repeatMode = ValueAnimator.REVERSE
            addUpdateListener { animator ->
                scanLineProgress = animator.animatedValue as Float
                invalidate()
            }
            start()
        }
    }

    fun stopScanAnimation() {
        scanLineAnimator?.cancel()
        scanLineAnimator = null
        scanLineProgress = 0f
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val inset = strokeWidth / 2f
        val left = inset
        val top = inset
        val right = width - inset
        val bottom = height - inset

        drawTopLeft(canvas, left, top)
        drawTopRight(canvas, right, top)
        drawBottomLeft(canvas, left, bottom)
        drawBottomRight(canvas, right, bottom)
        drawScanLine(canvas, left, top, right, bottom)
    }

    private fun drawTopLeft(canvas: Canvas, left: Float, top: Float) {
        val path = Path().apply {
            moveTo(left, top + cornerRadius + verticalLength)
            lineTo(left, top + cornerRadius)
            quadTo(left, top, left + cornerRadius, top)
            lineTo(left + cornerRadius + horizontalLength, top)
        }
        canvas.drawPath(path, guidePaint)
    }

    private fun drawTopRight(canvas: Canvas, right: Float, top: Float) {
        val path = Path().apply {
            moveTo(right - cornerRadius - horizontalLength, top)
            lineTo(right - cornerRadius, top)
            quadTo(right, top, right, top + cornerRadius)
            lineTo(right, top + cornerRadius + verticalLength)
        }
        canvas.drawPath(path, guidePaint)
    }

    private fun drawBottomLeft(canvas: Canvas, left: Float, bottom: Float) {
        val path = Path().apply {
            moveTo(left, bottom - cornerRadius - verticalLength)
            lineTo(left, bottom - cornerRadius)
            quadTo(left, bottom, left + cornerRadius, bottom)
            lineTo(left + cornerRadius + horizontalLength, bottom)
        }
        canvas.drawPath(path, guidePaint)
    }

    private fun drawBottomRight(canvas: Canvas, right: Float, bottom: Float) {
        val path = Path().apply {
            moveTo(right - cornerRadius - horizontalLength, bottom)
            lineTo(right - cornerRadius, bottom)
            quadTo(right, bottom, right, bottom - cornerRadius)
            lineTo(right, bottom - cornerRadius - verticalLength)
        }
        canvas.drawPath(path, guidePaint)
    }

    private fun drawScanLine(
        canvas: Canvas,
        left: Float,
        top: Float,
        right: Float,
        bottom: Float
    ) {
        if (scanLineAnimator == null) return

        val horizontalInset = dp(12f)
        val y = top + ((bottom - top) * scanLineProgress)
        canvas.drawLine(
            left + horizontalInset,
            y,
            right - horizontalInset,
            y,
            scanLinePaint
        )
    }

    override fun onDetachedFromWindow() {
        stopScanAnimation()
        super.onDetachedFromWindow()
    }

    private fun dp(value: Float): Float {
        return value * resources.displayMetrics.density
    }
}
