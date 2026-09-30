// Application module: Fairwind Android client (skeleton).
//
// spec 88-90, 132; docs/PLATFORM_MATRIX.md "Android".
//
// BUILT AND VERIFIED on 2026-09-29: this file evaluates under Gradle 8.10.2 with AGP 8.6.1
// and JDK 17, and `assembleDebug` produces a signed debug APK (see BUILD.md for the exact
// toolchain, the three-pass recipe and the artefact hash). What is still unverified: no
// device run, no release signing, no proxy core - BUILD.md lists each one.

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "club.noclub.accelerator"
    compileSdk = libs.versions.compileSdk.get().toInt()

    defaultConfig {
        applicationId = "club.noclub.accelerator"
        // minSdk 26 (Android 8.0) is a deliberate product decision, not a default:
        //  * the tunnel MUST run in a foreground service with a notification, and
        //    notification channels only exist from API 26
        //  * VpnService.Builder's per-app APIs (21+), addDnsServer (21+) and
        //    setBlocking (21+) are all well below it, so we are not trading features
        //    for reach - we are trading a small amount of reach for a modern,
        //    testable service/battery surface (docs/PLATFORM_MATRIX.md).
        // Raising it later (e.g. 29 for setMetered) is a product decision with a
        // documented device-coverage cost. Nothing about this is verified on device.
        minSdk = 26
        targetSdk = libs.versions.targetSdk.get().toInt()

        versionCode = 1
        // Not a release version: this client has no released baseline (Gate B). The value
        // only has to change when a published build must be distinguished, and "-source"
        // marks the artefact as built from sources with no device verification behind it.
        versionName = "0.3.0-android-source"

        // ABI list for the bundled proxy core. The core is NOT chosen yet
        // (docs/CORE_APPROVAL.md), so this is a plan: one core binary per ABI shipped
        // as a native library (jniLibs/<abi>/lib<core>.so) so the OS extracts it into
        // nativeLibraryDir, the only location Android allows exec() from on API 29+
        // (W^X / app-data exec is blocked). See ARCHITECTURE.md.
        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            // NO signingConfig on purpose. Signing is BLOCKED: no keystore exists
            // (docs/PLATFORM_MATRIX.md "Signing keystore"). A release APK cannot
            // be produced until that changes; nothing here pretends otherwise.
        }
        debug {
            isMinifyEnabled = false
            applicationIdSuffix = ".debug"
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlin {
        jvmToolchain(17)
    }

    packaging {
        jniLibs {
            // Keep .so files uncompressed/alignable per ABI; the core binary is
            // executed from nativeLibraryDir, never extracted to app data.
            useLegacyPackaging = false
        }
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    buildTypes.getByName("release") {
        // Reproducible-build friendliness; not a claim that the build is bit-for-bit
        // reproducible (the debug build above is not).
        isDebuggable = false
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.okhttp)

    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    implementation(libs.compose.material.icons.core)

    // JVM unit tests (`testDebugUnitTest`) — host-side only, no device and no emulator.
    // The logic under test is deliberately Android-free (config dialect, process supervisor,
    // exit verifier, capability ledger), so these tests run on any CI runner, including the
    // Linux jobs that never see an Android device.
    testImplementation(libs.junit)
    testImplementation(libs.kotlin.test)
}

tasks.withType<Test>().configureEach {
    // A unit test that only passes on the developer's JVM is not a test: keep the output
    // deterministic and make failures show their assertion.
    testLogging {
        events("failed", "skipped")
        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
    }
}
