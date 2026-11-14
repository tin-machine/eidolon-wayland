# Linux Desktop v3 I5 — 実装・検証と引き継ぎ

## 対象と証拠の範囲

開始時と push 前に GitHub の main を再取得し、
`eca71addc58009b4576644da162d56670eebfaf5`、PR #16 merged を確認した。
I5 の変更は `feat/linux-desktop-v3-i5-storage-launcher` にまとめた。
初回ローカル検証の製品コード・テスト対象は `93a216f6979f1c5920b5bd917a463f011c41e5b3` までの source である。
HTTPS push の認証情報がないため、接続済み GitHub API で責務別 commit を公開し、各 source tree がローカル検証済み tree と同一であることを照合した。
後続の文書 commit を含む **PR 最終 HEAD の Actions run / job / conclusion は PR 本文へ記録する**。
旧 #16 の CI は今回の変更の検証へ流用しない。

詳細な差分、型、所有者、保存順序、失敗後の遷移は
[I5 詳細設計](linux-real-app-v3-i5-detailed-design.md) にある。
I5 の実装・JVM fixture・Host CI と、I6/T20/V11・I7/Quest/V12・H の受け入れは別の証拠である。
この文書や PR の作成は、設計承認、製品受け入れ、Quest 成功を意味しない。

## 実装の成立条件

- `RuntimeAdmission` の epoch は保存 actor だけが遷移させる。通常操作は enqueue 時と実行時の両方で照合する。
- 旧 Controller を維持して origin を回収し、必要な live Recover と同 capture の I2 finalizer を先に終える。
- 次に診断を含む全 Runtime を停止する。生存 snapshot の全 counter、実 nativeDestroy Accepted、
  全世代・旧 Scene・callback を含む SDK ticket を `StopReceipt` に結合する。
- `ReclamationBarrier.withReclaimed` に入る時点で回収を完了し、callback 内は file 保存と readback だけを行う。
- intent → E1 Matched → v3 configuration → active marker → 全体 readback の順に進める。
  単一 file の rename を複数 file の atomic transaction と扱わない。
- readback 失敗も未完了として保持し、同 process の元 receipt / intent / epoch による明示 resume だけを許す。
- cold restart で旧 local / SDK authority が失われた保存途中状態は起動を閉じる。
  durable intent は再開する bytes を表し、回収証明そのものにはしない。使えない Recover を表示上だけ有効にしない。
- v3 catalog は二項目のまま、単一 GUI lease を維持する。Start の queue Accepted を I4 ready に読み替えない。

## ローカル検証

### Android / Kotlin

| 検査 | 結果 | 範囲 |
| --- | --- | --- |
| main + release Kotlin / Compose compile | PASS、93 source files | Android API 36、Meta SDK 0.15.0、既存 Compose BOM 相当の実 jar、Kotlin 2.4.21。R / BuildConfig だけは生成物相当の定数 |
| main + debug Kotlin / Compose compile | PASS、167 source files | 通常 Host と debug BuildHooks / probe 全 source。SDK 型や Compose を mock に差し替えない |
| JUnit `test` + `testDebug` 全 source | **474 PASS、68 suites、skip 0** | 69 source files を compile。既存 test resources と B1 shared fixtures を使用 |
| Gradle / lint / APK / NDK / ELF・同梱物検査 | ローカル未実施 | Gradle distribution 取得が Java `SocketException: Network is unreachable` で失敗。上記 compile / JUnit と区別し、PR CI で実行 |

直接コンパイルで新しい fixture の `Files.readString/writeString` が Android boot classpath にないことを検出し、
既存 Android API で利用できる `readAllBytes/write` に変更した。期待する bytes や検査条件は変えていない。
JUnit の直接実行では Gradle と同じ `test` / `testDebug` と resource roots を揃えてから上記全件を実行した。

実ファイル保存テストは既存の C/JNI POSIX fixture による lstat/no-follow/CAS/fsync/rename/readback を使う。
`RuntimeSettingsIntegrationTest` はこの実 I/O と実 SDK ledger / save actor を結合し、native Port のみ明示的な fixture を使う。
これらは Android instrumentation、Meta SDK 実機 disposal、Quest の描画・入力を実行した証拠ではない。

### 公開後レビューの SDK 取消補修

初回公開 HEAD `a5b09921868c2b2d54f26f70189b4b8b40147e12` の CI 待機中に、既存
`WindowControls.cancelSources()` の `onNoIntersectionInput` / `resolveInput` が例外を捨てる経路を確認した。
両方を既存の failure count と同 SDK scope の sticky failure へ伝搬するよう補修した。
これにより `closeControls()` が disposal failure を保持し、auxiliaryDisposed / readyToRelease を成功にできない。
単一 file を実 Android / Meta SDK jar と release classes に対して再 compile し PASS。
既存 ledger / save actor の失敗テストに加え、最終 HEAD の CI を再実行する。
SDK 取消例外そのものの Quest 上の注入・観測は未実施であり、初回公開 HEAD の CI を最終 HEAD の代用にはしない。

### Host / native

| 検査 | 結果 |
| --- | --- |
| `cargo check --locked --workspace --all-targets` | PASS |
| `cargo clippy --locked --workspace --all-targets -- -D warnings` | PASS |
| `cargo fmt --all -- --check` | PASS |
| model | 136 PASS |
| core full attempt | 148 PASS / 7 FAIL / 1 ignored。named Unix socket 作成の EPERM 5 件と共有 fixture lock poison の波及 2 件 |
| core の実行可能範囲 | 上記直接 EPERM 5 件のみをローカル CLI で除外し、150 PASS / 1 ignored。波及した 2 件も PASS |
| transport full attempt | 144 PASS / 13 FAIL。13 件とも named Unix socket fixture の EPERM。最終 v2 追加テスト前の試行 |
| transport 最終 source の実行可能範囲 | 同 13 件のみをローカル CLI で除外して 146 PASS。全 146 件の serial 再実行で確認 |
| transport v3 subset | 45 PASS。全件の上記件数と重複する |
| Rust Android registry / JNI boundary | 5 PASS |
| frozen v3 Python contract | 16 PASS |

`scripts/check-host --suite all` は実行したが、この作業環境の named AF_UNIX socket 制約で途中失敗した。
既存 suite、CI、required check、golden に除外設定は追加していない。完全な Host、Waypipe clipboard/IME 検査は最終 HEAD CI に委ねる。
transport の並列 filtered 試行では既存 journal test 1 件に一過性 `DESKTOP_V3_JOURNAL_BUSY` も発生した。
単独再実行と全 146 件の serial 再実行は PASS。CI の実行方式は変更していない。

`scripts/linux_real_app_check.py` の `FROZEN_V2` 全 5 ファイルを基準 main と bytes 比較し一致した。
v2 Rust journal への変更は `Pending.matches_binding()` の読み取り専用照合であり、record schema、serialize、publish、remote protocol は変更していない。

### 文書と検査入口

`python3 scripts/docs.py validate`、`python3 scripts/check-design`、CI scope の既存 25 tests、`git diff --check` を実行して PASS。
check-design は 31 tasks / 45 checks の依存・参照整合性であり、実装・実機の PASS 判定ではない。
I2/I4 context は別々に取得した。I5 は manual discovery のままで、存在しない resolver task を追加済みとは扱わない。

追加 JUnit は通常の `app/src/test/java` の `@Test` であり、既存
`.github/workflows/check.yml` → `scripts/build-android` → `:app:testDebugUnitTest` が収集する。
native / Android 差分で既存 scope classifier が必須 job を選ぶ。CI gate の条件は変更していない。

## V09 / V10 の検査対応

| 条件 | 主な実行 suite |
| --- | --- |
| 初回登録、同 host/user 編集、交代、各 publish / readback 境界、明示 resume | `LinuxDesktopSettingsRepositoryTest`、既存 `LinuxStorageRepositoryTest` / `LinuxStorageFilesTest` |
| marker 欠落・破損・混在、v3→v2、暗黙 fallback 拒否 | `LinuxDesktopSettingsRepositoryTest` |
| 各 counter 残存、remote/local pending、Destroy Busy/STALE/Failed、SDK failure | `RuntimeSaveCoordinatorTest`、`RuntimeSettingsIntegrationTest`、native reclamation / registry tests |
| 元 capture Recover、timeout 保持、旧 Scene / diagnostic / callback drain | `RuntimeSettingsIntegrationTest`、`RuntimeSaveCoordinatorTest`、`SdkEvidenceBridgeTest`、native v2/v3 recovery tests |
| stale capture、CAS、並行 profile、Runtime 再生成、古い epoch | `RuntimeAdmissionTest`、`RuntimeSettingsIntegrationTest`、既存 `DebugSdkProductExecutorTest` |
| Connect Accepted 後の session 未割当、取消実完了、古い ACK / recovery 接続 | `RuntimeOriginStopTest` |
| 未知・重複・改変 catalog、旧 revision、二重押し、一 lease | `LinuxDesktopV3CatalogTest`、native v3 catalog / selection tests |
| I4 ready と command Accepted の区別、固定 reason、日本語表示 | `LinuxDesktopV3StatusTest` |
| import / canonical / 最終 Runtime / JNI bytes、混在・storage 単独拒否 | `LinuxRuntimeConfigurationTest`、`LinuxDesktopV3ConfigTest`、native model / JNI tests |
| v2 / Termux / diagnostic / 入力互換性 | 既存 JUnit 全 suite と native suite。実 Quest 上の回帰は I7/H で別実施 |

## I6 / I7 / H への引き継ぎ

| 段階 | 未実施の受け入れと利用できる入口 |
| --- | --- |
| I6 / T20 / V11 | 実 SSH + C2 + Waypipe + Mousepad の通常 v3 全経路。通常設定保存 → Connect → Inspect → 固定 Mousepad 選択、I4 report、Stop / live Recover を使用 |
| I7 / V12 | Quest への実 APK 配備、SDK delete/listener/後続 pass、合成入力、実保存・回収の operator 自動受け入れ。`runtime.reclamation`、transport / B1 / I4 report と Host Stop receipt を観測 |
| H | 装着、MR 視認、物理キーボード、コントローラー、複数独立 window の人間による確認 |

旧 Scene が終了し delete 通知と後続 pass を取得できなかった lease、SDK dispose failure、nativeDestroy 拒否、
cold restart で旧 local authority を失った保存途中状態は、この PR では推測で完了へ変えない。
これらに新しい運用上の再開を設ける場合は、明示的な所有権移送とその証拠を別途設計する。
I5 の CI 成功によって T20 / Quest / H を PASS に変更しない。

この I5 PR はユーザーの追加指示までマージしない。
