# NX18 B1: 接続ごとの設定捕捉と固定SSH確認

2026-11-08。基準 `a628faf`。[profile v2の純粋契約](remote-profile-v2-contract-20261108.md)に、
transport内部の接続APIとhost上の固定SSH fixtureを追加した。
**runtime/Host/UIにはv2を配線しておらず、Questや別Linuxマシンへの接続は未実施**。
旧 `Controller.connect(v1, request)` とTermux通常経路は維持する。

## APIと実行範囲

`connect_profile::ConnectSpec::from_v2(&profile)` は、保存用profileのendpoint、username、pin、
profile ID、revision、binding、helper対応可否をimmutableに捕捉する。
`Controller.connect_spec(spec, request)` は、そのspecをPendingConnectから接続ownerまで引き継ぐ。
接続先をController生成時の一個のアドレスで上書きしない。従来の固定endpointテスト用overrideは
v1の`cfg(test)`経路だけに残し、v2試験はprofile内の実際のfixture portへ接続する。

初版はIPv4 literalのみ接続できる。Aで保存可能なDNS名は `PrepareError::DnsUnsupported`
(`DNS_NOT_IMPLEMENTED`) として**ソケットを作る前に拒否**する。DNS名をloopbackへ置き換えない。
DNS取消/期限/worker回収とIPv6は後続実装である。

- `termux_local` は既存のhelper経路を使う。v1はschema/ID/usernameもtransport入口で再検査する。
- `linux_ssh` はSSH host key照合、公開鍵認証、既存の固定 `id -u` とtokenのexec確認だけ。
  成功状態は `Ready` と異なる **`ProbeReady`** にする。
- ProbeReadyでapp、terminal、catalog、forward、cleanup probeを呼んでも `NotReady` を返す。
  Termux固有pathを持つlauncher/helperは実行しない。StreamLocal受入れに必要な登録も行わない。

## 認証要求の束縛

v2ではlegacyの `provide(session, request, key)` を拒否する。
private owner向け `credential_scope()` が返すsession/request/profile ID/revision/bindingを使い、
`provide_for(session, request, binding, key)` で同じ接続へ供給する。
binding違い、古いsession/request、取消済み、二重供給はStaleで、勝手に別profileへ付け替えない。
scopeはAwaitingCredentialの間だけ取得でき、取消/停止/接続終了時に破棄する。

bindingと接続先・username・pinは通常Snapshotへ追加しない。scopeはSerialize/Debugを実装しない。
Snapshotへ追加するのはprofile revisionの文字列とProbeReady状態だけ。
coreのcredential_required eventとAndroid Hostは依然v1を使用し、このv2供給APIのJNI配線はB2で行う。

## 回収journal

既存v1のbinding計算と保存schemaは変えない。v2の新接続にはAのdomain-separated bindingを使い、
同じTermux endpoint/user/pinでもv1 bindingを自動的にv2 bindingへ書き換えない。
旧profileの明示移行receipt・journal・atomic設定保存の調停はB2で行う。

bindingまたはUIDの違うentryは `CLEANUP_PROFILE_MISMATCH` として保存し、相手先でauditも削除もしない。
Linux ProbeOnlyではbinding/UIDが一致するentryでも `CLEANUP_BACKEND_UNSUPPORTED` とし、
Termux cleanup helperを実行しない。未回収entryがある間は従来どおりCleanupPendingで、
新しいGUIを始めない。profile別journal分離や孤立entryの扱いを、この変更で暗黙に緩和しない。

## 固定fixtureの証拠と限界

host上のlocalhost SSH serverを二つ立て、**同一Controller**がprofile内の別port/pinへ順に接続する。
v2の2^53超revision、scopeの一致、不一致binding拒否、古い要求の拒否、誤pin時に認証要求ゼロ、
Linux helper系APIの拒否、通常Snapshotへのpin/binding非掲載、停止時worker/connectionゼロを確認する。
SSH serverは受けた固定probe以外のexecも数え、Linux試験でゼロであることを検査する。
旧v1/別hostのv2/同scopeのv2を混在させたjournalは、全entryが保全されcleanup execゼロであることを検査する。
DNSは型作成の失敗として検査し、実DNSや実LANへアクセスしない。

既存transport testsも含めて実行する。これらはSSH/owner契約の検証であり、Linux helper、
Waypipe/SHM、GUI、入力、LAN性能、HMDの受入れ結果ではない。
次はB2の保存/Host/runtime配線と、Linux helperの独立adapter・固定SHM検証を進める。

## 続き

APK UIDでの固定検査用配線は[NX18 B2](remote-apk-probe-b2-20261108.md)を参照。通常Linux GUIの有効化とは分離する。
