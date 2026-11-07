# NX18a 接続先profile v2の契約

2026-11-08。調査基準 `837d483`。**保存用の型と明示移行APIを追加した段階**。
現行の `profile.json`、CredentialStore、Host、native transportはschema 1のまま動く。
自動移行、remoteへの接続、Linux helper、profile切替UIは本変更に含めない。
[遠隔Linux計画](remote-linux-gui-plan-20261107.md)の最初の実装単位。

後続の[B1通信側契約](remote-transport-b1-20261108.md)でv2の内部接続APIと固定SSH試験を追加した。
通常runtime/Host/UIへの配線、実設定fileの移行、遠隔GUI有効化は引き続き未実装。

## 型と範囲

Androidの `ConnectionProfileV2` と Rustの `model::connection_profile` は同じ契約を持つ。
両者は共通の合成TSV fixtureでcanonical bindingを照合する。fixtureに実在の接続先や鍵は含めない。

| field | 契約 |
| --- | --- |
| schema | 整数2。未知schema/fieldを拒否 |
| installation | 現APK identityの32桁小文字hexと完全一致 |
| profile_id | 小文字英字で開始、英小文字・数字・`-`の1〜64文字 |
| revision | 正の符号付き64bit範囲の十進**文字列**。先頭0禁止、丸め禁止 |
| display_name | 空白だけを除き、制御文字なし、UTF-8で160bytes以内 |
| backend | `termux_local` / `linux_ssh` |
| host | canonical IPv4、またはASCII DNS名。DNS名のみ小文字化、ここでは解決しない |
| port | 整数1〜65535。文字列・浮動小数・boolから変換しない |
| username | 英数字・`_`・`-`、先頭`-`不可、1〜64文字。Termuxは従来どおり`-`不可 |
| host_key | 従来と同じEd25519公開鍵。wire形式を確認してコメントを除いたcanonical値 |

IPv6、scope ID、IDN、末尾dot、URI、`user@host`、hostとportの連結表記は初版で拒否する。
複数labelのDNS名は最終labelを英字のみとする保守的な初期制約。`linux-build-01`等の短縮名は扱える。
DNS解決・候補切替・取消・timeoutは次段階。
`127.1`や先頭0付きIPv4のような別解釈は許さない。

`termux-local` IDは `termux_local` / `127.0.0.1:8022` に固定する。
`linux_ssh` は別IDに限定し、保存用型として受理しても現runtimeへの投影APIはnull/Noneを返す。
未知backendやIDをTermuxへ置き換えるfallbackは設けない。
既存v1 API/設定/認証値への書込みは行わない。

## 束縛と更新

bindingは以下の順をNULで区切ったUTF-8のSHA-256（小文字hex）。末尾NULなし。

```text
eidolon-profile-v2, installation, profile_id, revision, backend,
canonical host, decimal port, username, canonical host_key
```

表示名は接続identityではない。正規の設定変更は表示名だけでもrevisionを一つ進める。
DNSの一時的な回答IPをbindingへ付け替えない。
`replacement(expectedBinding, next, confirmed)` は現在bindingとの一致、確認済み、
同installation/ID、revisionが厳密に+1であることを要求する。最大値では枯渇しwrapしない。
変更した接続先へ古い認証要求・cleanup journalを付け替えないため、次段階のownerは
runtime/session/request IDとこのimmutable profile bindingを一緒に捕捉する必要がある。

型の `toString` / `Debug` は値を表示しない。`toFields` / serde JSONは設定専用経路に限る。
通常diagnosticsへendpoint、username、pin、設定JSONを載せない。

## 明示移行と旧journal

`migrateLegacyTermux` / `migrate_legacy_termux` は実際に読んだschema 1設定を入力する。
field集合、`termux-local`、installation、username、pinを確認したうえで、revision `"1"`、
固定loopback、表示名 `Quest / Termux` のv2と、移行receiptを返す。fileは書き換えない。

receiptが返せる旧binding候補は、**移行したv2の全fieldが現在値と同じ場合のみ**。
remote profile、endpoint/user/pin/revision変更後、表示名を変更したものへ旧bindingを流用しない。
v2 JSONに「移行済み」等の自己申告fieldは置かず、任意v2だけから旧候補を生成するAPIも設けない。
旧binding計算は現 `transport::journal::binding` と同じ順序
`installation, profile_id, username, canonical host_key` を保つ。

この候補はprocess所有権の証明ではない。既存のUID・nonce・PID/starttime・所有group・
lease検査と照合した後にだけ旧Termuxの回収へ利用できる。旧journalをv2 bindingへ
盲目的に書き換えない。実ファイルの移行はBで次の順序・中断復旧を確定する。

1. 現在のv1を読んでstrictに検査し、移行結果と旧journalのbindingを照合する。
2. 旧Termuxと確認できるentryだけ既存経路で回収する。未回収・不一致はpendingとして保存する。
3. 安全な回収記録の保全を含むatomic保存後、v2を読戻して一致を確認する。

これは採用予定の境界であり、現段階でこのI/O手順を実装・実行したわけではない。
Kotlin APIはJSONライブラリに依存しないtyped Mapを扱うため、接続するcodec側で16KiB上限と
duplicate key拒否を実装する。Rustのparseは両方を検査する。Kotlin codecが未配線のまま
JSON受入れ全体を検証済みと扱わない。

## 検証と次の実装

pure testsで、共通binding vectors、2^53超/最大revision、旧v1投影、移行receiptの対象限定、
pin/endpoint/user変更、stale更新、確認なし、revision再利用/枯渇、不正型/未知field/endpointを確認する。
既存ConnectionProfile testsとmodel全体も実行する。通常Termuxの実機再受入れやremote接続の成功は未確認。
Rust modelのSHA-256/base64は既にlock内にある `sha2 0.11` / `base64ct 1` を再利用する。

次はBでprofile別endpoint・credential event・journal ownerを接続し、hostの固定SSH fixtureで
pin取り違え/取消/late要求/回収の分離を確認する。Linux helperは別adapterとして固定SHM fixtureから始める。
remote採用はその後であり、このpure型の追加を通常UIへの有効化根拠にしない。
