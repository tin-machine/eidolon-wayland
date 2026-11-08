# Freedoomの通常ランチャーへの明示登録

固定demo128と通常ランチャーからの対話起動は別の確認である。
demo128の自然終了・SHM描画・SDK全資源回収はBuild172で成立した。
通常起動、ゲーム内入力、閉じる確認、helper Stopの実機確認はこの登録だけでは成立しない。

`scripts/quest-register-freedoom-launcher`は固定ID `freedoom`、Chocolate Doom3.1.1、
`freedoom_phase1_software` profile一項目だけを扱う。自由exec/argv/env/path/PID入力はない。
引数は事前配備した32hex asset run-idと、配備記録にある既知64hex manifest SHAである。
現在のdebug APK source digest/process、Termux UID10158、同じbuildの3file helper hash鎖、
全asset/COPYING/credits/engine/SDL/EGL runtime pinを照合する。
観測設定の変更、ゲーム起動、catalog reload、通常GUI全般の解禁は行わない。

```bash
# まず確認する。既存catalog本文や他アプリの定義を記録しない。
scripts/quest-register-freedoom-launcher --serial "$EIDOLON_QUEST_SERIAL" \
  --asset-run-id "$FREEDOOM_ASSET_RUN_ID" --manifest-sha256 "$FREEDOOM_MANIFEST_SHA256"

# inspect result.jsonのremote.revisionを渡す。未作成ならabsentと明記する。
scripts/quest-register-freedoom-launcher --serial "$EIDOLON_QUEST_SERIAL" \
  --asset-run-id "$FREEDOOM_ASSET_RUN_ID" --manifest-sha256 "$FREEDOOM_MANIFEST_SHA256" \
  --action register --expected-revision "$FREEDOOM_CATALOG_REVISION"
```

既存catalogはrevision一致時のみ既存項目を保持して一項目追加する。
既に同じIDで異なる定義がある場合は拒否し、上書きしない。同一定義なら変更しない。
catalog未作成時は既定のターミナル/エディターとFreedoomを新規0600 staging fileへまとめ、
exclusive linkで採用する。検査後に別fileが作られた場合も上書きしない。
設定先はTermuxの検証済みHOME配下 `.config/eidolon/launcher.toml`だけであり、
親symlink・他UID・group/world writeを拒否する。

結果はprivate artifactの`result.json`へ残る。`registered`も`execution=not_run`である。
登録後の通常GUI検証ではproduction `launchRegistered`と現行lease/launch receipt/cookieを対応させ、
SHM/SDK初frame、元foot継続、GUI子の自然exitとTERM/KILL、全full-target/Board/controls/MODE回収を分けて確認する。
ウィンドーcloseは確認画面が出る可能性があるため、即終了を前提にしない。
合成キーの配送とゲームの反応、物理キーボードとHMDでの見え方も別に扱う。
