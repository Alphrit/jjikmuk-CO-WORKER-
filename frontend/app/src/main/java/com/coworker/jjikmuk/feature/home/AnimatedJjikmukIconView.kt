package com.coworker.jjikmuk.feature.home

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import android.view.animation.LinearInterpolator
import kotlin.math.min

class AnimatedJjikmukIconView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private data class IconBar(
        val x: Float,
        val y: Float,
        val width: Float,
        val height: Float,
        val startColor: Int,
        val endColor: Int,
        val phase: Float
    )

    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val shaderMatrix = Matrix()

    private var animationProgress = 0f
    private val animator = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 1800L
        repeatCount = ValueAnimator.INFINITE
        interpolator = LinearInterpolator()
        addUpdateListener {
            animationProgress = it.animatedValue as Float
            invalidate()
        }
    }

    private val bars = listOf(
        IconBar(28.0249f, 0f, 6.05957f, 89f, Color.rgb(0, 149, 32), Color.rgb(139, 227, 162), 0f),
        IconBar(11.3613f, 51.8848f, 6.05957f, 37.1149f, Color.rgb(0, 149, 32), Color.rgb(139, 227, 162), 0.18f),
        IconBar(58.7021f, 0f, 6.05957f, 37.1149f, Color.rgb(139, 227, 162), Color.rgb(0, 154, 152), 0.36f),
        IconBar(44.6897f, 0f, 6.05957f, 89f, Color.rgb(139, 227, 162), Color.rgb(0, 154, 152), 0.54f),
        IconBar(72.7151f, 0f, 6.05957f, 89f, Color.rgb(139, 227, 162), Color.rgb(0, 154, 152), 0.72f)
    )

    init {
        if (contentDescription.isNullOrBlank()) {
            contentDescription = "JJIKMUK"
        }
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        startAnimationIfNeeded()
    }

    override fun onDetachedFromWindow() {
        animator.cancel()
        super.onDetachedFromWindow()
    }

    override fun onVisibilityChanged(changedView: View, visibility: Int) {
        super.onVisibilityChanged(changedView, visibility)
        if (visibility == VISIBLE) {
            startAnimationIfNeeded()
        } else {
            animator.cancel()
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val contentWidth = width - paddingLeft - paddingRight
        val contentHeight = height - paddingTop - paddingBottom
        val size = min(contentWidth, contentHeight).toFloat()
        if (size <= 0f) return

        val left = paddingLeft + (contentWidth - size) / 2f
        val top = paddingTop + (contentHeight - size) / 2f
        val scale = size / FIGMA_ICON_SIZE

        bars.forEach { bar ->
            val rectLeft = left + bar.x * scale
            val rectTop = top + bar.y * scale
            val rectRight = rectLeft + bar.width * scale
            val rectBottom = rectTop + bar.height * scale

            barPaint.shader = createFlowingShader(
                bar = bar,
                top = top,
                size = size,
                phase = (animationProgress + bar.phase) % 1f
            )
            canvas.drawRect(rectLeft, rectTop, rectRight, rectBottom, barPaint)
        }

        barPaint.shader = null
    }

    private fun startAnimationIfNeeded() {
        if (isShown && !animator.isStarted) {
            animator.start()
        }
    }

    private fun createFlowingShader(
        bar: IconBar,
        top: Float,
        size: Float,
        phase: Float
    ): LinearGradient {
        val darkStart = darken(bar.startColor)
        val lightEnd = lighten(bar.endColor)
        val shader = LinearGradient(
            0f,
            top - size,
            0f,
            top + size,
            intArrayOf(darkStart, bar.startColor, lightEnd, bar.endColor, darkStart),
            floatArrayOf(0f, 0.25f, 0.5f, 0.75f, 1f),
            Shader.TileMode.REPEAT
        )

        shaderMatrix.reset()
        shaderMatrix.setTranslate(0f, phase * size * 2f)
        shader.setLocalMatrix(shaderMatrix)
        return shader
    }

    private fun darken(color: Int): Int {
        return Color.rgb(
            (Color.red(color) * DARKEN_FACTOR).toInt(),
            (Color.green(color) * DARKEN_FACTOR).toInt(),
            (Color.blue(color) * DARKEN_FACTOR).toInt()
        )
    }

    private fun lighten(color: Int): Int {
        return Color.rgb(
            Color.red(color) + ((255 - Color.red(color)) * LIGHTEN_FACTOR).toInt(),
            Color.green(color) + ((255 - Color.green(color)) * LIGHTEN_FACTOR).toInt(),
            Color.blue(color) + ((255 - Color.blue(color)) * LIGHTEN_FACTOR).toInt()
        )
    }

    private companion object {
        const val FIGMA_ICON_SIZE = 89f
        const val DARKEN_FACTOR = 0.72f
        const val LIGHTEN_FACTOR = 0.18f
    }
}
