// Fairwind - Android client (skeleton).
//
// spec 88-90, 132; docs/PLATFORM_MATRIX.md "Android".
//
// Status: BUILT. A debug APK is produced by this project on the toolchain BUILD.md
// records (JDK 17 + Android SDK 35 + Gradle 8.10.2). See BUILD.md for the build recipe
// and for what is still unverified (no device run, no release signing, no core).
pluginManagement {
    // Versions live in gradle/libs.versions.toml and were verified by an actual build
    // (see BUILD.md). Re-verify against the official release notes before bumping any.
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "fairwind-android"
include(":app")
