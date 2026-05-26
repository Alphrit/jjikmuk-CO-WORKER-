package com.coworker.jjikmuk.feature.scanner

import android.Manifest
import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.app.Activity
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.RectF
import android.media.ExifInterface
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.view.MotionEvent
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.annotation.OptIn
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.TorchState
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.coworker.jjikmuk.R
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import dagger.hilt.android.AndroidEntryPoint
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import kotlin.math.max
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@AndroidEntryPoint
class BarcodeScannerActivity : AppCompatActivity() {
    private lateinit var scannerRoot: View
    private lateinit var scannerContent: View
    private lateinit var previewView: PreviewView
    private lateinit var galleryScanContainer: View
    private lateinit var galleryImageView: ImageView
    private lateinit var galleryBarcodeOverlay: GalleryBarcodeOverlayView
    private lateinit var galleryReselectButton: TextView
    private lateinit var gallerySelectBarcodeButton: TextView
    private lateinit var flashButton: ImageButton
    private lateinit var scanGuideView: ScanGuideView
    private lateinit var scanCaptureButton: View
    private lateinit var previousScansButton: View
    private lateinit var resultScrim: View
    private lateinit var resultSheet: View
    private lateinit var registrationMessageText: TextView
    private lateinit var resultSafetyText: TextView
    private lateinit var resultProductNameText: TextView
    private lateinit var resultAnalysisText: TextView
    private lateinit var nutritionLayout: LinearLayout
    private lateinit var caloriesText: TextView
    private lateinit var sugarText: TextView
    private lateinit var fatText: TextView
    private lateinit var sodiumText: TextView
    private lateinit var resultPrimaryButton: TextView
    private lateinit var retakeButton: TextView
    private lateinit var cameraExecutor: ExecutorService
    private var camera: Camera? = null
    private var scanTimeoutJob: Job? = null
    private var lastScanResult: ScannerResult? = null
    private var renderedResultSequence = 0
    private var selectedGalleryBarcode: String? = null
    private var galleryImageSequence = 0
    private var isFlashEnabled = false

    @Volatile private var isScanAttemptActive = false
    @Volatile private var isBarcodeLookupInFlight = false

    private val viewModel: ScannerViewModel by viewModels()

    private val cameraPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted ->
        if (isGranted) {
            startCamera()
        } else {
            Toast.makeText(this, "Camera permission is required.", Toast.LENGTH_SHORT).show()
            setResult(Activity.RESULT_CANCELED)
            finish()
        }
    }

    private val galleryImagePicker = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri ->
        if (uri != null) {
            handleGalleryImage(uri)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        configureSystemBars()
        setContentView(R.layout.activity_barcode_scanner)

        scannerRoot = findViewById(R.id.scannerRoot)
        scannerContent = findViewById(R.id.scannerContent)
        previewView = findViewById(R.id.barcodePreviewView)
        galleryScanContainer = findViewById(R.id.galleryScanContainer)
        galleryImageView = findViewById(R.id.ivGalleryScanImage)
        galleryBarcodeOverlay = findViewById(R.id.galleryBarcodeOverlay)
        galleryReselectButton = findViewById(R.id.btnGalleryReselect)
        gallerySelectBarcodeButton = findViewById(R.id.btnGallerySelectBarcode)
        flashButton = findViewById(R.id.btnScannerFlash)
        scanGuideView = findViewById(R.id.scanGuideView)
        scanCaptureButton = findViewById(R.id.btnScanCapture)
        previousScansButton = findViewById(R.id.layoutPreviousScans)
        resultScrim = findViewById(R.id.scannerResultScrim)
        resultSheet = findViewById(R.id.scannerResultSheet)
        registrationMessageText = findViewById(R.id.tvRegistrationMessage)
        resultSafetyText = findViewById(R.id.tvScanResultSafety)
        resultProductNameText = findViewById(R.id.tvScanResultProductName)
        resultAnalysisText = findViewById(R.id.tvScanResultAnalysis)
        nutritionLayout = findViewById(R.id.layoutScanNutrition)
        caloriesText = findViewById(R.id.tvScanCalories)
        sugarText = findViewById(R.id.tvScanSugar)
        fatText = findViewById(R.id.tvScanFat)
        sodiumText = findViewById(R.id.tvScanSodium)
        resultPrimaryButton = findViewById(R.id.btnScanResultPrimary)
        retakeButton = findViewById(R.id.btnScanRetake)

        findViewById<ImageButton>(R.id.btnScannerClose).setOnClickListener {
            setResult(Activity.RESULT_CANCELED)
            finish()
        }
        flashButton.setOnClickListener {
            toggleFlash()
        }
        updateFlashButtonTint()
        scanCaptureButton.setOnClickListener {
            startScanAttempt()
        }
        findViewById<View>(R.id.btnScanGallery).setOnClickListener {
            openGalleryPicker()
        }
        previousScansButton.setOnClickListener {
            lastScanResult?.let { result ->
                showResultSheet(result)
            }
        }
        retakeButton.setOnClickListener {
            hideResultSheet(showPreviousScans = true)
        }
        galleryReselectButton.setOnClickListener {
            openGalleryPicker()
        }
        gallerySelectBarcodeButton.setOnClickListener {
            selectedGalleryBarcode?.let { barcode ->
                submitGalleryBarcode(barcode)
            }
        }
        galleryBarcodeOverlay.onBarcodeSelected = { candidate ->
            selectedGalleryBarcode = candidate?.rawValue
            updateGallerySelectButton(isEnabled = candidate != null)
        }

        setupResultSheetDrag()
        applySystemBarInsets()
        cameraExecutor = Executors.newSingleThreadExecutor()
        collectScannerState()

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

    private fun applySystemBarInsets() {
        ViewCompat.setOnApplyWindowInsetsListener(scannerRoot) { view, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(view.paddingLeft, view.paddingTop, view.paddingRight, 0)
            setScannerContentBottomInset(systemBars.bottom)
            insets
        }
        ViewCompat.requestApplyInsets(scannerRoot)
    }

    private fun setScannerContentBottomInset(bottomInset: Int) {
        val params = scannerContent.layoutParams as? FrameLayout.LayoutParams ?: return
        if (params.bottomMargin == bottomInset) return
        params.bottomMargin = bottomInset
        scannerContent.layoutParams = params
    }

    private fun openGalleryPicker() {
        if (isBarcodeLookupInFlight) return
        stopScanAttempt()
        galleryImagePicker.launch("image/*")
    }

    private fun handleGalleryImage(uri: Uri) {
        val bitmap = loadGalleryBitmap(uri)
        if (bitmap == null) {
            Toast.makeText(this, "이미지를 불러오지 못했습니다.", Toast.LENGTH_SHORT).show()
            return
        }

        galleryImageSequence += 1
        val imageSequence = galleryImageSequence
        selectedGalleryBarcode = null
        galleryImageView.setImageBitmap(bitmap)
        galleryImageView.setColorFilter(Color.argb(46, 120, 120, 120))
        galleryBarcodeOverlay.showBarcodes(
            imageWidth = bitmap.width,
            imageHeight = bitmap.height,
            candidates = emptyList()
        )
        updateGallerySelectButton(isEnabled = false)
        galleryScanContainer.visibility = View.VISIBLE
        previousScansButton.visibility = View.GONE

        detectBarcodesFromGalleryBitmap(bitmap, imageSequence)
    }

    private fun detectBarcodesFromGalleryBitmap(bitmap: Bitmap, imageSequence: Int) {
        val inputImage = InputImage.fromBitmap(bitmap, 0)
        BarcodeScanning.getClient().process(inputImage)
            .addOnSuccessListener { barcodes ->
                if (imageSequence != galleryImageSequence) return@addOnSuccessListener

                val candidates = extractGalleryBarcodeCandidates(barcodes)
                galleryBarcodeOverlay.showBarcodes(
                    imageWidth = bitmap.width,
                    imageHeight = bitmap.height,
                    candidates = candidates
                )
                if (candidates.isEmpty()) {
                    Toast.makeText(this, "이미지에서 바코드를 찾지 못했습니다.", Toast.LENGTH_SHORT).show()
                }
            }
            .addOnFailureListener { exception ->
                if (imageSequence != galleryImageSequence) return@addOnFailureListener

                Log.d(TAG, "Gallery barcode detection failed", exception)
                galleryBarcodeOverlay.showBarcodes(
                    imageWidth = bitmap.width,
                    imageHeight = bitmap.height,
                    candidates = emptyList()
                )
                Toast.makeText(this, "이미지 바코드 스캔에 실패했습니다.", Toast.LENGTH_SHORT).show()
            }
    }

    private fun extractGalleryBarcodeCandidates(
        barcodes: List<Barcode>
    ): List<GalleryBarcodeCandidate> {
        return barcodes.mapNotNull { barcode ->
            val rawValue = barcode.rawValue
                ?.toScanBarcode()
                ?: return@mapNotNull null
            val bounds = barcode.boundingBox ?: return@mapNotNull null
            if (bounds.width() <= 0 || bounds.height() <= 0) return@mapNotNull null

            GalleryBarcodeCandidate(
                rawValue = rawValue,
                imageBounds = RectF(bounds)
            )
        }
    }

    private fun submitGalleryBarcode(barcode: String) {
        if (isBarcodeLookupInFlight) return

        isBarcodeLookupInFlight = true
        hideGalleryScanMode()
        Log.d(TAG, "Submitting gallery barcode lookup: $barcode")
        viewModel.submitBarcode(barcode)
    }

    private fun hideGalleryScanMode() {
        galleryScanContainer.visibility = View.GONE
        galleryImageView.setImageDrawable(null)
        galleryImageView.clearColorFilter()
        galleryBarcodeOverlay.clear()
        selectedGalleryBarcode = null
        updateGallerySelectButton(isEnabled = false)
    }

    private fun updateGallerySelectButton(isEnabled: Boolean) {
        gallerySelectBarcodeButton.isEnabled = isEnabled
        gallerySelectBarcodeButton.alpha = if (isEnabled) 1f else DISABLED_BUTTON_ALPHA
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
            inSampleSize = calculateImageSampleSize(imageWidth, imageHeight)
        }
        val decoded = contentResolver.openInputStream(uri)?.use { inputStream ->
            BitmapFactory.decodeStream(inputStream, null, decodeOptions)
        } ?: return null

        return applyExifOrientation(uri, decoded)
    }

    private fun applyExifOrientation(uri: Uri, bitmap: Bitmap): Bitmap {
        val orientation = try {
            contentResolver.openInputStream(uri)?.use { inputStream ->
                ExifInterface(inputStream).getAttributeInt(
                    ExifInterface.TAG_ORIENTATION,
                    ExifInterface.ORIENTATION_NORMAL
                )
            } ?: ExifInterface.ORIENTATION_NORMAL
        } catch (exception: Exception) {
            Log.d(TAG, "Unable to read gallery image EXIF orientation", exception)
            ExifInterface.ORIENTATION_NORMAL
        }

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

    private fun calculateImageSampleSize(width: Int, height: Int): Int {
        var sampleSize = 1
        while (max(width, height) / sampleSize > MAX_GALLERY_IMAGE_SIZE_PX) {
            sampleSize *= 2
        }
        return sampleSize
    }

    private fun collectScannerState() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch {
                    viewModel.uiState.collect { state ->
                        val result = state.scanResult
                        if (result != null && state.resultSequence > renderedResultSequence) {
                            renderedResultSequence = state.resultSequence
                            isBarcodeLookupInFlight = false
                            lastScanResult = result
                            showResultSheet(result)
                        } else if (!state.isLoading && result == null) {
                            isBarcodeLookupInFlight = false
                        }
                    }
                }

                launch {
                    viewModel.effect.collect { effect ->
                        when (effect) {
                            is ScannerUiEffect.ShowToast -> {
                                Toast.makeText(
                                    this@BarcodeScannerActivity,
                                    effect.message,
                                    Toast.LENGTH_SHORT
                                ).show()
                            }
                        }
                    }
                }
            }
        }
    }

    private fun startScanAttempt() {
        if (isScanAttemptActive || isBarcodeLookupInFlight) return

        Log.d(TAG, "Scan attempt started")
        isScanAttemptActive = true
        previousScansButton.visibility = View.GONE
        scanGuideView.startScanAnimation()
        scanTimeoutJob?.cancel()
        scanTimeoutJob = lifecycleScope.launch {
            delay(SCAN_TIMEOUT_MS)
            if (isScanAttemptActive) {
                Log.d(TAG, "Scan attempt timed out")
                isScanAttemptActive = false
                scanGuideView.stopScanAnimation()
                if (lastScanResult != null) {
                    previousScansButton.visibility = View.VISIBLE
                }
                Toast.makeText(
                    this@BarcodeScannerActivity,
                    "스캔에 실패하였습니다. 다시 시도해주세요.",
                    Toast.LENGTH_SHORT
                ).show()
            }
        }
    }

    private fun stopScanAttempt() {
        isScanAttemptActive = false
        scanTimeoutJob?.cancel()
        scanTimeoutJob = null
        scanGuideView.stopScanAnimation()
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
            val imageAnalyzer = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
                .also {
                    it.setAnalyzer(cameraExecutor) { imageProxy ->
                        processImageProxy(imageProxy)
                    }
                }

            cameraProvider.unbindAll()
            val boundCamera = cameraProvider.bindToLifecycle(
                this,
                CameraSelector.DEFAULT_BACK_CAMERA,
                preview,
                imageAnalyzer
            )
            camera = boundCamera
            flashButton.isEnabled = boundCamera.cameraInfo.hasFlashUnit()
            boundCamera.cameraInfo.torchState.observe(this) { torchState ->
                isFlashEnabled = torchState == TorchState.ON
                updateFlashButtonTint()
            }
        }, ContextCompat.getMainExecutor(this))
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

    @OptIn(ExperimentalGetImage::class)
    private fun processImageProxy(imageProxy: ImageProxy) {
        val mediaImage = imageProxy.image
        if (mediaImage == null || !isScanAttemptActive || isBarcodeLookupInFlight) {
            imageProxy.close()
            return
        }

        val inputImage = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)
        BarcodeScanning.getClient().process(inputImage)
            .addOnSuccessListener { barcodes ->
                val barcode = barcodes
                    .asSequence()
                    .mapNotNull { it.rawValue }
                    .mapNotNull { rawValue -> rawValue.toScanBarcode() }
                    .firstOrNull()

                if (barcode != null) {
                    Log.d(TAG, "Barcode detected: $barcode")
                    returnBarcode(barcode)
                }
            }
            .addOnCompleteListener {
                imageProxy.close()
            }
    }

    private fun returnBarcode(barcode: String) {
        if (!isScanAttemptActive || isBarcodeLookupInFlight) return

        isBarcodeLookupInFlight = true
        stopScanAttempt()
        Log.d(TAG, "Submitting barcode lookup: $barcode")
        viewModel.submitBarcode(barcode)
    }

    private fun showResultSheet(result: ScannerResult) {
        Log.d(
            TAG,
            "Showing result sheet: barcode=${result.barcode}, requiresRegistration=${result.requiresRegistration}"
        )
        bindResultSheet(result)
        previousScansButton.visibility = View.GONE

        resultScrim.visibility = View.VISIBLE
        resultScrim.alpha = 0f
        resultScrim.animate().alpha(1f).setDuration(SHEET_ANIMATION_MS).start()

        resultSheet.visibility = View.VISIBLE
        resultSheet.post {
            resultSheet.translationY = resultSheet.height.toFloat()
            resultSheet.animate()
                .translationY(0f)
                .setDuration(SHEET_ANIMATION_MS)
                .start()
        }
    }

    private fun bindResultSheet(result: ScannerResult) {
        if (result.requiresRegistration || result.product == null) {
            registrationMessageText.visibility = View.VISIBLE
            resultSafetyText.visibility = View.INVISIBLE
            resultProductNameText.visibility = View.INVISIBLE
            resultAnalysisText.visibility = View.INVISIBLE
            nutritionLayout.visibility = View.INVISIBLE
            resultPrimaryButton.text = "제품 등록하기"
            return
        }

        val product = result.product
        registrationMessageText.visibility = View.GONE
        resultSafetyText.visibility = View.VISIBLE
        resultProductNameText.visibility = View.VISIBLE
        resultAnalysisText.visibility = View.VISIBLE
        nutritionLayout.visibility = View.VISIBLE
        resultPrimaryButton.text = "MORE →"

        val isDangerous = result.analysis?.isDangerous == true
        resultSafetyText.text = if (isDangerous) "Caution" else "Safe"
        resultSafetyText.setTextColor(
            Color.parseColor(if (isDangerous) "#D96B5F" else "#16A635")
        )
        resultProductNameText.text = product.productName.orEmpty().ifBlank { "상품명 없음" }
        resultAnalysisText.text = formatAnalysisMessage(result)
        caloriesText.text = formatNutrition(
            value = product.calories,
            unit = "kcal",
            percent = result.nutrientPercents?.energyPercent
        )
        sugarText.text = formatNutrition(
            value = product.sugar,
            unit = "g",
            percent = result.nutrientPercents?.sugarPercent
        )
        fatText.text = formatNutrition(
            value = product.fat,
            unit = "g",
            percent = result.nutrientPercents?.fatPercent
        )
        sodiumText.text = formatNutrition(
            value = product.sodium,
            unit = "mg",
            percent = result.nutrientPercents?.sodiumPercent
        )
    }

    private fun hideResultSheet(showPreviousScans: Boolean) {
        resultSheet.animate()
            .translationY(resultSheet.height.toFloat())
            .setDuration(SHEET_ANIMATION_MS)
            .setListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: Animator) {
                    resultSheet.visibility = View.GONE
                    resultSheet.animate().setListener(null)
                    if (showPreviousScans && lastScanResult != null) {
                        previousScansButton.visibility = View.VISIBLE
                    }
                }
            })
            .start()

        resultScrim.animate()
            .alpha(0f)
            .setDuration(SHEET_ANIMATION_MS)
            .setListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: Animator) {
                    resultScrim.visibility = View.GONE
                    resultScrim.animate().setListener(null)
                }
            })
            .start()
    }

    private fun setupResultSheetDrag() {
        var downRawY = 0f
        var startTranslationY = 0f

        resultSheet.setOnTouchListener { _, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downRawY = event.rawY
                    startTranslationY = resultSheet.translationY
                    true
                }

                MotionEvent.ACTION_MOVE -> {
                    val dy = (event.rawY - downRawY).coerceAtLeast(0f)
                    resultSheet.translationY = startTranslationY + dy
                    true
                }

                MotionEvent.ACTION_UP,
                MotionEvent.ACTION_CANCEL -> {
                    if (resultSheet.translationY > resultSheet.height * SHEET_DISMISS_THRESHOLD) {
                        hideResultSheet(showPreviousScans = true)
                    } else {
                        resultSheet.animate()
                            .translationY(0f)
                            .setDuration(SHEET_ANIMATION_MS)
                            .start()
                    }
                    true
                }

                else -> false
            }
        }
    }

    private fun formatNutrition(value: Double?, unit: String, percent: Int? = null): String {
        if (value == null) return "-"
        val number = if (value % 1.0 == 0.0) {
            value.toInt().toString()
        } else {
            String.format("%.1f", value)
        }
        val amount = "$number$unit"
        return if (percent != null) {
            "$amount\n${percent}%"
        } else {
            amount
        }
    }

    private fun formatAnalysisMessage(result: ScannerResult): String {
        val ingredients = result.analysis?.dangerousIngredients.orEmpty()
        if (ingredients.isNotEmpty()) {
            return "주의 성분: ${ingredients.joinToString(", ")}"
        }
        return result.analysis?.message.orEmpty().ifBlank {
            result.product?.allergy?.let { allergy ->
                "알레르기 표시: $allergy"
            }.orEmpty().ifBlank {
                "알레르기 위험 성분이 확인되지 않았습니다."
            }
        }
    }

    private fun String.toScanBarcode(): String? {
        val digits = filter { it.isDigit() }
        return when (digits.length) {
            UPC_A_LENGTH -> "0$digits"
            EAN_13_LENGTH -> digits
            else -> null
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        scanTimeoutJob?.cancel()
        scanGuideView.stopScanAnimation()
        camera?.cameraControl?.enableTorch(false)
        cameraExecutor.shutdown()
    }

    private companion object {
        const val UPC_A_LENGTH = 12
        const val EAN_13_LENGTH = 13
        const val SCAN_TIMEOUT_MS = 10_000L
        const val SHEET_ANIMATION_MS = 260L
        const val SHEET_DISMISS_THRESHOLD = 0.18f
        const val DISABLED_BUTTON_ALPHA = 0.45f
        const val MAX_GALLERY_IMAGE_SIZE_PX = 2048
        val FLASH_ON_COLOR: Int = Color.parseColor("#FFDD4A")
        const val TAG = "BarcodeScanner"
    }
}
