# Freedoom専用assetsの固定stream導入

hostで準備した固定8fileをTermux UID10158の専用新runへ転送する。engineの起動、APK操作、
既存session fileやcatalogの変更は行わない。28MBのWADをshell引数へ入れず、有界stdin streamを使う。

Chocolate Doom3.1.1はroot operatorが版固定で導入した。追加7/更新0であり、
実engineはAArch64/64LE、801632byte、UID10158、mode0700、nlink1、
SHA-256 `abcb1d304f6c9f859d46c156990c9db214b0a3e67707170a7655844dc0c5c253`。
`--version`は3.1.1/終了0だったが、ゲーム自体はまだ起動していない。

```sh
scripts/quest-install-freedoom-probe --serial "$EIDOLON_QUEST_SERIAL" \
  --prepared-directory "$EIDOLON_FREEDOOM_PREPARED_DIRECTORY" \
  --manifest-sha256 "$EIDOLON_FREEDOOM_MANIFEST_SHA256" \
  --engine-sha256 abcb1d304f6c9f859d46c156990c9db214b0a3e67707170a7655844dc0c5c253 \
  --artifact-root "$EIDOLON_TASK_ROOT/freedoom-install"
```

prepared directoryとmanifest SHAは[host準備](freedoom-minimum-probe-plan-20261108.md)の保存済み値を使う。
現在のengineから期待SHAを生成しない。engineが更新・変更された場合は止め、source pinと実機検証を見直す。
device上の自由path、exec、PID、env、UIDや書込み先は指定できない。

## 配備時の条件

- hostで全fileのowner/mode、size/SHA、読取り前後identityを照合する。manifestは固定schemaと
  WAD/DEMO/license3/config/launch-planの既知pinから再検証し、missing COPYINGや任意planへの変更を拒否する。
- SHAは各file、連結payload、header込みwireの3段で計算・比較する。hostが固定順の小chunkを送り、
  remoteは同じ固定fileだけを正確な長さで受け取る。余分な末尾、途中EOF、改変を拒否する。
- remoteはUID10158と実Python prefix→Termux HOMEを確認する。run-asのHOMEは使わない。
  engineは固定prefix内の`bin/chocolate-doom`だけを開き、版3.1.1/dpkg installed状態、固定SHA、
  ELF/size/owner/mode0700/nlink1を読み取り前後に検査する。ゲームを実行して検査しない。
- 保存先はHOME由来の`.local/share/eidolon/freedoom/0.13.0/<asset-run-id>/`固定。
  parentは同UID・非共有書込み・symlink無し、新run/assets/configは0700、fileは0600。
  既存runと途中失敗runは上書きしない。configは将来stateへコピーするimmutable templateのまま保存する。
- 全payloadとengine再照合が揃った後にだけ`manifest.json`と`install-receipt.json`を排他的に作り、fsyncする。
  途中失敗のdirectoryをreadyとして扱わない。receiptはインストールの証拠であり、ゲーム/GUIの成功ではない。

remote受信は180秒、host転送は195秒の固定期限。取消/EOF/期限とearly remote拒否を記録し、
hostが実際に起動したADB handle/groupだけを回収する。remoteは転送中にengine子を起動しない。
raw stderrや任意error本文を保存せず、固定reason/byte数/元終了codeと回収結果だけを残す。
source5個のhashを前後で確認し、圧縮bootstrapは49KiB以下に制限する。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/tests -p test_freedoom_install.py -v
```

host fixtureは偽engineを実行せず、公開ELF形式とpackage metadataを使ってstream/拒否/owner/mode/
nonoverwrite/EOF/期限を検査する。実配備はroot operatorの一回確認で分ける。
続く固定demo operatorが同じasset/engine pin、認証済み現行lease、所有game childとSDK full資源回収を照合する。
通常catalogのGUI許可はこのinstallerでは変更しない。
