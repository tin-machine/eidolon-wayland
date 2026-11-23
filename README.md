# Eidolon Wayland

> Linuxに肉体を残し、その姿だけを異界へ投影する。

<p align="center">
  <img src="docs/assets/readme/10-home-lab.webp" alt="Eidolon Waylandのコンセプトイラスト。Quest 3のMR空間にLinuxアプリのウィンドウを立体的に配置し、手で操作している様子" width="100%" />
</p>
<p align="center"><sub>Concept illustration — 実機スクリーンショットではありません。</sub></p>

**Eidolon Wayland** は、**Meta Quest 3のMR空間でLinuxアプリを独立したウィンドウとして配置・操作する**ための、オープンソースの実験的な空間デスクトップです。

従来のデスクトップ画面を大きな1枚の板として映すのではなく、ターミナルやGUIアプリそれぞれを空間に取り出し、目の前や左右、奥行きのあるワークスペースに並べて使うことを目指しています。

## 目指す体験

- **画面の枠を超えるワークスペース** — 複数のアプリウィンドウを空間に配置し、移動・サイズ変更・グループ化する。
- **Linuxアプリをそのまま活用** — Quest内のTermuxや別のLinuxホストでアプリを実行し、Waylandの画面をQuestへ転送する。
- **MRならではの操作** — パススルー背景にウィンドウを重ね、キーボード・ポインター・XR入力を組み合わせる。
- **ホストをまたぐ作業空間** — 将来的には複数ホストのアプリを選んで起動し、1つの空間にまとめる。

## 仕組み

EidolonはQuest上のAndroidアプリに**Rust製Wayland compositor**を組み込みます。Linuxアプリは実行元に残し、**SSHとWaypipe**を介してウィンドウを受け取り、**Meta Spatial SDK**でXR空間に表示します。

```text
Quest内のTermux / 別Linuxホスト
       └─ Waylandアプリ（foot、GTKなど）
                    │
              SSH + Waypipe
                    │
                    ▼
       Eidolon（Quest 3 / Android）
         Rust / Smithay / EGL・GLES
                    │ JNI
         Kotlin / Meta Spatial SDK
                    │
                    ▼
            MR空間のウィンドウ
```

技術スタックの中心は **Kotlin・Rust・Smithay・EGL/OpenGL ES・SSH・Waypipe** です。デスクトップ全体のミラーリングではなく、ウィンドウ単位の扱いを重視しています。

## 現在の位置づけ

**開発・検証中のプロジェクトです。** Quest内Termuxのターミナル表示や複数窓操作には実装・検証実績がありますが、任意のLinuxアプリを複数ホストから同時に利用する体験は、引き続き開発の目標です。画像は構想を表現したもので、対応機能や実機での表示を保証するものではありません。

詳細な実装状況やCI・実機検証の記録は、READMEには列挙せず、[ドキュメント](docs/overview/README.md)と[進捗・検証記録](docs/evidence/progress.md)から確認できます。

## はじめる・開発する

- [利用方法・初回セットアップ](docs/guides/usage.md)
- [アーキテクチャ](docs/architecture/architecture.md)
- [開発・デバッグ](docs/development/development-automation-and-debugging.md)
- [設計・機能別ドキュメントの入口](docs/overview/README.md)

## ライセンス

Eidolon Waylandが権利を持つコード・ドキュメントは[MIT License](LICENSE)で公開しています。第三者のライブラリや素材、Meta Spatial SDKにはそれぞれのライセンスが適用され、MITがAPKや依存SDKの再配布を一律に許可するわけではありません。詳細は[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)を参照してください。
