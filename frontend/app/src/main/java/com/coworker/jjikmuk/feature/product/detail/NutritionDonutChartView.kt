package com.coworker.jjikmuk.feature.product.detail

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View

class NutritionDonutChartView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private val arcPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.BUTT
    }

    private val emptyPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#EDEDED")
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.BUTT
    }

    private val bounds = RectF()

    private var carbPercent: Float = 0f
    private var proteinPercent: Float = 0f
    private var fatPercent: Float = 0f

    fun setPercents(
        carbohydratePercent: Double?,
        proteinPercent: Double?,
        fatPercent: Double?
    ) {
        carbPercent = carbohydratePercent.toPositiveFloat()
        this.proteinPercent = proteinPercent.toPositiveFloat()
        this.fatPercent = fatPercent.toPositiveFloat()
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val strokeWidth = width.coerceAtMost(height) * STROKE_WIDTH_RATIO
        arcPaint.strokeWidth = strokeWidth
        emptyPaint.strokeWidth = strokeWidth

        val inset = strokeWidth / 2f
        bounds.set(inset, inset, width - inset, height - inset)

        val total = carbPercent + proteinPercent + fatPercent
        if (total <= 0f) {
            canvas.drawArc(bounds, 0f, FULL_SWEEP, false, emptyPaint)
            return
        }

        var startAngle = START_ANGLE
        drawSection(canvas, startAngle, carbPercent / total, COLOR_CARBOHYDRATE)
        startAngle += FULL_SWEEP * (carbPercent / total)

        drawSection(canvas, startAngle, proteinPercent / total, COLOR_PROTEIN)
        startAngle += FULL_SWEEP * (proteinPercent / total)

        drawSection(canvas, startAngle, fatPercent / total, COLOR_FAT)
    }

    private fun drawSection(
        canvas: Canvas,
        startAngle: Float,
        ratio: Float,
        color: Int
    ) {
        if (ratio <= 0f) return

        arcPaint.color = color
        canvas.drawArc(bounds, startAngle, FULL_SWEEP * ratio, false, arcPaint)
    }

    private fun Double?.toPositiveFloat(): Float {
        return this?.toFloat()?.coerceAtLeast(0f) ?: 0f
    }

    companion object {
        private const val START_ANGLE = -90f
        private const val FULL_SWEEP = 360f
        private const val STROKE_WIDTH_RATIO = 0.22f
        private val COLOR_CARBOHYDRATE = Color.parseColor("#E25143")
        private val COLOR_PROTEIN = Color.parseColor("#5A78FF")
        private val COLOR_FAT = Color.parseColor("#E4B328")
    }
}
