import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}
val probeVersion = providers.gradleProperty("eidolonProbeVersion").orElse("1")
val generatedNative = layout.buildDirectory.dir("generated/jniLibs")
val generatedAssets = objects.directoryProperty().convention(layout.buildDirectory.dir("generated/probeAssets"))
val nativeProbe by tasks.registering(Exec::class) {
    inputs.files(fileTree("../../probes/native") { exclude("target/**") })
    inputs.file("../../scripts/build-native-probe")
    inputs.file("../../scripts/build-probe-xkbcommon")
    inputs.property("commit", providers.exec { commandLine("git", "rev-parse", "HEAD") }.standardOutput.asText.map { it.trim() })
    inputs.property("version", probeVersion)
    outputs.dir(generatedNative)
    outputs.file(layout.buildDirectory.file("generated/native-probe.json"))
    outputs.dir(generatedAssets)
    doFirst {
        commandLine("python3", file("../../scripts/build-native-probe"), "--output", generatedNative.get().asFile,
            "--version", probeVersion.get(), "--assets", generatedAssets.get().asFile)
    }
}
android {
    namespace = "io.github.tinmachine.eidolon.probe"
    compileSdk = 36
    buildToolsVersion = "35.0.0"
    defaultConfig {
        applicationId = "io.github.tinmachine.eidolon.probe"
        minSdk = 34
        targetSdk = 34
        versionCode = probeVersion.get().toInt()
        versionName = "probe.$versionCode"
        ndk { abiFilters += "arm64-v8a" }
    }
    buildFeatures { compose = true; buildConfig = true }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    sourceSets["main"].jniLibs.srcDir(generatedNative)
    packaging { jniLibs.useLegacyPackaging = true; resources.excludes += "META-INF/LICENSE" }
    lint { disable += "ExpiredTargetSdkVersion" }
}
androidComponents {
    beforeVariants(selector().withBuildType("release")) { it.enable = false }
    onVariants(selector().withBuildType("debug")) { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(nativeProbe) { generatedAssets }
    }
}
tasks.configureEach { if (name == "mergeDebugJniLibFolders" || name == "mergeDebugAssets") dependsOn(nativeProbe) }
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
