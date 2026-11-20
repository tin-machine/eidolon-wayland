# I6 N02: 通常 Host Launch の型付き証拠と formal validator

2026-11-16。参照: [I6 詳細設計](linux-real-app-v3-i6-detailed-design.md) §11/§13–15、
[証拠 validator 第1単位](i6-evidence-validation.md) および
[N01 Inspect-only](i6-n01-inspect-only.md)。

## 範囲と証拠の権限

通常 Registry → SSH Connect/Inspect → 同 actor fixed catalog → typed v3 Launch →
実 local Waypipe/Wayland → GTK3 デモまたは Mousepad → native Create →
Host synthetic SDK binding (S) → EGL/SHM bounded readback (T) → B1 Ready →
Focus/Key → native/SDK/renderer/remote retirement → journal finalizer →
origin_retired → Runtime shutdown → Registry destroy、という既存実測経路を維持する。

今回の追加は N02 の **型付き receipt と固定 I6 validator の接続**であり、
Meta Spatial SDK の実 Entity や Quest 3 の acceptance ではない。

各 variant の実 C2 session/capture、native run_id は異なるため、
同一 run ID に偽装して一つの正式 report にまとめない。
それぞれが 28 family／87 variant／533 assertion の固定 manifest を持つ
個別 partial acceptance report を発行し、当該 N02 case のみを検証する。

出力する固定ファイル:
- n02-<variant>-native.json と n02-<variant>-environment.json: 実 Host/SSH/環境観測
- n02-<variant>-receipt.json: 同一 run / captured origin / full TargetKey / CreateSeq /
  adapter ID / real content-frame / SDK / retirement / external census を束縛する
- acceptance-<variant>.json と acceptance-<variant>-validation.json:
  固定 profile の部分判定。他86ケースは not_run
- container.json: image ID、exit code、外部 rescue。削除完了の証明ではない

formal validator は n02_receipt の保存 bytes を private root から no-follow で読み、
source、owner、唯一の target、R/S/T、census と順序を独立 oracle で再照合する。
現在の N02 は実 root target を1つだけ扱う。target-resource 参照はその derived
target_id に限り、producer の別名や別 capture を authority にしない。
固定 N02 manifest の evidence class は R+S のまま。R-class の複合 receipt に
実 S の synthetic SDK transcript と T の renderer readback を含めて検査する。

producer が pass を書いても validator が verified_case_oracles に追加しなければ
部分合格にならない。Host 全体の success flag は引き続き false、exit code は2。
N02 の10 assertion が全て独立検証されると unverified_assertions は0になり得るが、
これは記述された assertion の未検証数であり、残り85ケースの未実施は
固定 manifest と missing で検査する。533 assertion 完了とは解釈しない。

## 検証と残件

disposable N02 CI は GTK/Mousepad を直列実行する。外側 exporter が保存した
partial acceptance report と typed receipt を再読み込みし、正式 validator の再計算
結果を出力と厳密比較する。既存 R/S/T mutations に加え、owner/target/source の
すり替え、欠落 receipt、producer の偽 pass を unittest で拒否する。
patched oracle の単体テストは実物証拠ではなく、実 SSH/Waypipe/Host test と区別する。

未完了なのは、Docker 終了後の環境破棄を観測する外側の証拠
(environment_disposal=not_yet)、異なる native run を束ねる正式 aggregate、
N03–N11、F01–F12、C01–C05 の独立 oracle、および Android E1/Quest I7/H の
実 Meta SDK/physical input/保存・空間操作の検証である。
case manifest、product barrier、I6 success flag を緩めない。
