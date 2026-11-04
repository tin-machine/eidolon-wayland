import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "io.github.tinmachine.eidolon"
    compileSdk = 36
    buildToolsVersion = "35.0.0"
    defaultConfig {
        applicationId = "io.github.tinmachine.eidolon"
        minSdk = 34
        targetSdk = 34 // Quest実機はAndroid 14。Play Store向けアプリではない。
        versionCode = providers.gradleProperty("eidolonVersionCode").orElse("1").get().toInt()
        versionName = "0.1.$versionCode"
        ndk { abiFilters += "arm64-v8a" }
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    lint {
        // Horizon OSのtarget API 34を維持。その他のlintエラーは失敗させる。
        disable += "ExpiredTargetSdkVersion"
    }
    packaging { resources.excludes += "META-INF/LICENSE" }
}
kotlin { compilerOptions { jvmTarget.set(JvmTarget.JVM_17) } }

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation(platform("androidx.compose:compose-bom:2024.09.03"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("com.meta.spatial:meta-spatial-sdk:0.15.0")
    implementation("com.meta.spatial:meta-spatial-sdk-toolkit:0.15.0")
    implementation("com.meta.spatial:meta-spatial-sdk-vr:0.15.0")
    implementation("com.meta.spatial:meta-spatial-sdk-compose:0.15.0")
}

// Phase 0はcodeでpanelを生成し、独自componentも定義しない。
// Spatial Editor / Meta Gradle pluginのcode generationには依存しない。
