# NX18 C1: 通常Linux用の接続設定を独立保存する

2026-11-08。固定GTK probeの後に通常Linux GUIへ進むための保存境界。
この変更は接続や起動を行わず、接続設定UI・ランチャー・通常Hostへの配線は後続C2/C3で行う。
固定SSH/SHM/GTKの検証結果を、任意GUIの起動許可へ変換しない。

## 保存と更新

初版は既存のQuest内TermuxとLinux一台のうち一接続先を選ぶ。複数hostの同時接続は対象外。

- Termuxの `credentials/profile.json` と鍵、debugの `remote-probe`、各既存journalは変更しない。
- 通常Linuxのprofile IDは `remote-linux`、backendは `linux_ssh`。`linux-probe` は読込でも拒否する。
- 新しい保存先は `no_backup/linux-profile/remote-linux.json`。秘密鍵を入れず、接続metadataは非公開とする。
- 既存v2の10-field schema、16 KiB上限、重複key・未知field・不正UTF-8・型変換拒否を利用する。
  host keyはEd25519公開鍵を正規化し、installationとprofile identityを照合する。
- 初回revisionは十進文字列 `"1"`。変更は表示名だけでも+1とし、最大値では保存を拒否する。
  endpoint/user/pin/revisionを含むbindingを旧画面のexpected値と照合する。
- DNS名は保存できるが、現transportでの接続は `DNS_UNSUPPORTED`。IPv4への暗黙変換やTermux fallbackを行わない。

`LinuxConnectionDraft` はUI入力専用、`LinuxConnectionProfile.propose` は保存候補を作るだけである。
`LinuxProfileRepository` は `save(draft, expectedBinding, confirmed)` のCASと正規読戻しを担当する。
`LinuxProfileStore` はAndroid workerから呼び、0700 directory・0600 regular file・same UID・single link・
nofollow・size・保存前後のinodeを検査する。別Store instanceも同じprocess内lockを使う。
`remote-linux.tmp` はexclusive作成し、file fsync、検査読戻し、旧bytesの再照合、atomic rename、
directory fsync、保存後読戻しの順に進める。既存tmpを勝手に削除せず、失敗時は自分が作ったinodeだけ回収する。

publish後のfsync/読戻し失敗は旧profileへのrollbackや自動再保存をしない。
明示reloadで保存済みrevisionを確認し、必要なら新しいbindingで操作をやり直す。
不正保存fileを「未設定」として上書きしない。`toString` はdraft/profile本文を出さない。

## Hostへ配線する前の契約

保存APIだけでは稼働中sessionや回収journalの状態を判定できない。
Hostは通常workerと同じ直列化境界で、次の条件を満たしたときだけ保存・選択を受け付ける。

1. input/IME/move/menu/group/SDK debug leaseなどの操作ownerを終了し、押下とfocusを解除する。
2. 旧appとforwardを停止し、remote receipt・旧profile bindingのjournal・local resource・SDK targetの回収を確認する。
3. 未回収なら旧profile/鍵/endpointを保持する。新profileへjournalを書き換えたり、新profileで見えないことを回収済み扱いしない。
4. 回収完了後に保存し、immutableなprofile/backend/endpoint/revision/bindingを新接続要求へcaptureする。
5. credentialはcaptured runtime/request/session/profile/bindingに一度だけ供給する。設定を再読して遅い要求の宛先を変えない。

Termux v1の利用継続にv2への自動移行を要求しない。明示移行を実装する場合だけ既存の移行receiptと
旧journal回収契約を使う。通常Linux設定をdebug stagingから自動importする処理も設けない。

## 次の実装単位

1. **C2a: Linux固定foot。** Linux専用helperの固定terminal起動・process group・Waypipe/StreamLocal・
   別journal・lease回収を実装する。profile保存をSSH成功やGUI準備完了と扱わない。
   最初はfootだけを許可し、catalog/任意shell/GUIを同時に解放しない。
2. **C2b: Linuxアプリ定義。** 許可した実行file/argv/cwdと独立instanceの契約、Linux namespaceのcatalogを接続する。
   Termux pathや環境を流用せず、workload未対応値はunknownのまま表示する。
3. **C3: UIとランチャー。** 選択中の接続先名を表示し、profile/runtime/session/catalog revisionで
   読込結果と起動要求を束縛する。起動操作だけで旧接続を無断切替しない。
4. **受入れ。** 旧Termux回帰、remote foot表示/入力/終了、切断・再接続・同identity回収、複数窓の予算とSDK資源、
   launcherの別host誤配送拒否を確認してから通常利用を有効にする。物理HMD/キーボード確認は最後に分ける。

## 検証範囲

pure fixturesで初回pin確認、debug/Termux namespace拒否、stale保存、二Store競合、
publish前後の失敗、不正保存file、DNS未対応、revision枯渇と値の非表示を確認する。
既存v1/v2/codec fixtureも併せて実行する。Android APIの型検査は行うが、device filesystemの
実fsync/権限/中断復旧、Host採用、通常remote接続はこのcheckpointの完了範囲に含めない。
