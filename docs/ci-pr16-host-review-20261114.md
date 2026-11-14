# PR #16 Host CI 再開調査 — 2026-11-14

状態: **Draft / 製品統合未完了**。本記録はソース点検とCI修正であり、Quest受け入れ証拠ではない。

## 調査基準

- main: `3d28988cf90d39d3aa0ab2474c88eb89a829533f`
- PR #16 調査開始HEAD: `3ae47672209a5f4fd79d7751548f469e13fde50c`
- [検証 #43583250760](https://github.com/tin-machine/eidolon-wayland/actions/runs/43583250760): Hostのみ失敗。Android、Documentation integrityは成功。
- [CodeQL #43583250724](https://github.com/tin-machine/eidolon-wayland/actions/runs/43583250724)、[L7a #43583250705](https://github.com/tin-machine/eidolon-wayland/actions/runs/43583250705)は同HEADで成功。

## 直接の失敗原因と修正

Host job `132412328541` は `cargo fmt --all --check` で停止。Rust 1.95 formatterが要求する差分は5箇所であり、このrunではClippy/Rust/Pythonテストを通過したとは言えない。

`protocol.rs` の4箇所と `v3_fixture_test_support.rs` の1箇所を修正する。前回のformat-onlyコミットは4箇所で逆向きの整形を適用し、別の1箇所を未修正にしていた。CIのゲート、formatter設定、テストのassertionは緩和しない。

## 広めに確認した境界

| 境界 | 点検結果 / 今回の変更 |
| --- | --- |
| listener origin設定時期 | `bind_v3_origin` は同Session/既存origin/sealを確認するが、legacy clientをacceptした後の呼び出しを防いでいなかった。成功acceptを単調な `accepted_any` で記録し、切断でclient数が0に戻ってもv3への後付け変更を拒否する |
| 未bind/封鎖済listener | 未bindでのverified accept、封鎖後のbind、accept後のbindを回帰試験に追加。既存v2/Termuxのuntagged acceptは維持 |
| fixture workerの異常終了 | result channelのDisconnectedをEmptyと同じように24〜26秒待たない。join結果を保持し、通常終了ループを抜けた後はprotocol/SHM drainを実施してからworker failureを報告する |
| assertion unwind | 既存RAII workerのstop/socket shutdown/joinを維持。既存shared fixture lockのcatch/unlock/resumeも維持。worker panicを明示joinが失わない回帰試験を追加 |
| slot / main tombstone | 先行修正 `b84714f` のWindowId別slot予約と、main tombstone GC後のcut拒否を点検。未採用dialogのpending数だけでlive mainを押し出す旧方式には戻さない |
| 9→10 / parent fixture | 既存実Unix Wayland/Smithay fixture、parent unmap/remap/destroy、ACK待ちslotのassertionを維持。fixtureの二重ACKを本番SDK証拠とは扱わない |

## 残存P1 — CI修正とは別に必要

1. Stop/seal後の `WindowHistory::observe` と `Session::refresh_stats` の回収専用観測経路。表示admissionを閉じてもdestroy/retirementの事実は収集し続ける必要がある。
2. `SpatialPanelAdapter` の実Create ACK/Entity/Surface/listener、Scene/generationと現在のrenderer content frameをI4へ接続する。現在のv3 default denyを成功stubで置き換えない。
3. 全世代のnative+SDK退役証拠から `confirm_retired` / B1 `retire_confirmed_dialog` を呼ぶ本番GCと、I2 `take_v3_reclamation → confirm → finalize_reclaimed → publish_v3_finalized`。remote Completeだけでjournalを消去しない。
4. 完全な入力・parent/roleイベント検査、実SSH+C2+Waypipe+MousepadとQuestの受け入れ。既存の[PR #16検証記録](linux-desktop-v3-pr16-p1-verification-20261114.md)も参照する。

## 検証の境界

この修正作成環境にはRust toolchainとcheckoutがなく、外向きDNS接続も利用できなかった。ローカルのcargo fmt/Clippy/testsを実行済みとは記録しない。新HEADのGitHub Actions結果を個別に確認し、PR本文にrun URLと結果を追記する。古い成功runを新HEADの成功と混同しない。

B1 golden/shared contract、15秒の本番lease、I5設定保存/ランチャー、GitHub Actionsの必須チェックは変更していない。PRはDraftのまま維持し、mainへマージしない。
