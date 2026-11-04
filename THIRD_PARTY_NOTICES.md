# Third-party components

## Gradle wrapper

`android/gradlew`、`android/gradlew.bat`、`android/gradle/wrapper/gradle-wrapper.jar`はGradle 9.4.1の公式配布物を使用する。スクリプトの著作権表示を保持し、jar内の`META-INF/LICENSE`にApache License 2.0本文が含まれる。

- [Gradle 9.4.1 source](https://github.com/gradle/gradle/tree/v9.4.1)
- [License](https://github.com/gradle/gradle/blob/v9.4.1/LICENSE)
- distribution SHA-256: `2ab2958f2a1e51120c326cad6f385153bb11ee93b3c216c5fccebfdfbb7ec6cb`
- wrapper jar SHA-256: `55243ef57851f12b070ad14f7f5bb8302daceeebc5bce5ece5fa6edb23e1145c`

## SDKと参考資料

Meta Spatial SDK、AndroidX/Compose等はGradleから取得する依存で、それぞれの利用条件が適用される。SDKのバイナリをこのrepositoryへ取り込まない。製品配布時のライセンス一覧・同梱noticeの監査は今後の受入項目である。

SDKの接続方法・版選定は[Meta公式samplesの固定commit](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/tree/822f4d1ec967d4975ae8db386502b60cda5218c0)と公式文書を参照した。Termux APKは[公式release](https://github.com/termux/termux-app/releases/tag/v0.118.3)から端末へ別途導入し、repositoryには同梱しない。
