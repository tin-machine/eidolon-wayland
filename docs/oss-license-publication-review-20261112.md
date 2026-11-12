# Eidolon Wayland — OSSライセンス・公開前レビュー（2026-11-12）

> Status: 調査記録／提案。正式なライセンス適用・Public化・APK公開を承認する文書ではない。
>
> 対象: tin-machine/eidolon-wayland、調査時点の main `f894d796a06bcf0fafb1567718cd180ecf29009b`。
> 法律意見ではない。依存物の実配布条件は正式配布前に最新の原文と成果物で確認する。

## 1. 暫定結論と適用範囲

- **Eidolon独自のRust/Kotlin/Python等**: MIT License（SPDX: MIT）を採用する案を推奨。現時点でルート `LICENSE` は存在しないため、**まだ正式適用していない**。
- **第三者のソース・パッチ・テストfixture・素材**: ルートのMITでは再許諾しない。各コンポーネントの権利表示、ライセンス本文および必要な再配布条件を維持する。
- **Meta Spatial SDK**: Meta Platform Technologies SDK License Agreementが適用される。Gradle経由で入手するSDKのライセンスをMITへ変更するものではない。SDKを同梱したAPKの配布可否は別途確認する。
- **自動生成・AI生成コード**: 生成された事実だけでは著作権の帰属や保護が決まらない。人間の創作的関与と第三者素材の由来によって評価が変わる。MITの適用を宣言できる範囲に限定する。
- **現時点の公開判断**: OSSのソース公開は条件付きで進める方針。ライセンス適用、第三者素材確認、およびActions artifact中のAPK公開可能性の精査をPublic化前に行う。

Meta公式の Spatial SDK Samples も、主なコードをMIT、Meta SDKと一部素材をMeta独自ライセンスとする複数ライセンス構成を明示している。これは構成上の先例であって、Eidolonの個々の依存物の配布を一律に許諾するものではない。

## 2. 現行ソースで確認した主な依存物

以下は `THIRD_PARTY_NOTICES.md`、`native/deps.lock.json`、`android/app/build.gradle.kts`等を参照したスナップショット。表中の表現は正確なライセンス条項の代替ではない。

| 対象 | 現行の認識・ライセンス | 公開／配布時の注意 |
| --- | --- | --- |
| Smithay 0.7.0 | MIT | 改変パッチと上流著作権・ライセンスを維持。 |
| Waypipe 0.11.3 のC版 | MIT | APKにPIE helperとして同梱。Rust版と区別。 |
| LZ4 1.10.0 | BSD-2-Clause | 同梱通知を保持。 |
| Zstandard 1.5.7 | BSD-3-Clauseを選択 | 同梱通知を保持。 |
| libxkbcommon 1.8.1 | 上流の複数ライセンス・権利表示 | `native/licenses/xkbcommon-1.8.1-LICENSE`を保持。 |
| xkeyboard-config 2.48 | 上流の複数許諾条件 | 個別の著作権・COPYINGを維持。 |
| Mousepad 0.6.3 | GPL-2.0-or-later | 本体は別Linuxホストで動作しAPKへ同梱しない。repository内のテスト用gschemaにはGPLの条件が残る。 |
| Gradle Wrapper 9.4.1 | Apache-2.0 | 配布されるJARと付随表示を確認。 |
| AndroidX / Jetpack Compose / Kotlin等 | コンポーネントごとのライセンス | Gradle推移依存を含む最終ライセンス一覧が必要。 |
| Rust/Cargo依存全体 | `Cargo.lock` 等で固定 | **全依存を網羅した配布notice監査は未完了**。 |
| Meta Spatial SDK 0.15.0（4つのGradleモジュール） | Meta Platform Technologies SDK License Agreement | SDKのMIT再許諾不可。ソース公開とAPK再配布を混同しない。 |

参照: `THIRD_PARTY_NOTICES.md` により、WaypipeはMITのC版のみ利用、Termux APK自体はリポジトリに同梱しないと記載されている。`android/app/build.gradle.kts`は `com.meta.spatial:meta-spatial-sdk`、`-toolkit`、`-vr`、`-compose` の0.15.0に依存する。また `packaging { resources.excludes += "META-INF/LICENSE" }` があるため、APK配布時に必須NOTICEが別の形で保持されるかを確認する。

### GPLとの境界

Mousepadを別プロセス・別Linuxホストで利用しているからといって、それだけでEidolon本体がGPLになるわけではない。ただしテストfixtureのschemaなど、直接コピーしたGPL対象物の再配布条件は保持する。将来、本体のコピーや派生コードをAPK/ソースへ取り込む場合は改めて確認する。

## 3. Copyright表記の役割と推奨案

MITの標準テンプレートは、例えば以下のように記す。

    Copyright (c) 2026 tin-machine

- **付けるメリット**: どの著作権者が許諾主体か示す。MITに従って再配布される場合、著作権表示と許諾通知を残してもらう条件の根拠になる。第三者ライブラリの表示と区別しやすい。
- **付けなくても著作権が生じ得る**: 著作権表記は登録制度・権利取得の手続ではない。表記だけではオリジナリティ、権利帰属、侵害の不存在の証明にならない。
- **表示の限界**: `tin-machine` が権利を有しない第三者の作品や、著作権保護対象とならないAI生成部分に、表記だけで権利を発生させることはできない。
- **実装案**: MIT採用を正式に決めた後、ルート `LICENSE` に標準文面と上記表示を置く。個々のソース全行に表示を追加する必要はない。ライセンス適用範囲と例外をREADMEと `THIRD_PARTY_NOTICES.md` から案内する。
- **名義**: `tin-machine` を利用する案は自然だが、法的な権利者名と表記上の識別子との関係は著作権者側の判断事項。将来コントリビュータが入る場合、他者の著作権表示を勝手に置き換えない。

MITは商用利用、改変、再配布、再許諾を広く認める一方、著作権表示とライセンス文面の保持を求め、通常の無保証条項を含む。GitHubでPublicにするだけでは、再配布・改変を許諾したことにはならないため、明示的な `LICENSE` を設ける意味がある。

## 4. Meta Spatial SDKの鍵の扱い

過去の公開前監査で報告された内容（今回の文書作成時にバイナリ解析を再実行したわけではない）:

- Android debug APK内の `lib/arm64-v8a/libMetaSpatialSDK_1.so` に秘密鍵形式のヘッダーを21個検出。
- 解析可能な秘密鍵ブロック10、異なる公開鍵4、同じライブラリに対応X.509証明書が存在、`localhost` / `test` を含む証明書識別名が報告された。
- Actionsの33件のartifactで、同一ライブラリに対する検出が合計693回（33×21）観測された。693個の独立した秘密鍵を意味しない。
- **分類**: Meta SDK由来のテスト用TLS鍵セットという仮説と整合する。Eidolon固有の認証情報が混入したことを示す証拠は見つかっていない。ただし実行時用途、上流バイナリとの厳密な同一性、鍵が一般公開テスト用であることの一次資料による確認は未完了。
- **公開判断**: Git履歴の書き換えを必要とする証拠ではない。APK公開前はビルドされたライブラリのhashを上流の同一版と照合し、SDKのライセンス・必要なNOTICE・鍵の利用用途を確認する。秘密鍵本体は監査資料に載せない。

## 5. ソース公開とActions artifactの重要な境界

**Publicへの変更は「ソースだけ公開」の意味にならない**。GitHubの公式説明によると、公開後はActionsの実行履歴とログが第三者から参照可能になる。さらに、リポジトリに読み取り権限のあるユーザーはartifact取得APIを利用できるため、保持期間中のdebug APKも公開対象となり得る。

前回監査では222 run、503 jobを列挙し、461個の実行ジョブログのうち460個をv3で走査、最後の1個を別経路で取得し簡易パターン検査した。artifactは94件列挙、92件を検査、2件はHTTP 410/再取得404により取得不能だった。上記値は調査時点の観測値であり、公開時点の最新インベントリではない。

APKをReleasesで提供しなくてもActions成果物で実質的なバイナリ配布が起こる点に注意。Public化前に、現在ダウンロードできるAPKについてライセンス上の再配布可否とNOTICEを確認する。artifactの削除・保持設定変更は本調査の範囲外であり、別途明示承認が必要。

## 6. 公開前／配布前のゲート

### A. リポジトリをPublicにする前

1. 著作権者・適用対象を確認し、ルート `LICENSE` にMIT本文を追加、READMEと `THIRD_PARTY_NOTICES.md` から適用範囲を案内する（本レビューでは**まだ変更しない**）。
2. repositoryに直接保持するGPLや他ライセンスのfixture、引用、改変パッチのライセンス・著作権表示を最終確認する。
3. Actions成果物に含まれ、Public化後に取得できるAPKを棚卸しし、Meta SDKの再配布と第三者NOTICEの条件を確認する。
4. 公開前のセキュリティ監査（秘密情報、Actions、runner設定、個人情報）と判断を独立に維持する。

### B. debug APK / release APKを配布する前

1. Gradle推移依存・Cargo推移依存・native静的リンク・assetsについてSBOMとライセンス一覧を作る。
2. Meta SDK 0.15.0の配布条件・含有物のNOTICEと鍵・テスト証明書の用途を確認する。
3. `META-INF/LICENSE`の除外が適切か検証し、必要なlicense / noticesをAPK内または配布物で利用者へ提供する。
4. GitHub Actions artifact、GitHub Releases、ストア配布の各経路で条件が満たされるか評価する。

## 7. 出典（一次資料を優先）

- GitHub — Licensing a repository: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository
- GitHub Choose a License — MIT: https://choosealicense.com/licenses/mit/
- Meta — Spatial SDK Packages: https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-packages/
- Meta — Meta Platform Technologies SDK License Agreement: https://developers.meta.com/vr/licenses/oculussdk/
- Meta公式 Spatial SDK Samples — 複数ライセンス構成: https://github.com/meta-quest/Meta-Spatial-SDK-Samples
- GitHub — Setting repository visibility: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility
- GitHub — Actions artifacts API / read access: https://docs.github.com/en/rest/actions/artifacts
- Repository — `THIRD_PARTY_NOTICES.md`, `native/deps.lock.json`, `android/app/build.gradle.kts`, `Cargo.lock`, `native/licenses/`, `scripts/tests/fixtures/licenses/`

## 8. 変更の範囲

この文書は調査の記録であり、`LICENSE` の新設、既存ソースの再ライセンス、GitHubリポジトリのvisibility変更、artifact削除、Actions設定変更は一切伴わない。
