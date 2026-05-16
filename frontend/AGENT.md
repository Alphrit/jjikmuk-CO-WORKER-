# AGENT.md

## Project Overview

JJIKMUK is an Android application in a single Gradle module, `:app`.

- Language: Kotlin
- UI: XML layouts with `AppCompatActivity`, `Fragment`, `RecyclerView`, and Material components
- Dependency injection: Hilt
- Networking: Retrofit, OkHttp, Gson converter
- Async/state: Kotlin coroutines, `StateFlow`, `SharedFlow`
- Scanner: CameraX preview/analysis with ML Kit barcode scanning
- Package root: `com.coworker.jjikmuk`

## Repository Layout

- `settings.gradle.kts`: includes only `:app`
- `build.gradle.kts`: top-level plugin aliases
- `gradle/libs.versions.toml`: version catalog for AGP, AndroidX, Hilt, KSP, tests
- `app/build.gradle.kts`: Android app configuration and app dependencies
- `app/src/main/AndroidManifest.xml`: permissions, application class, activities
- `app/src/main/java/com/coworker/jjikmuk/MainActivity.kt`: main host activity
- `app/src/main/java/com/coworker/jjikmuk/JjikmukApplication.kt`: Hilt application entry point
- `app/src/main/java/com/coworker/jjikmuk/di`: Hilt modules
- `app/src/main/java/com/coworker/jjikmuk/data`: remote DTO/API and repository implementation
- `app/src/main/java/com/coworker/jjikmuk/domain`: domain models and repository contracts
- `app/src/main/java/com/coworker/jjikmuk/feature`: feature screens such as home, chat, product, scanner
- `app/src/main/res/layout`: XML screens, items, dialogs, and bottom sheets
- `app/src/main/res/drawable`: icons and shape backgrounds

## Build And Test Commands

Run commands from the repository root.

```powershell
.\gradlew.bat assembleDebug
.\gradlew.bat testDebugUnitTest
.\gradlew.bat connectedDebugAndroidTest
```

Use `connectedDebugAndroidTest` only when an Android emulator or device is available.

The app reads these Gradle properties:

- `API_BASE_URL`: defaults to `http://10.0.2.2:8080/`
- `USE_MOCK_SCAN`: defaults to `true`

Example with the real backend:

```powershell
.\gradlew.bat assembleDebug -PAPI_BASE_URL=http://10.0.2.2:8080/ -PUSE_MOCK_SCAN=false
```

Do not commit machine-specific SDK paths from `local.properties`.

## Architecture Notes

The current dependency direction is:

`feature -> domain -> data -> remote`

- Feature code should depend on `domain.repository.ProductRepository`, not directly on Retrofit APIs.
- `ProductRepositoryImpl` maps nullable remote DTOs into nullable-safe domain models.
- Network dependencies are provided in `NetworkModule`.
- Repository bindings are provided in `RepositoryModule`.
- Use `ApiResult` for repository results instead of throwing through feature layers.

The scanner flow is:

1. `HomeFragment` starts `BarcodeScannerActivity`.
2. `BarcodeScannerActivity` owns camera permission, CameraX preview, ML Kit barcode detection, and result sheet UI.
3. `ScannerViewModel` receives the barcode and either returns a mock result or calls `ProductRepository.scanProduct`.
4. `ScannerUiState` drives sheet rendering with `resultSequence` to distinguish repeated results.

## Coding Conventions

- Keep Kotlin style official and match existing package structure.
- Prefer constructor injection with Hilt for new dependencies.
- Keep network DTOs in `data.remote.dto`, API interfaces in `data.remote.api`, repository implementations in `data.repository`, and app-facing contracts/models in `domain`.
- Keep Android UI work in feature packages and XML resources.
- Write code and UI with maintainability, refactoring, and future extension in mind instead of using temporary fixes that only work in one runtime environment or only account for the current code path.
- For new UI text, prefer string resources unless existing nearby code intentionally uses temporary inline text.
- Preserve existing resource naming patterns:
  - `activity_*`, `fragment_*`, `item_*`, `dialog_*`, `bottom_sheet_*`
  - `bg_*` for shape backgrounds
  - `ic_*` for vector icons
- Avoid unrelated formatting churn in XML and Gradle files.

## Scanner And Camera Guidelines

- Keep camera access behind runtime permission checks.
- Close every `ImageProxy` in CameraX analyzers.
- Avoid starting duplicate scan attempts while barcode lookup is in flight.
- Keep UI state in `ScannerViewModel`; keep camera lifecycle and animations in `BarcodeScannerActivity`.
- Preserve the mock scanner path because it lets the UI be tested without the backend.

## Networking Guidelines

- Retrofit base URLs must end with `/`.
- The emulator-to-host default is `10.0.2.2`.
- Keep timeout changes centralized in `NetworkModule`.
- Add API endpoints to `ProductApi` or sibling API interfaces, then expose app behavior through repositories.

## Testing Guidance

- Add unit tests for repository mapping, ViewModel state transitions, and error handling when changing domain/data behavior.
- Add instrumentation tests only for Android framework behavior that cannot be exercised on the JVM.
- Before handing off non-trivial changes, run at least:

```powershell
.\gradlew.bat testDebugUnitTest
```

Run `assembleDebug` when build configuration, resources, manifest entries, Hilt modules, or generated code are touched.

## Working Tree Safety

- The repository may contain user edits. Do not revert or overwrite changes unless explicitly asked.
- Check `git status --short` before broad edits.
- Keep changes scoped to the requested task.
