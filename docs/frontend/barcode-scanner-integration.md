# Barcode Scanner Frontend Integration

## Purpose

이 문서는 프론트엔드 팀이 스캔 버튼에 바코드 스캔 기능을 연결하는 방법을 정리한다.

스캐너 기능의 최종 output은 제품 정보가 아니라 13자리 바코드 문자열이다.

```json
{
  "barcode": "8801234567890"
}
```

## Added Frontend Files

- `frontend/app/src/main/java/com/coworker/frontend/barcode/BarcodeScannerActivity.kt`
- `frontend/app/src/main/java/com/coworker/frontend/barcode/BarcodeScanContract.kt`
- `frontend/app/src/main/res/layout/activity_barcode_scanner.xml`

## Added Frontend Dependencies

`frontend/app/build.gradle.kts`에 CameraX와 ML Kit Barcode Scanning 의존성을 추가했다.

```kotlin
val cameraxVersion = "1.3.0"
implementation("androidx.camera:camera-core:$cameraxVersion")
implementation("androidx.camera:camera-camera2:$cameraxVersion")
implementation("androidx.camera:camera-lifecycle:$cameraxVersion")
implementation("androidx.camera:camera-view:$cameraxVersion")
implementation("com.google.mlkit:barcode-scanning:17.2.0")
```

## Added Android Manifest Entries

`frontend/app/src/main/AndroidManifest.xml`에 카메라 권한과 스캐너 Activity를 추가했다.

```xml
<uses-permission android:name="android.permission.CAMERA" />
```

```xml
<activity
    android:name=".barcode.BarcodeScannerActivity"
    android:exported="false" />
```

## Button Integration

프론트 팀은 스캔 버튼 클릭 시 `BarcodeScanContract`를 실행하면 된다.

Example:

```kotlin
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.coworker.frontend.barcode.BarcodeScanContract

class MainActivity : AppCompatActivity() {
    private val barcodeScannerLauncher = registerForActivityResult(BarcodeScanContract()) { barcode ->
        if (barcode == null) {
            // User canceled or scan failed.
            return@registerForActivityResult
        }

        // Use this value as the scanner output.
        // Example: "8801234567890"
        submitBarcode(barcode)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        scanButton.setOnClickListener {
            barcodeScannerLauncher.launch(Unit)
        }
    }

    private fun submitBarcode(barcode: String) {
        // 1. Optionally call scanner validation API.
        // 2. Then call DB/product lookup API with the same barcode value.
    }
}
```

## Recommended Frontend Flow

```text
1. User taps scan button.
2. Frontend launches BarcodeScannerActivity.
3. BarcodeScannerActivity opens the camera and detects EAN-13 with ML Kit.
4. BarcodeScannerActivity returns a 13-digit barcode string.
5. Frontend sends the barcode to backend.
6. Frontend sends or forwards the same barcode to DB/product lookup API.
```

## Backend Validation API

Scanner validation endpoint:

```http
POST /api/v1/barcodes/scan
Content-Type: application/json
```

Request:

```json
{
  "barcode": "8801234567890"
}
```

Success response:

```json
{
  "status": "SCANNED",
  "barcode": "8801234567890",
  "barcodeType": "EAN_13",
  "digitCount": 13,
  "message": "바코드 스캔이 완료되었습니다."
}
```

## Responsibility Boundary

Frontend:

- Shows the scan button.
- Opens the camera screen.
- Handles camera permission.
- Runs ML Kit barcode recognition.
- Receives the final 13-digit barcode string.

Backend scanner API:

- Receives the barcode string.
- Normalizes digits.
- Validates EAN-13 length.
- Returns `SCANNED` or `INVALID_BARCODE`.

DB/product API:

- Receives `barcode`.
- Looks up product information.
- Returns product details.
