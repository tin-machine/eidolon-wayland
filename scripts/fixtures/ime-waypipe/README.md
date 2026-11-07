# text-input-v3 の実 Waypipe fixture

repository root で `scripts/check-waypipe-ime` を実行する。既存の固定 Waypipe host binary と共有 `target/` を利用し、Cargo は locked/offline とする。実機操作、追加 download、production IME backend の有効化は行わない。

実 `ProtocolState` → Waypipe client → Waypipe server → 別 process の raw Wayland client を接続する。client は実 xdg_toplevel と SHM buffer で 2 窓を map し、固定日本語の preedit/commit/done、古い配送の拒否、geometry-only commit と配送の競合、focus 切替、reset、surface 破棄、切断後の資源回収を確認する。geometry 競合では capture 済み DTO の serial を書き換えず配送し、client が最新 serial の done と一度だけの commit を受けることを検査する。

入力本文は記録せず、artifact には source hash、固定 binary hash、件数、serial、資源数だけを残す。既定保存先は codex-work-root 設定配下の `eidolon-ime-implementation-20261107/waypipe-validation/`。directory は 0700、記録は 0600。Waypipe 両端と client は fixture が所有する process group に閉じ、終了時に wait する。

これは synthetic input の protocol 検証であり、Android InputConnection、実 IME の候補 UI、foot の rendering/PTY 配送、Quest の表示・キーボード受入れは別 gate で確認する。
