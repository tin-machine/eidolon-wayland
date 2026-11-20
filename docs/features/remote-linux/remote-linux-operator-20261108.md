# Linux固定SHM検査の再現用operator

`scripts/quest-run-linux-shm`は[B4のvirtual検査](remote-linux-apk-shm-b4-20261108.md)、
[B5のSDK検査](remote-linux-sdk-shm-b5-20261108.md)、同scopeの明示recoveryを実行する。
Linux側sshdやbundleの準備、host keyの承認、profile/pinsのstagingは別の手順で完了していることを前提とする。
接続先や鍵を探索したり、profileを新しいendpointへ付け替えたりしない。

## 前提と実行

- Quest 3、API 34以上、ADB承認済み、製品debug APKのMainActivity起動済み。
- checkoutのnative source digestと導入APKのbuild情報が一致すること。source commitだけが変わるdocs/script追加は許容する。
- 現APK UIDとrun-as UID、導入versionCode、live PIDとdiagnosticsのprocess_idを照合する。
  source digestは環境変数のoverrideを使わず、現checkoutの入力ファイルから計算する。
- profileとpinsは既存の正規staging手順で配置する。Intentには固定caseと32桁小文字hex nonceだけを渡す。
  `--artifact-root`はPC側の保存先であり、端末へ渡すpathではない。
- 他の検査との同時実行は避ける。アプリが別ownerを保持して新caseを拒否した場合、他のnonceを停止して枠を奪わない。

```sh
scripts/quest-run-linux-shm --case virtual --serial "$EIDOLON_QUEST_SERIAL" \
  --nonce "$EIDOLON_PROBE_NONCE" --artifact-root "$EIDOLON_ARTIFACT_ROOT"

# B4成功後、B5には別の新しいnonceを使う。
scripts/quest-run-linux-shm --case sdk --serial "$EIDOLON_QUEST_SERIAL" \
  --nonce "$EIDOLON_SDK_NONCE" --artifact-root "$EIDOLON_ARTIFACT_ROOT"

# 失敗したprobeと同じprofile/pins/endpoint/nonceを保持した明示recovery。
scripts/quest-run-linux-shm --case recover --serial "$EIDOLON_QUEST_SERIAL" \
  --nonce "$EIDOLON_PROBE_NONCE" --artifact-root "$EIDOLON_ARTIFACT_ROOT"
```

virtual/recoverは固定`LinuxSshProbeReceiver`、sdkは固定`DebugCommandActivity`を使う。
通常GUIや任意argvの起動口は追加しない。同case/nonceの結果が開始前から存在する場合は再起動せず拒否する。
検査中のapp process変更、diagnostic sequence逆行、source/version/UIDの不一致も拒否する。

## 結果と回収

virtualのPASSは100画素hash/copy/callback、remote回収、scope消去、local owner破棄、
設定不変と別handleの通常Ready/Idleへの復帰を必要とする。GPU/SDK表示の合格にはしない。
recoverは同じnative復帰条件に加えてquery/receiptの整合を確認する。
`not_required`はjournalが無く回収queryを実行しなかった結果であり、回収PASSと区別する。

sdkのPASSにはsourceと同targetの実draw証拠だけでなく、lease解放、旧runtime破棄、設定不変、
通常画面の可視/FOCUSED・同scene診断target・実SDK binding・3frame以上を要求する。
caseの途中の`pass`やnative復帰だけを、SDK復帰済みと扱わない。
設定変更の検出とowner回収の確認も別fieldで記録する。

| 終了code | `status` | 意味 |
| --- | --- | --- |
| 0 | `pass` | 選択caseの必要証拠と復帰を確認 |
| 3 | `not_required` | recovery不要。実回収を検証したPASSではない |
| 1 | `fail` / `failed` / `cancelled` | 検査不合格または開始前の照合失敗 |
| 124 | `timeout` | 期限切れ。同nonce停止後の回収は確認済み |
| 125 | `cleanup_unconfirmed` | owner/通常画面の復帰を期限内に確認できない |

case待機は既定130秒、`--timeout`で1〜180秒。起動要求の送信後に失敗・取消・期限切れとなり、
回収が未確認なら同nonceの固定stopを一回送り、別に45秒まで観測する。
各ADB要求にも5〜10秒の上限があり、実経過時間にはその最終要求と開始/終了の照合時間が加わる。
起動コマンド自体がtimeoutしても配達済みの可能性があるため、停止観測を行う。
期限切れ後の遅い`pass`を元の実行のPASSへ変更しない。

stop対象はSDKで`linux-shm-sdk-stop`、virtual/recoverで`linux-shm-probe-stop`。
回収未確認の場合は同じnonceを保って既存固定stopを明示的に再送できるが、
このscript自身はstart/recoveryを自動再送せず、journalやremote processを削除しない。
端末接続を失った場合の回収は保証しない。

## 証拠と検証

実行ごとのdirectoryは0700、ファイルは0600で作る。`metadata.json`はallowlist後の最新値、
`result.json`は判定・source/version/UID/PID・回収状態、`commands.log`は固定ADB要求でserialを伏せる。
snapshot全体、profile/pin/鍵本文、環境変数全体、logcat、室内画像は保存しない。
HMDと物理入力は常に`not_verified`であり、この自動検査から受入れを推測しない。

端末なしのfixture 16件が成功した。virtual/SDK/recovery成功、SDK復帰不足、not_requiredと偽PASS、
UID/version/PID/source/型の不一致、secret field除外、失敗と設定変更、timeout後の同nonce停止、
遅い成功の非昇格、回収不能、起動要求の配達不明、古いnonce拒否を検証する。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/tests -p test_linux_shm_operator.py -v
```

この追加はoperatorとfixtureのみ。APK、Linux bundle、Quest設定は変更しない。
実端末の一連の再現はroot担当の後続gateとして記録する。

## Build169での公開CLI実機確認

private手動harnessだけでなく、このCLIの `virtual` と `sdk` を実Questで各一度確認した。
正規stagingのprofile/pinsは変更せず、既知host key/endpointの隔離sshdを同じ設定で起動した。

- virtual nonce `6cea0e97d6189807c3be0c766e1f232f`: exit0/status pass、native復帰とcleanupを確認。
- sdk nonce `58c86ddcc7828fee1238299856e0aef8`: exit0/status pass、nativeに加えて同scene SDK診断draw、lease解放まで確認。

元profile/pinsとの照合と専用sshd handleの回収も成功した。
CLIのsource/UID/version/PID、scope別終端判定が実metadataで動作した結果で、
任意Linux GUIやHMD視認の成功へ広げない。
