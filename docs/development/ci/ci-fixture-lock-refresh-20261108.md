# CIの独立fixture lock更新

Actions run `42399258404`（`e76d313`）はAndroid成功、hostの通常suite成功後に
clipboard検査で停止した。独立fixtureの`Cargo.lock`が、NX18 profile型で追加した
model依存`base64ct`/`sha2`を反映しておらず、`--locked --offline`が正しく拒否していた。

clipboard/IMEの二つの独立lockを、各manifestへの`cargo metadata --offline`で更新した。
既存registry package版は変更せず、modelの二依存を追加した。IME側ではpackageの順序もCargoが正規化した。
検査の`--locked --offline`条件は維持する。

ローカルで再実行した結果:

- `scripts/check-waypipe-clipboard`: 終了0、core bounds検証成功。既存の`observed_intermediary_buffering`を維持し、end-to-end timeoutを合格扱いしない。
- `scripts/check-waypipe-ime`: 終了0、固定payloadの12 checkpoint成功。停止後のclient/object/window/surface/pixel/poolは0、所有group3件のwaitを確認。

```sh
CARGO_INCREMENTAL=0 scripts/check-waypipe-clipboard
CARGO_INCREMENTAL=0 scripts/check-waypipe-ime
```

push後の`8c92e61`/run `42402084389`と`e8bd699`/run `42402741125`は、host/android/report全体で成功した。

## GTK実fixtureのCI依存

`cc5c5cf` のCI run `42406322221` はhostとAndroidの両jobで、
`test_gtk_interaction_fixture.HostGtkTests.setUpClass` の `pkg-config gtk+-3.0` が失敗した。
Android APK生成自体やRust warning修正後のclippyではなく、両jobが実行するPython suiteの依存不足だった。

両jobのapt導入へ `libgtk-3-dev xvfb` を追加した。検査をskipに変更せず、
GTKの実C compileと隔離Xvfb上のcallback検証を維持する。ローカルで関連12件、
統合後のPython236件（既存skip7件）、workflowの固定版actionlintが成功した。
新しいremote runでCI全体が成功したことは、別にrun IDと結果で確認する。
