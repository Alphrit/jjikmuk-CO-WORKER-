plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.kapt)
    alias(libs.plugins.hilt)
}

val apiBaseUrl = providers.gradleProperty("API_BASE_URL")
    .orElse("http://10.0.2.2:8080/")
    .get()
val useMockScan = providers.gradleProperty("USE_MOCK_SCAN")
    .orElse("true")
    .get()
val scanUserId = providers.gradleProperty("SCAN_USER_ID")
    .orElse("")
    .get()
val aiBaseUrl = providers.gradleProperty("AI_BASE_URL")
    .orElse("http://10.0.2.2:8000/")
    .get()
val geminiApiKey = providers.gradleProperty("GEMINI_API_KEY")
    .orElse(providers.environmentVariable("GEMINI_API_KEY"))
    .orElse("")
    .get()
val geminiModel = providers.gradleProperty("GEMINI_MODEL")
    .orElse("gemini-2.5-flash-lite")
    .get()

android {
    namespace = "com.coworker.jjikmuk"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.coworker.jjikmuk"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        buildConfigField("String", "API_BASE_URL", "\"$apiBaseUrl\"")
        buildConfigField("String", "AI_BASE_URL", "\"$aiBaseUrl\"")
        buildConfigField("String", "GEMINI_API_KEY", "\"$geminiApiKey\"")
        buildConfigField("String", "GEMINI_MODEL", "\"$geminiModel\"")
        buildConfigField("Boolean", "USE_MOCK_SCAN", useMockScan)
        buildConfigField("String", "SCAN_USER_ID", "\"$scanUserId\"")
    }

    buildFeatures {
        buildConfig = true
        viewBinding = true
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }

        debug {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
}

hilt {
    enableAggregatingTask = true
}

kapt {
    correctErrorTypes = true
}

kotlin {
    jvmToolchain(11)
}

dependencies {
    val cameraxVersion = "1.3.0"
    implementation("androidx.camera:camera-core:$cameraxVersion")
    implementation("androidx.camera:camera-camera2:$cameraxVersion")
    implementation("androidx.camera:camera-lifecycle:$cameraxVersion")
    implementation("androidx.camera:camera-view:$cameraxVersion")
    implementation("com.google.mlkit:barcode-scanning:17.2.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-gson:2.11.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("org.opencv:opencv:4.10.0")

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.fragment.ktx)
    implementation(libs.androidx.constraintlayout)
    implementation(libs.androidx.recyclerview)
    implementation(libs.material)

    implementation(libs.androidx.lifecycle.viewmodel.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)

    implementation(libs.androidx.datastore.preferences)

    implementation(libs.hilt.android)
    kapt(libs.hilt.compiler)

    testImplementation(libs.junit)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
}
