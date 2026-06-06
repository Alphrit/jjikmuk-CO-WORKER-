package com.coworker.jjikmuk.feature.scanner

import android.app.Activity
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Matrix
import android.media.ExifInterface
import android.os.Bundle
import android.widget.ImageButton
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.coworker.jjikmuk.MainActivity
import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.data.local.preference.RegisteredProductImageStore
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.opencv.android.OpenCVLoader
import org.opencv.android.Utils
import org.opencv.core.CvType
import org.opencv.core.Mat
import org.opencv.core.Rect
import org.opencv.core.Scalar
import org.opencv.imgproc.Imgproc

class ProductGrabCutActivity : AppCompatActivity() {
    private lateinit var editorView: GrabCutEditorView
    private lateinit var statusText: TextView
    private lateinit var runButton: TextView
    private lateinit var resetButton: TextView
    private lateinit var confirmButton: TextView

    private var sourceBitmap: Bitmap? = null
    private var resultBitmap: Bitmap? = null
    private var imagePath: String? = null
    private var productId: String? = null
    private var productName: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_product_grab_cut)

        imagePath = intent.getStringExtra(EXTRA_IMAGE_PATH)
        productId = intent.getStringExtra(EXTRA_PRODUCT_ID)
        productName = intent.getStringExtra(EXTRA_PRODUCT_NAME)
        editorView = findViewById(R.id.grabCutEditorView)
        statusText = findViewById(R.id.tvGrabCutStatus)
        runButton = findViewById(R.id.btnRunGrabCut)
        resetButton = findViewById(R.id.btnResetGrabCut)
        confirmButton = findViewById(R.id.btnConfirmGrabCut)

        findViewById<ImageButton>(R.id.btnGrabCutBack).setOnClickListener {
            setResult(Activity.RESULT_CANCELED)
            finish()
        }
        runButton.setOnClickListener {
            runGrabCut()
        }
        resetButton.setOnClickListener {
            resultBitmap = null
            editorView.showSelectionMode()
            editorView.resetSelection()
            setConfirmEnabled(false)
            statusText.text = "손가락으로 상품 외곽을 한 바퀴 그린 뒤 배경 제거를 실행하세요."
        }
        confirmButton.setOnClickListener {
            saveRegisteredProductImage()
        }

        if (!OpenCVLoader.initLocal()) {
            Toast.makeText(this, "OpenCV 초기화에 실패했습니다.", Toast.LENGTH_LONG).show()
            finish()
            return
        }

        loadImage()
    }

    private fun loadImage() {
        val path = imagePath
        if (path.isNullOrBlank()) {
            Toast.makeText(this, "촬영 이미지를 찾을 수 없습니다.", Toast.LENGTH_LONG).show()
            finish()
            return
        }

        val decodedBitmap = BitmapFactory.decodeFile(path)
        val bitmap = decodedBitmap?.let { applyExifOrientation(path, it) }
            ?.copy(Bitmap.Config.ARGB_8888, true)
        if (bitmap == null) {
            Toast.makeText(this, "촬영 이미지를 읽을 수 없습니다.", Toast.LENGTH_LONG).show()
            finish()
            return
        }

        sourceBitmap = bitmap
        editorView.setSourceBitmap(bitmap)
        statusText.text = "손가락으로 상품 외곽을 한 바퀴 그린 뒤 배경 제거를 실행하세요."
    }

    private fun runGrabCut() {
        val source = sourceBitmap ?: return
        val maskBitmap = editorView.getSelectionMaskBitmap()
        val selectionBounds = editorView.getSelectionBoundsInBitmap()

        if (maskBitmap == null || selectionBounds == null) {
            Toast.makeText(this, "상품 외곽을 손가락으로 먼저 그려주세요.", Toast.LENGTH_SHORT).show()
            return
        }
        if (selectionBounds.width() < MIN_MASK_SIZE || selectionBounds.height() < MIN_MASK_SIZE) {
            Toast.makeText(this, "그린 영역이 너무 작습니다.", Toast.LENGTH_SHORT).show()
            return
        }

        runButton.isEnabled = false
        runButton.alpha = 0.45f
        statusText.text = "OpenCV GrabCut으로 배경을 제거하는 중입니다."

        lifecycleScope.launch {
            val result = withContext(Dispatchers.Default) {
                applyGrabCut(source, maskBitmap)
            }

            maskBitmap.recycle()
            resultBitmap = result
            editorView.setResultBitmap(result)
            setConfirmEnabled(true)
            runButton.isEnabled = true
            runButton.alpha = 1f
            statusText.text = "배경이 제대로 지워졌나요? 이대로 확정할까요?"
        }
    }

    private fun applyGrabCut(source: Bitmap, userMaskBitmap: Bitmap): Bitmap {
        val input = Mat()
        Utils.bitmapToMat(source, input)
        Imgproc.cvtColor(input, input, Imgproc.COLOR_RGBA2RGB)

        val mask = Mat(input.size(), CvType.CV_8UC1, Scalar(Imgproc.GC_BGD.toDouble()))
        val bgdModel = Mat()
        val fgdModel = Mat()

        val maskPixels = IntArray(source.width * source.height)
        userMaskBitmap.getPixels(maskPixels, 0, source.width, 0, 0, source.width, source.height)
        val grabCutMaskBytes = ByteArray(maskPixels.size)
        for (index in maskPixels.indices) {
            grabCutMaskBytes[index] = if (Color.red(maskPixels[index]) > 0) {
                Imgproc.GC_PR_FGD.toByte()
            } else {
                Imgproc.GC_BGD.toByte()
            }
        }
        mask.put(0, 0, grabCutMaskBytes)

        val fullImageRect = Rect(0, 0, input.cols(), input.rows())
        Imgproc.grabCut(input, mask, fullImageRect, bgdModel, fgdModel, GRABCUT_ITERATIONS, Imgproc.GC_INIT_WITH_MASK)

        val output = Bitmap.createBitmap(source.width, source.height, Bitmap.Config.ARGB_8888)
        val maskBytes = ByteArray((mask.total() * mask.channels()).toInt())
        mask.get(0, 0, maskBytes)

        val pixels = IntArray(source.width * source.height)
        source.getPixels(pixels, 0, source.width, 0, 0, source.width, source.height)

        for (index in pixels.indices) {
            val value = maskBytes[index].toInt() and 0xFF
            val isForeground = value == Imgproc.GC_FGD || value == Imgproc.GC_PR_FGD
            if (!isForeground) {
                pixels[index] = Color.TRANSPARENT
            }
        }

        output.setPixels(pixels, 0, source.width, 0, 0, source.width, source.height)

        input.release()
        mask.release()
        bgdModel.release()
        fgdModel.release()

        return output
    }

    private fun applyExifOrientation(imagePath: String, bitmap: Bitmap): Bitmap {
        val orientation = runCatching {
            ExifInterface(imagePath).getAttributeInt(
                ExifInterface.TAG_ORIENTATION,
                ExifInterface.ORIENTATION_NORMAL
            )
        }.getOrDefault(ExifInterface.ORIENTATION_NORMAL)

        val matrix = Matrix()
        when (orientation) {
            ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> matrix.setScale(-1f, 1f)
            ExifInterface.ORIENTATION_ROTATE_180 -> matrix.setRotate(180f)
            ExifInterface.ORIENTATION_FLIP_VERTICAL -> matrix.setScale(1f, -1f)
            ExifInterface.ORIENTATION_TRANSPOSE -> {
                matrix.setRotate(90f)
                matrix.postScale(-1f, 1f)
            }
            ExifInterface.ORIENTATION_ROTATE_90 -> matrix.setRotate(90f)
            ExifInterface.ORIENTATION_TRANSVERSE -> {
                matrix.setRotate(270f)
                matrix.postScale(-1f, 1f)
            }
            ExifInterface.ORIENTATION_ROTATE_270 -> matrix.setRotate(270f)
        }
        if (matrix.isIdentity) return bitmap

        val transformed = Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
        if (transformed != bitmap) {
            bitmap.recycle()
        }
        return transformed
    }

    private fun saveRegisteredProductImage() {
        val id = productId
        val bitmap = resultBitmap
        if (id.isNullOrBlank()) {
            Toast.makeText(this, "선택한 상품 정보를 찾을 수 없습니다.", Toast.LENGTH_SHORT).show()
            return
        }
        if (bitmap == null) {
            Toast.makeText(this, "확정할 배경 제거 이미지가 없습니다.", Toast.LENGTH_SHORT).show()
            return
        }

        RegisteredProductImageStore(this).saveProductImage(id, bitmap)
        val label = productName ?: "상품"
        Toast.makeText(this, "$label 사진이 등록되었습니다.", Toast.LENGTH_LONG).show()
        setResult(Activity.RESULT_OK)
        val homeIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra(MainActivity.EXTRA_SHOW_HOME, true)
        }
        startActivity(homeIntent)
        finish()
    }

    private fun setConfirmEnabled(enabled: Boolean) {
        confirmButton.isEnabled = enabled
        confirmButton.alpha = if (enabled) 1f else 0.45f
    }

    override fun onDestroy() {
        super.onDestroy()
        if (isFinishing) {
            imagePath?.let { path -> File(path).delete() }
        }
    }

    companion object {
        const val EXTRA_IMAGE_PATH = "extra_image_path"
        const val EXTRA_PRODUCT_ID = "extra_product_id"
        const val EXTRA_PRODUCT_NAME = "extra_product_name"
        private const val GRABCUT_ITERATIONS = 5
        private const val MIN_MASK_SIZE = 40f
    }
}
