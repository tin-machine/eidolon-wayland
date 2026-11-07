# NX17b: remoteの回収責務とlive判定


保存済みBuild165の通常foot診断では、remoteが`phase=running`でも`cleanup_pending=true`だった。
このflagは実行中ownerが終了時に回収すべき責務を持つことを示し、回収実行中とは限らない。
初版の`cleanup_pending=false`必須は実物を誤って拒否していたため撤回する。
認証済み起動scope/child PID・start/receipt/Boardの照合は維持し、
`finished`・EOF・exit status・exit signalを受け取ったremoteはrunningの表示が残っていても分類しない。
公開fixtureも実物と同じrunning/cleanup_pending=trueへ修正し、終了証拠4種を負例に追加した。

検証はcapture10件、owner16件、core all-targets clippy（`-D warnings`）がPASS。
実行中の正常状態を受理し、終了通知が先行した状態を拒否する契約の修正であり、
グループのSDK表示や実機アニメーションの受入れではない。
