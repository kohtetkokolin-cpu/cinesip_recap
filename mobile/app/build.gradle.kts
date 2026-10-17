plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}
android {
    namespace = "com.cinesip.recap"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.cinesip.recap"
        // Universal: Android 8.0+ covers 95%+ of phones
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
        // Universal ABIs: arm64 (modern) + arm32 (older phones)
        ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a") }
    }
    buildTypes {
        release { isMinifyEnabled = false }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    packaging {
        jniLibs { pickFirsts += listOf("**/libc++_shared.so") }
    }
}
repositories {
    google()
    mavenCentral()
}
dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // === Video rendering (on-device, Zippy-style) ===
    // Original FFmpegKit retired Apr 2025 → antonkarpenko fork (Maven Central)
    implementation("com.antonkarpenko:ffmpeg-kit-min:2.2.3")

    // === On-device AI (Phase 2 — added incrementally after base APK builds) ===
    // Transcription: ONNX Runtime + Whisper (temporarily disabled for build stability)
    // implementation("com.microsoft.onnxruntime:onnxruntime-android:1.18.0")
    // Face detection: OpenCV (temporarily disabled)
    // implementation("org.opencv:opencv:4.10.0")
    // Audio events: TFLite (temporarily disabled)
    // implementation("org.tensorflow:tensorflow-lite:2.14.0")

    // === Cloud AI (plain HTTP — works from phone unchanged) ===
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    implementation("org.json:json:20240303")
}
