package com.coworker.jjikmuk.feature.scanner

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Matrix
import android.media.ExifInterface
import android.net.Uri
import android.os.Bundle
import android.util.Base64
import android.view.View
import android.widget.ImageButton
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.core.TorchState
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.lifecycleScope
import com.coworker.jjikmuk.BuildConfig
import com.coworker.jjikmuk.R
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.math.max
import kotlin.math.sqrt
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject

class ProductPhotoCaptureActivity : AppCompatActivity() {
    private lateinit var previewView: PreviewView
    private lateinit var flashButton: ImageButton
    private lateinit var galleryButton: View
    private lateinit var captureButton: View
    private lateinit var statusText: TextView
    private lateinit var resultScrim: View
    private lateinit var resultSheet: View
    private lateinit var decisionText: TextView
    private lateinit var reasonText: TextView
    private lateinit var metricsText: TextView
    private lateinit var confirmButton: TextView
    private lateinit var retakeButton: TextView
    private lateinit var cameraExecutor: ExecutorService

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(GEMINI_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .readTimeout(GEMINI_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .callTimeout(GEMINI_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .build()

    private var camera: Camera? = null
    private var imageCapture: ImageCapture? = null
    private var isFlashEnabled = false
    private var isCaptureInFlight = false
    private var capturedBarcode: String? = null
    private var selectedProductId: String? = null
    private var selectedProductName: String? = null
    private var latestAcceptedPhotoPath: String? = null

    private val cameraPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted ->
        if (isGranted) {
            startCamera()
        } else {
            Toast.makeText(this, "카메라 권한이 필요합니다.", Toast.LENGTH_SHORT).show()
            setResult(Activity.RESULT_CANCELED)
            finish()
        }
    }

    private val galleryImagePicker = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri ->
        if (uri != null) {
            validateGalleryImage(uri)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        configureSystemBars()
        setContentView(R.layout.activity_product_photo_capture)

        capturedBarcode = intent.getStringExtra(EXTRA_BARCODE)
        selectedProductId = intent.getStringExtra(EXTRA_PRODUCT_ID)
        selectedProductName = intent.getStringExtra(EXTRA_PRODUCT_NAME)
        previewView = findViewById(R.id.productPhotoPreviewView)
        flashButton = findViewById(R.id.btnProductPhotoFlash)
        galleryButton = findViewById(R.id.btnProductPhotoGallery)
        captureButton = findViewById(R.id.btnProductPhotoCapture)
        statusText = findViewById(R.id.tvProductPhotoStatus)
        resultScrim = findViewById(R.id.productPhotoResultScrim)
        resultSheet = findViewById(R.id.productPhotoResultSheet)
        decisionText = findViewById(R.id.tvProductPhotoDecision)
        reasonText = findViewById(R.id.tvProductPhotoReason)
        metricsText = findViewById(R.id.tvProductPhotoMetrics)
        confirmButton = findViewById(R.id.btnProductPhotoConfirm)
        retakeButton = findViewById(R.id.btnProductPhotoRetake)
        cameraExecutor = Executors.newSingleThreadExecutor()

        findViewById<ImageButton>(R.id.btnProductPhotoClose).setOnClickListener {
            setResult(Activity.RESULT_CANCELED)
            finish()
        }
        flashButton.setOnClickListener {
            toggleFlash()
        }
        captureButton.setOnClickListener {
            captureAndValidate()
        }
        galleryButton.setOnClickListener {
            openGalleryPicker()
        }
        retakeButton.setOnClickListener {
            hideResultSheet()
        }
        confirmButton.setOnClickListener {
            val imagePath = latestAcceptedPhotoPath
            if (imagePath == null) {
                Toast.makeText(this, "확정할 촬영 이미지가 없습니다.", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            val intent = Intent(this, ProductGrabCutActivity::class.java).apply {
                putExtra(ProductGrabCutActivity.EXTRA_IMAGE_PATH, imagePath)
                putExtra(ProductGrabCutActivity.EXTRA_PRODUCT_ID, selectedProductId)
                putExtra(ProductGrabCutActivity.EXTRA_PRODUCT_NAME, selectedProductName)
            }
            startActivity(intent)
        }

        updateFlashButtonTint()
        updateStatus("선택한 제품의 앞면을 안내선 안에 꽉 차도록 촬영해 주세요.")

        if (hasCameraPermission()) {
            startCamera()
        } else {
            cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    private fun configureSystemBars() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        window.statusBarColor = Color.BLACK
        window.navigationBarColor = Color.WHITE
        WindowInsetsControllerCompat(window, window.decorView).apply {
            isAppearanceLightStatusBars = false
            isAppearanceLightNavigationBars = true
        }
    }

    private fun hasCameraPermission(): Boolean {
        return ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.CAMERA
        ) == PackageManager.PERMISSION_GRANTED
    }

    private fun startCamera() {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(this)
        cameraProviderFuture.addListener({
            val cameraProvider = cameraProviderFuture.get()
            val preview = Preview.Builder().build().also {
                it.setSurfaceProvider(previewView.surfaceProvider)
            }
            imageCapture = ImageCapture.Builder()
                .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                .build()

            cameraProvider.unbindAll()
            val boundCamera = cameraProvider.bindToLifecycle(
                this,
                CameraSelector.DEFAULT_BACK_CAMERA,
                preview,
                imageCapture
            )
            camera = boundCamera
            flashButton.isEnabled = boundCamera.cameraInfo.hasFlashUnit()
            boundCamera.cameraInfo.torchState.observe(this) { torchState ->
                isFlashEnabled = torchState == TorchState.ON
                updateFlashButtonTint()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun captureAndValidate() {
        if (isCaptureInFlight) return

        val imageCapture = imageCapture ?: return
        isCaptureInFlight = true
        captureButton.isEnabled = false
        galleryButton.isEnabled = false
        updateStatus("사진을 촬영하고 자동검수를 진행 중입니다.")

        val photoFile = File.createTempFile("product_photo_", ".jpg", cacheDir)
        val outputOptions = ImageCapture.OutputFileOptions.Builder(photoFile).build()
        imageCapture.takePicture(
            outputOptions,
            cameraExecutor,
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(outputFileResults: ImageCapture.OutputFileResults) {
                    lifecycleScope.launch {
                        val result = validatePhoto(photoFile)
                        if (result.decision == "retake" || result.decision == "error") {
                            photoFile.delete()
                            latestAcceptedPhotoPath = null
                        } else {
                            latestAcceptedPhotoPath?.let { oldPath ->
                                if (oldPath != photoFile.absolutePath) {
                                    File(oldPath).delete()
                                }
                            }
                            latestAcceptedPhotoPath = photoFile.absolutePath
                        }
                        isCaptureInFlight = false
                        captureButton.isEnabled = true
                        galleryButton.isEnabled = true
                        showValidationResult(result)
                    }
                }

                override fun onError(exception: ImageCaptureException) {
                    photoFile.delete()
                    runOnUiThread {
                        isCaptureInFlight = false
                        captureButton.isEnabled = true
                        galleryButton.isEnabled = true
                        updateStatus("촬영에 실패했습니다. 다시 시도해 주세요.")
                        Toast.makeText(
                            this@ProductPhotoCaptureActivity,
                            exception.message ?: "촬영 실패",
                            Toast.LENGTH_SHORT
                        ).show()
                    }
                }
            }
        )
    }

    private fun openGalleryPicker() {
        if (isCaptureInFlight) return
        galleryImagePicker.launch("image/*")
    }

    private fun validateGalleryImage(uri: Uri) {
        if (isCaptureInFlight) return

        val photoFile = runCatching {
            createTempFileFromGalleryImage(uri)
        }.getOrElse { error ->
            Toast.makeText(
                this,
                error.message ?: "갤러리 이미지를 읽을 수 없습니다.",
                Toast.LENGTH_SHORT
            ).show()
            return
        }

        isCaptureInFlight = true
        captureButton.isEnabled = false
        galleryButton.isEnabled = false
        updateStatus("갤러리 사진으로 자동검수를 진행 중입니다.")

        lifecycleScope.launch {
            val result = validatePhoto(photoFile)
            if (result.decision == "retake" || result.decision == "error") {
                photoFile.delete()
                latestAcceptedPhotoPath = null
            } else {
                latestAcceptedPhotoPath?.let { oldPath ->
                    if (oldPath != photoFile.absolutePath) {
                        File(oldPath).delete()
                    }
                }
                latestAcceptedPhotoPath = photoFile.absolutePath
            }
            isCaptureInFlight = false
            captureButton.isEnabled = true
            galleryButton.isEnabled = true
            showValidationResult(result)
        }
    }

    private fun createTempFileFromGalleryImage(uri: Uri): File {
        val bitmap = loadGalleryBitmap(uri)
            ?: throw IllegalStateException("갤러리 이미지를 읽을 수 없습니다.")
        val photoFile = File.createTempFile("product_gallery_photo_", ".jpg", cacheDir)
        photoFile.outputStream().use { outputStream ->
            bitmap.compress(Bitmap.CompressFormat.JPEG, GEMINI_JPEG_QUALITY, outputStream)
        }
        bitmap.recycle()
        return photoFile
    }

    private fun loadGalleryBitmap(uri: Uri): Bitmap? {
        val boundsOptions = BitmapFactory.Options().apply {
            inJustDecodeBounds = true
        }
        contentResolver.openInputStream(uri)?.use { inputStream ->
            BitmapFactory.decodeStream(inputStream, null, boundsOptions)
        }

        val imageWidth = boundsOptions.outWidth
        val imageHeight = boundsOptions.outHeight
        if (imageWidth <= 0 || imageHeight <= 0) return null

        val decodeOptions = BitmapFactory.Options().apply {
            inSampleSize = calculateImageSampleSize(imageWidth, imageHeight, GALLERY_MAX_IMAGE_SIZE)
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        val decoded = contentResolver.openInputStream(uri)?.use { inputStream ->
            BitmapFactory.decodeStream(inputStream, null, decodeOptions)
        } ?: return null

        return applyExifOrientation(uri, decoded)
    }

    private fun applyExifOrientation(uri: Uri, bitmap: Bitmap): Bitmap {
        val orientation = runCatching {
            contentResolver.openInputStream(uri)?.use { inputStream ->
                ExifInterface(inputStream).getAttributeInt(
                    ExifInterface.TAG_ORIENTATION,
                    ExifInterface.ORIENTATION_NORMAL
                )
            } ?: ExifInterface.ORIENTATION_NORMAL
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

    private suspend fun validatePhoto(photoFile: File): ProductPhotoValidationResult {
        return withContext(Dispatchers.IO) {
            runCatching {
                val quality = analyzePhotoQuality(photoFile)
                if (quality.status == "fail") {
                    return@withContext ProductPhotoValidationResult(
                        decision = "retake",
                        reasons = quality.fails,
                        quality = quality,
                        geminiAnswer = null
                    )
                }

                val geminiAnswer = askGemini(photoFile)
                val isProductPhoto = parseYesNo(geminiAnswer)

                val finalDecision = when {
                    isProductPhoto == false -> "retake"
                    isProductPhoto == null -> "review"
                    quality.status == "warning" -> "review"
                    else -> "pass"
                }
                val reasons = when {
                    isProductPhoto == false -> listOf("상품이 보이는 사진으로 판단되지 않았습니다.")
                    isProductPhoto == null -> listOf("Gemini 응답을 yes/no로 해석하지 못했습니다.")
                    quality.status == "warning" -> quality.warnings
                    else -> listOf("상품이 보이고 기본 촬영 품질 기준을 통과했습니다.")
                }

                ProductPhotoValidationResult(
                    decision = finalDecision,
                    reasons = reasons,
                    quality = quality,
                    geminiAnswer = geminiAnswer
                )
            }.getOrElse { error ->
                ProductPhotoValidationResult(
                    decision = "error",
                    reasons = listOf(error.message ?: "자동검수 중 오류가 발생했습니다."),
                    quality = null,
                    geminiAnswer = null
                )
            }
        }
    }

    private fun analyzePhotoQuality(photoFile: File): PhotoQualityResult {
        val bitmap = decodeScaledBitmap(photoFile, QUALITY_MAX_IMAGE_SIZE)
            ?: throw IllegalStateException("이미지를 읽을 수 없습니다.")
        val width = bitmap.width
        val height = bitmap.height
        val gray = DoubleArray(width * height)

        var sum = 0.0
        var index = 0
        for (y in 0 until height) {
            for (x in 0 until width) {
                val pixel = bitmap.getPixel(x, y)
                val value = (
                    0.299 * Color.red(pixel) +
                        0.587 * Color.green(pixel) +
                        0.114 * Color.blue(pixel)
                    )
                gray[index] = value
                sum += value
                index += 1
            }
        }

        val brightness = sum / gray.size
        var contrastSum = 0.0
        for (value in gray) {
            val diff = value - brightness
            contrastSum += diff * diff
        }
        val contrast = sqrt(contrastSum / gray.size)

        val laplacians = ArrayList<Double>((width - 2) * (height - 2))
        for (y in 1 until height - 1) {
            for (x in 1 until width - 1) {
                val center = gray[y * width + x]
                val laplacian = (4 * center) -
                    gray[(y - 1) * width + x] -
                    gray[(y + 1) * width + x] -
                    gray[y * width + x - 1] -
                    gray[y * width + x + 1]
                laplacians.add(laplacian)
            }
        }
        val laplacianMean = laplacians.sum() / laplacians.size
        val blurScore = laplacians.sumOf { value ->
            val diff = value - laplacianMean
            diff * diff
        } / laplacians.size

        bitmap.recycle()

        val warnings = mutableListOf<String>()
        val fails = mutableListOf<String>()

        if (blurScore < BLUR_FAIL_THRESHOLD) {
            fails.add("이미지가 심하게 흔들렸거나 초점이 맞지 않았습니다.")
        } else if (blurScore < BLUR_WARNING_THRESHOLD) {
            warnings.add("이미지가 약간 흐릴 수 있습니다.")
        }

        if (brightness < DARK_FAIL_THRESHOLD) {
            fails.add("이미지가 너무 어둡습니다.")
        } else if (brightness > BRIGHT_FAIL_THRESHOLD) {
            fails.add("이미지가 너무 밝거나 과노출되었습니다.")
        }

        if (contrast < CONTRAST_WARNING_THRESHOLD) {
            warnings.add("대비가 낮아 상품이 선명하게 구분되지 않을 수 있습니다.")
        }

        val status = when {
            fails.isNotEmpty() -> "fail"
            warnings.isNotEmpty() -> "warning"
            else -> "pass"
        }

        return PhotoQualityResult(
            status = status,
            blurScore = blurScore,
            brightness = brightness,
            contrast = contrast,
            warnings = warnings,
            fails = fails
        )
    }

    private fun askGemini(photoFile: File): String {
        val apiKey = BuildConfig.GEMINI_API_KEY
        if (apiKey.isBlank()) {
            throw IllegalStateException("GEMINI_API_KEY가 설정되지 않았습니다.")
        }

        val imageBase64 = encodeImageForGemini(photoFile)
        val payload = JSONObject()
            .put(
                "contents",
                JSONArray().put(
                    JSONObject().put(
                        "parts",
                        JSONArray()
                            .put(
                                JSONObject().put(
                                    "inline_data",
                                    JSONObject()
                                        .put("mime_type", "image/jpeg")
                                        .put("data", imageBase64)
                                )
                            )
                            .put(JSONObject().put("text", GEMINI_PRODUCT_PROMPT))
                    )
                )
            )
            .put(
                "generationConfig",
                JSONObject()
                    .put("temperature", 0)
                    .put("maxOutputTokens", 8)
            )

        val model = BuildConfig.GEMINI_MODEL.removePrefix("models/")
        val request = Request.Builder()
            .url("https://generativelanguage.googleapis.com/v1beta/models/$model:generateContent")
            .addHeader("x-goog-api-key", apiKey)
            .addHeader("Content-Type", "application/json")
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()

        httpClient.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IllegalStateException("Gemini API 오류 HTTP ${response.code}: $body")
            }
            return extractGeminiText(body)
        }
    }

    private fun encodeImageForGemini(photoFile: File): String {
        val bitmap = decodeScaledBitmap(photoFile, GEMINI_MAX_IMAGE_SIZE)
            ?: throw IllegalStateException("Gemini 전송용 이미지를 읽을 수 없습니다.")
        val outputStream = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, GEMINI_JPEG_QUALITY, outputStream)
        bitmap.recycle()
        return Base64.encodeToString(outputStream.toByteArray(), Base64.NO_WRAP)
    }

    private fun decodeScaledBitmap(file: File, maxSize: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply {
            inJustDecodeBounds = true
        }
        BitmapFactory.decodeFile(file.absolutePath, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null

        var sampleSize = 1
        while (
            bounds.outWidth / sampleSize > maxSize ||
            bounds.outHeight / sampleSize > maxSize
        ) {
            sampleSize *= 2
        }

        val options = BitmapFactory.Options().apply {
            inSampleSize = sampleSize
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        return BitmapFactory.decodeFile(file.absolutePath, options)
    }

    private fun calculateImageSampleSize(width: Int, height: Int, maxSize: Int): Int {
        var sampleSize = 1
        while (max(width, height) / sampleSize > maxSize) {
            sampleSize *= 2
        }
        return sampleSize
    }

    private fun extractGeminiText(body: String): String {
        val root = JSONObject(body)
        val candidates = root.optJSONArray("candidates") ?: return ""
        val firstCandidate = candidates.optJSONObject(0) ?: return ""
        val content = firstCandidate.optJSONObject("content") ?: return ""
        val parts = content.optJSONArray("parts") ?: return ""

        return buildString {
            for (index in 0 until parts.length()) {
                val text = parts.optJSONObject(index)?.optString("text").orEmpty()
                append(text)
            }
        }.trim()
    }

    private fun parseYesNo(response: String?): Boolean? {
        val text = response?.lowercase().orEmpty()
        if (text.isBlank()) return null

        val hasYes = Regex("\\byes\\b").containsMatchIn(text)
        val hasNo = Regex("\\bno\\b").containsMatchIn(text)

        return when {
            hasYes && !hasNo -> true
            hasNo && !hasYes -> false
            else -> null
        }
    }

    private fun showValidationResult(result: ProductPhotoValidationResult) {
        val normalizedDecision = result.decision.lowercase()
        val title = when (normalizedDecision) {
            "pass" -> "PASS"
            "retake" -> "RETAKE"
            "review" -> "REVIEW"
            else -> "ERROR"
        }
        val titleColor = when (normalizedDecision) {
            "pass" -> "#16A635"
            "review" -> "#7A73FF"
            else -> "#D96B5F"
        }

        decisionText.text = title
        decisionText.setTextColor(Color.parseColor(titleColor))
        reasonText.text = result.reasons.joinToString("\n")
        metricsText.text = result.formatMetrics()
        confirmButton.visibility = if (normalizedDecision == "retake" || normalizedDecision == "error") {
            View.GONE
        } else {
            View.VISIBLE
        }
        confirmButton.text = if (normalizedDecision == "pass") {
            "테두리 지정 단계로 이동"
        } else {
            "검토 후 테두리 지정"
        }
        updateStatus("자동검수 결과를 확인해 주세요.")

        resultScrim.visibility = View.VISIBLE
        resultSheet.visibility = View.VISIBLE
    }

    private fun hideResultSheet() {
        resultScrim.visibility = View.GONE
        resultSheet.visibility = View.GONE
        latestAcceptedPhotoPath?.let { path -> File(path).delete() }
        latestAcceptedPhotoPath = null
        updateStatus("선택한 제품의 앞면을 안내선 안에 꽉 차도록 다시 촬영해 주세요.")
    }

    private fun toggleFlash() {
        val boundCamera = camera ?: return
        if (!boundCamera.cameraInfo.hasFlashUnit()) return

        isFlashEnabled = !isFlashEnabled
        updateFlashButtonTint()
        boundCamera.cameraControl.enableTorch(isFlashEnabled)
    }

    private fun updateFlashButtonTint() {
        val tintColor = if (isFlashEnabled) FLASH_ON_COLOR else Color.WHITE
        flashButton.setColorFilter(tintColor)
    }

    private fun updateStatus(message: String) {
        val barcodePrefix = capturedBarcode?.let { "바코드 $it\n" }.orEmpty()
        val productPrefix = selectedProductName?.let { "선택 제품: $it\n" }.orEmpty()
        statusText.text = barcodePrefix + productPrefix + message
    }

    override fun onDestroy() {
        super.onDestroy()
        camera?.cameraControl?.enableTorch(false)
        cameraExecutor.shutdown()
        if (isFinishing) {
            latestAcceptedPhotoPath?.let { path -> File(path).delete() }
        }
    }

    private data class PhotoQualityResult(
        val status: String,
        val blurScore: Double,
        val brightness: Double,
        val contrast: Double,
        val warnings: List<String>,
        val fails: List<String>
    )

    private data class ProductPhotoValidationResult(
        val decision: String,
        val reasons: List<String>,
        val quality: PhotoQualityResult?,
        val geminiAnswer: String?
    ) {
        fun formatMetrics(): String {
            val qualityText = if (quality == null) {
                "blur - · brightness - · contrast -"
            } else {
                listOf(
                    "blur ${quality.blurScore.formatMetric()}",
                    "brightness ${quality.brightness.formatMetric()}",
                    "contrast ${quality.contrast.formatMetric()}"
                ).joinToString(" · ")
            }
            val answerText = geminiAnswer?.let { "\nGemini: $it" }.orEmpty()
            return qualityText + answerText
        }

        private fun Double.formatMetric(): String {
            return String.format("%.1f", this)
        }
    }

    companion object {
        const val EXTRA_BARCODE = "extra_barcode"
        const val EXTRA_PRODUCT_ID = "extra_product_id"
        const val EXTRA_PRODUCT_NAME = "extra_product_name"
        private const val GEMINI_PRODUCT_PROMPT = "Does the photo show the product? Answer yes or no."
        private const val GEMINI_TIMEOUT_SECONDS = 30L
        private const val QUALITY_MAX_IMAGE_SIZE = 1024
        private const val GEMINI_MAX_IMAGE_SIZE = 1600
        private const val GALLERY_MAX_IMAGE_SIZE = 2200
        private const val GEMINI_JPEG_QUALITY = 85
        private const val BLUR_FAIL_THRESHOLD = 80.0
        private const val BLUR_WARNING_THRESHOLD = 150.0
        private const val DARK_FAIL_THRESHOLD = 40.0
        private const val BRIGHT_FAIL_THRESHOLD = 240.0
        private const val CONTRAST_WARNING_THRESHOLD = 25.0
        private val FLASH_ON_COLOR: Int = Color.parseColor("#FFDD4A")
    }
}
