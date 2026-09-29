// Root Gradle build for the Smart Accelerator Android client (skeleton).
//
// spec 88-90, 132. Nothing here is verified: see ../../README.md.
// The `clean` task is the conventional one; no custom plugin is applied at the root
// so a single `:app` module stays readable.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
}

tasks.register<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}
