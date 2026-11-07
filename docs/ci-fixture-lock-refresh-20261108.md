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

CIの再受入れはこの変更をpushした後のrunで別途確認する。
