#!/usr/bin/env python3
"""Optional offline review probe. Uses existing Maven/Gradle cache; installs nothing."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PACKAGE = "io.github.tinmachine.eidolon."
MAIN = ROOT / "android/app/src/main/java/io/github/tinmachine/eidolon"
TEST = ROOT / "android/app/src/test/java/io/github/tinmachine/eidolon"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["sdk", "models", "ime", "save-ime"])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path.home() / ".gradle/caches/modules-2/files-2.1")
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if out.is_relative_to(ROOT):
        parser.error("output-dir must be outside this repository")
    os.umask(0o077)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    version = re.search(r'id\("org.jetbrains.kotlin.android"\) version "([^"]+)"',
                        (ROOT / "android/build.gradle.kts").read_text()).group(1)

    def jar(group, name, revision):
        matches = list((args.cache / group / name / revision).glob(f"*/{name}-{revision}.jar"))
        if len(matches) != 1:
            parser.error(f"cached dependency missing/ambiguous: {group}:{name}:{revision}; no download attempted")
        return str(matches[0])

    stdlib = jar("org.jetbrains.kotlin", "kotlin-stdlib", version)
    annotations = jar("org.jetbrains", "annotations", "23.0.0")
    compiler = [jar("org.jetbrains.kotlin", "kotlin-compiler-embeddable", version), stdlib,
                jar("org.jetbrains.kotlin", "kotlin-script-runtime", version),
                jar("org.jetbrains.kotlin", "kotlin-reflect", "1.6.10"),
                jar("org.jetbrains.kotlinx", "kotlinx-coroutines-core-jvm", "1.10.1"), annotations]
    cp = [stdlib, annotations]
    if args.mode == "sdk":
        sources = [MAIN / "SdkResourceScope.kt", MAIN / "SdkEvidenceBridge.kt",
                   *(HERE / name for name in ("AndroidDoubles.kt", "CoreDoubles.kt", "RuntimeDoubles.kt", "SdkBoundaryProbe.kt"))]
        entry = [PACKAGE + "SdkBoundaryProbeKt"]
    else:
        names = ["RuntimeSaveCoordinator", "RuntimeAdmission", "RuntimeOriginStop", "SdkRetirementHub", "SdkEvidenceBridge",
                 "DebugSdkProductExecutor", "DebugSdkRuntimeLease",
                 "LinuxStorageRepository", "LinuxStorageFiles", "LinuxStorageIdentity", "LinuxStorageIdentityCodec",
                 "LinuxDesktopSettingsRepository", "LinuxDesktopV3ConfigRepository", "LinuxDesktopV3Config",
                 "LinuxDesktopPins", "ConnectionProfile", "ConnectionProfileV2", "ConnectionProfileV2Codec", "LinuxConnectionProfile"]
        tests = ["RuntimeSaveCoordinatorTest", "RuntimeSettingsIntegrationTest", "SdkEvidenceBridgeTest",
                 "RuntimeAdmissionTest", "RuntimeOriginStopTest"]
        if args.mode == "ime":
            names = ["ImeEditorState", "ImeTransactionBridge", "ImeSessionConnection", "ImeRawFocusReturn"]
            tests = ["ImeSessionConnectionTest", "ImeRawFocusReturnTest"]
        helpers = ["HostLinuxStorageFixture"] if args.mode == "models" else []
        sources = [MAIN / (name + ".kt") for name in names] + [TEST / (name + ".kt") for name in [*tests, *helpers]]
        if args.mode == "save-ime":
            sources = [MAIN / (name + ".kt") for name in [*names, "ImeEditorState", "ImeTransactionBridge", "ImeSessionConnection"]]
            sources += [HERE / "SaveImeBoundaryTest.kt"]
            tests = ["SaveImeBoundaryTest"]
        cp += [jar("org.json", "json", "20240303"), jar("junit", "junit", "4.13.2"),
               jar("org.hamcrest", "hamcrest-core", "1.3")]
        entry = ["org.junit.runner.JUnitCore", *(PACKAGE + test for test in tests)]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    if args.mode == "models":
        resource = ROOT / "android/app/src/test/resources/linux-storage-posix-test.c"
        hashes[str(resource.relative_to(ROOT))] = hashlib.sha256(resource.read_bytes()).hexdigest()
    jar_hashes = {Path(p).name: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in set(compiler + cp)}
    (out / f"{args.mode}-inputs.json").write_text(json.dumps({"mode": args.mode, "kotlin": version,
        "source_sha256": hashes, "jar_sha256": jar_hashes,
        "limits": "JVM models / SDK doubles; storage test JNI uses host cc. No NativeSessionHost, product JNI, real SDK or device"}, indent=2) + "\n")
    dest = out / (args.mode + ".jar")
    compile_command = ["java", "-cp", os.pathsep.join(compiler), "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
                       "-no-stdlib", "-no-reflect", "-jvm-target", "17", "-classpath", os.pathsep.join(cp),
                       "-d", str(dest), *map(str, sources)]
    with (out / f"{args.mode}-compile.log").open("w") as log:
        result = subprocess.run(compile_command, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        print(f"compile failed; inspect {out / (args.mode + '-compile.log')}")
        return result.returncode
    command = ["java", "-cp", os.pathsep.join([str(dest), *cp, str(ROOT / "scripts/tests/fixtures"),
                                               str(ROOT / "android/app/src/test/resources")]), *entry]
    with (out / f"{args.mode}-result.log").open("w") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    print((out / f"{args.mode}-result.log").read_text(), end="")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
