# 現行SDL検証用leaseの読取り専用capture

`scripts/quest-capture-sdl-lease`は、現在のAPKと認証済みremote ownerに対応する登録rootを照合し、
`WAYLAND_DISPLAY`と`XDG_RUNTIME_DIR`だけをprivate artifactへ保存する。SDLの起動は行わない。

```bash
scripts/quest-capture-sdl-lease --serial "$EIDOLON_QUEST_SERIAL"
```

指定できるのは対象ADB serialとhost側の`--artifact-root`だけで、PID・nonce・remote path・envはCLIから受け付けない。
固定のAPK diagnosticsから現在のapp/transport/workloadを取得する。
DiagnosticsStore→JNI snapshot→Runtimeのschema、現在のnative source digest、debug build、session/nonce/UID、
remote childと登録rootを検査する。native digestは`native-build-inputs`と同じfile集合から計算する。
operator tool追加だけでcommitが変わることは許容し、native入力内容のdigest一致を必須にする。

`transport.pending_cleanup`には正常稼働中のjournal entryも含まれる。
現行nonce/session/UID/child identityに一致する1件をactiveとして許容し、無関係な古いentryは別に数えてblockedにする。
登録なし・観測OFFは`status=missing`で返し、観測設定を自動変更しない。複数登録を勝手に一つ選ばない。

Termuxでは同UIDの現行owner/child/root、boot ID、PID/starttime/groupを照合し、
開いたproc directory fdから上限付きで環境を読む。2環境値の解決先を、実在する同UIDの現在leaseのsocketと照合する。
取得前後でprocess/lease/socketの同一性を確認し、同じTermux単調時計で5秒を超えるsampleは使わない。
procの短いreadをEOFまでdrainし、許可や情報が不足した場合はblockedで返す。

最後にAPK diagnosticsの実際の更新とbinding不変を確認してから保存する。
`result.json`は実行時に読み直した`codex-work-root`配下の`eidolon-sdl-lease-capture/`に0600で保存する。
raw snapshot、全環境、本文、IME private API、logcatは保存しない。
`captured`は実行時点の読取り照合結果であり、後日の起動許可やSDL描画・停止回収の成功ではない。
実際にprobeを起動する段階では、同一性と所有lifecycleを再検査する。

PC検査:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts.tests.test_sdl_lease_capture -v
```
