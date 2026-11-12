# Third-party components

Eidolon Waylandの独自コードのうちライセンスを付与できる部分は、ルートの[MIT License](LICENSE)で公開しています。このMITは、以下の第三者コンポーネントを再ライセンスするものではありません。コピー・改変・リンク・バンドルした素材には、それぞれの上流ライセンスと権利表示が引き続き適用されます。

## L7a host test fixture

`scripts/tests/fixtures/mousepad-0.6.3.gschema.xml` は Mousepad 0.6.3 の
[固定source](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/org.xfce.mousepad.gschema.xml)
を変更せずに検査用として保存する。Mousepad は GPL-2.0-or-later で配布され、
[COPYING](scripts/tests/fixtures/licenses/mousepad-0.6.3-COPYING) を併記する。
専用CIでは同じcommitから未改変Mousepadを一時的な検査packageとしてbuildする。
Mousepad本体やこのschemaをAndroid APKへ同梱する変更ではない。

## 固定native依存

版・公式archive SHA256・build条件は[`native/deps.lock.json`](native/deps.lock.json)に記録する。
以下の著作権表示・license本文はrepositoryへ保存し、APKの`assets/licenses/`にも同梱する。

| component | 使用箇所 | license本文 |
| --- | --- | --- |
| Smithay 0.7.0 | Wayland protocol/SHMとGlesRenderer。[SHM計上・Android EGL loader・compiled keymap初期化・NUL/Android keymap FDの固定patch](native/patches/smithay/README.md)を適用 | [MIT](native/licenses/smithay-0.7.0-LICENSE) |
| libxkbcommon 1.8.1 | static libraryと自己完結keymapのparse | [上流のlicense集](native/licenses/xkbcommon-1.8.1-LICENSE) |
| xkeyboard-config 2.48 | US/JIS keymap生成元data | [COPYING](native/licenses/xkeyboard-config-2.48-COPYING) |
| Waypipe 0.11.3 C版 | APK同梱のPIE helper。[Android用librt patch](native/patches/waypipe/0001-android-libc-realtime.patch)を適用 | [MIT](native/licenses/waypipe-0.11.3-LICENSE.MIT) |
| LZ4 1.10.0 library | Waypipeへstatic link。CLIはbuildしない | [BSD-2-Clause](native/licenses/lz4-1.10.0-LICENSE) |
| Zstandard 1.5.7 library | Waypipeへstatic link。CLIはbuildしない | [BSD-3-Clauseを選択](native/licenses/zstd-1.5.7-LICENSE) |

Waypipeは同じarchive内のC版（MIT）だけを使用し、Rust版はbuildしない。
security-contextを無効にしているためlibwayland/scannerはlinkしない。
Android API34のlibcを使い、Termuxのprefix/ELFやlibandroid-spawnをAPKへcopyしない。

debug専用のSHM clientはwayland-client 0.31.14（MIT）を使う。
その他のRust依存は`Cargo.lock`へ固定する。全依存を対象とする配布notice監査はPhase6の受入項目。

## Gradle wrapper

`android/gradlew`、`android/gradlew.bat`、`android/gradle/wrapper/gradle-wrapper.jar`はGradle 9.4.1の公式配布物を使用する。スクリプトの著作権表示を保持し、jar内の`META-INF/LICENSE`にApache License 2.0本文が含まれる。

- [Gradle 9.4.1 source](https://github.com/gradle/gradle/tree/v9.4.1)
- [License](https://github.com/gradle/gradle/blob/v9.4.1/LICENSE)
- distribution SHA-256: `2ab2958f2a1e51120c326cad6f385153bb11ee93b3c216c5fccebfdfbb7ec6cb`
- wrapper jar SHA-256: `55243ef57851f12b070ad14f7f5bb8302daceeebc5bce5ece5fa6edb23e1145c`

## SDKと参考資料

Meta Spatial SDK、AndroidX/Compose等はGradleから取得する依存で、それぞれの利用条件が適用される。SDKのバイナリをこのrepositoryへ取り込まない。製品配布時のライセンス一覧・同梱noticeの監査は今後の受入項目である。

SDKの接続方法・版選定は[Meta公式samplesの固定commit](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/tree/822f4d1ec967d4975ae8db386502b60cda5218c0)と公式文書を参照した。Termux APKは[公式release](https://github.com/termux/termux-app/releases/tag/v0.118.3)から端末へ別途導入し、repositoryには同梱しない。

## ソース公開とAPK配布の区別

ルートの[LICENSE](LICENSE)はEidolonの独自実装に対するMITライセンスであり、Meta Spatial SDK、AndroidX、Cargoの依存、第三者のfixtureやassetsを一括してMITにするものではありません。

GitHub Actionsからダウンロード可能なdebug APKにはMeta SDKを含むバイナリ依存が入っています。リポジトリのPublic化は、保存中のActions artifactを第三者に公開する可能性があるため、ReleasesでAPKを配布しない場合も再配布条件を確認する必要があります。

**未完了の項目:** Android/GradleとRust/Cargoの推移依存のライセンス・NOTICE棚卸し、実APKに含まれるMeta SDKの配布条件、`META-INF/LICENSE`除外後の通知の完全性、SDKに含まれるテスト証明書・鍵の用途確認。正式配布前に各項目を検証します。

[OSSライセンス・公開前レビュー（2026-11-12）](docs/oss-license-publication-review-20261112.md)に調査結果と判断基準を記録しています。
