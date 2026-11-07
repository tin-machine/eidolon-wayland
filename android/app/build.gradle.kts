import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import groovy.json.JsonOutput

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

val eidolonVersion = providers.gradleProperty("eidolonVersionCode").orElse("1")
val nativeTasks = listOf("debug", "release").associateWith { profile ->
    val title = profile.replaceFirstChar { it.uppercaseChar() }
    val nativeOutput = layout.buildDirectory.dir("generated/jniLibs/$profile")
    val assetOutput = objects.directoryProperty().convention(layout.buildDirectory.dir("generated/nativeAssets/$profile"))
    val buildInfo = providers.exec {
        commandLine("python3", file("../../scripts/native-build-inputs"), "--version", eidolonVersion.get(), "--profile", profile)
    }.standardOutput.asText.map { it.trim() }
    val task = tasks.register<Exec>("build${title}EidolonNative") {
        inputs.property("buildInfo", buildInfo)
        inputs.property("ndk", "27.3.13750724")
        inputs.property("api", 34)
        inputs.property("abi", "arm64-v8a")
        outputs.dir(nativeOutput)
        outputs.dir(assetOutput)
        doFirst {
            commandLine("python3", file("../../scripts/build-native"), "--version", eidolonVersion.get(), "--profile", profile,
                "--output", nativeOutput.get().asFile, "--assets", assetOutput.get().asFile)
        }
    }
    Triple(task, assetOutput, buildInfo)
}

android {
    namespace = "io.github.tinmachine.eidolon"
    compileSdk = 36
    buildToolsVersion = "35.0.0"
    defaultConfig {
        applicationId = "io.github.tinmachine.eidolon"
        minSdk = 34
        targetSdk = 34 // Quest実機はAndroid 14。Play Store向けアプリではない。
        versionCode = eidolonVersion.get().toInt()
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
    packaging { jniLibs.useLegacyPackaging = true }
    // APK検査で照合するhashを保持する。debug/releaseで同じcopyを使用しない。
    packaging { jniLibs.keepDebugSymbols += setOf("**/libeidolon_native.so", "**/libeidolon_probe_bin.so", "**/libwaypipe_bin.so") }
    listOf("debug", "release").forEach { profile ->
        sourceSets[profile].jniLibs.srcDir(layout.buildDirectory.dir("generated/jniLibs/$profile"))
        buildTypes.getByName(profile).buildConfigField("String", "NATIVE_BUILD_INFO_JSON", JsonOutput.toJson(nativeTasks.getValue(profile).third.get()))
    }
}
androidComponents {
    onVariants { variant ->
        val (task, assets) = nativeTasks.getValue(variant.buildType!!)
        variant.sources.assets?.addGeneratedSourceDirectory(task) { assets }
    }
}
tasks.configureEach {
    listOf("debug", "release").forEach { profile ->
        val title = profile.replaceFirstChar { it.uppercaseChar() }
        if (name == "merge${title}JniLibFolders") dependsOn(nativeTasks.getValue(profile).first)
    }
}
kotlin { compilerOptions { jvmTarget.set(JvmTarget.JVM_17) } }

dependencies {
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
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
