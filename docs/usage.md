# Questからの起動・接続設定

Build 125からライブラリのアプリ名は **Eidolon**。以前のAPKでは **Eidolon Phase 0** と表示する。
Questのアプリライブラリの「提供元不明 / Unknown Sources」から起動する。
通常利用はQuest内のAPK→Termux（127.0.0.1:8022）で完結し、PCやWi-Fi ADBは不要。
初回のAPK/Termux導入と、配布APKの署名は別途必要。

## 再起動後（既に設定済みの端末）

Build134以降は、下記のTermux連携を一度設定すれば、Eidolonの操作画面または左下の
「ターミナル」ボタンから開ける。既にSSHが起動していればそのまま接続し、接続拒否の場合だけ
固定helperを一度呼ぶ。起動待ちは最大15秒。認証失敗・ホスト鍵不一致ではサービス起動へ進まない。

1. ライブラリからEidolonを開く。境界・追跡の案内があれば解消する。
2. 固定上部の「ターミナルを開く」、または左下のピンク色の「ターミナル」を押す。
   接続中の連打はまとめる。さらに開くとfootが増え、現在の同時表示は二枚。
3. 「窓 N へ入力」で対象を選ぶ。上部には配置編集中・キーボード未検出・未選択等の理由と入力先を表示する。
4. 接続設定・テーマ・配置・パススルー等は「設定と詳細」を開く。終了は「接続を閉じる」。

配置編集の終了には「配置を確定して入力へ戻る」を使う。対象が表示中なら、入力リセットの完了後に
同じ窓へ入力を戻す。対象が終了・再生成された場合や二枚のどちらを選ぶか不明な場合は、窓を明示選択する。
キーボード抜差し、システム画面・sleepからの復帰後は、入力先を選び直すことがある。

Build142では入力を主操作・ランチャーの両パネルから受け取り、選択したterminalへ送る。
左下のランチャー中心は床上約1.05m。Cyan Orbitは線幅30・平らな端で、中央はRSSに応じて回転する。
CPUが低負荷なら外側は停止する。内側のDisk I/Oはまだ未計測なので停止する。

電池/充電・RAM空き/総量・Quest自身のIPは通常APK権限で取得する。
RAMはAndroidの`availMem`でありLinuxの`MemFree`とは異なる。IPは既定ネットワークのローカルアドレスで、
ルーターや外部サーバーのIPではない。接続先は別表示の`このQuestのTermux`（127.0.0.1:8022）。
値が取れないときは「不明」と表示する。可視期間に5秒間隔で更新し、非表示時は購読を止める。

US/JISはキー配列だけを変更する。かな漢字変換は専用の`eidolon-japanese`入口を使う
[最小probe](extensions-nx01-nx08-20261107.md)であり、通常のfoot全体にIMEを自動適用するものではない。

### Termux連携の初回設定

既存の`eidolon-sshd`がある端末では次をTermuxで実行する。

```sh
install -m 700 scripts/termux/eidolon-start "$PREFIX/bin/eidolon-start"
mkdir -p "$HOME/.termux"
# termux.propertiesに以下の設定を一度だけ追加・編集する。
# allow-external-apps=true
termux-reload-settings
```

Eidolonの「設定と詳細」→「Termux連携を許可」でAndroidの権限を許可する。
この連携には[Termux公式RUN_COMMAND](https://github.com/termux/termux-app/wiki/RUN_COMMAND-Intent)の
実行権限と`allow-external-apps=true`の両方が必要。製品が送る実行file/引数は固定である。
開発用ADBからの同等導入は`python3 scripts/quest-setup-extensions --serial SERIAL --grant-run-command`。
変更前の設定fileはTermux内のprivate backupへ保存される。

`eidolon-sshd`サービスをまだ作っていない初回環境では、Eidolonを切断した状態で
`bash scripts/termux-bootstrap.sh 'ssh-ed25519 AAAA…'`へ許可するクライアントの公開鍵を渡す。
既存鍵は保持し、loopback:8022専用のserviceを構成する。通常の更新のたびにbootstrapは実行しない。

権限拒否、helper未導入、設定不足、起動制限では、理由と「Termuxを開く」を表示する。
必要ならTermux側で次を実行してからEidolonに戻り、再試行する。

```sh
. "$PREFIX/etc/profile.d/start-services.sh"
sv up eidolon-sshd
sv status eidolon-sshd
```


再起動後はADBの待受・IPも変わり得る。開発用Wi-Fi ADBはUSBで対象を照合して
`adb -s USB_SERIAL tcpip 5555`、その時点のIPへ`adb connect IP:5555`を行う。
USBを充電器へ替えても、製品のlocalhost SSHはWi-Fi ADBに依存しない。

## 初回のTermux準備（ADBなし）

APKと**同じbuild**の`eidolon-termux-tools.zip`をTermux内の作業directoryへ展開する。
source checkoutならrepositoryのrootへ移動する。既存環境の更新時は先にEidolonで切断する。
次はTermux自身のシェルで実行する。PCのシェルへ貼り付けない。

```bash
pkg install openssh python unzip x11-repo
pkg update
apt-get install waypipe=0.11.3 foot=1.28.0 fontconfig fontconfig-utils ttf-dejavu
ssh-keygen -A

# 初回だけ。既存のeidolon-sshdサービスがある場合は上のサービス起動手順を使う。
sshd -p 8022 -o ListenAddress=127.0.0.1 \
  -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \
  -o AllowStreamLocalForwarding=yes

install -d -m 700 "$HOME/.local/lib/eidolon"
install -m 700 scripts/termux/eidolon-session.py "$HOME/.local/lib/eidolon/eidolon-session.py"
waypipe --version
foot --version
```

固定版packageがrepositoryに無い場合はこの手順を止め、[toolsの版契約](termux-wayland-tools.md)を再確認する。
違うWaypipe版やfeaturesを黙って使用しない。launcherはAPK同梱のSHA256と一致しなければ起動を拒否する。
通常のfoot利用には開発用SHM fixtureの導入は不要。

## 公開鍵をペアリングする

1. Eidolonの「設定と詳細」→「接続設定・初回ペアリング」を開き、「APK公開鍵をコピー」を押す。
2. Termuxへ戻り、展開先で次を実行する。`'…'`全体をコピーした公開鍵で置換する。

   ```bash
   python3 scripts/termux/eidolon-pair 'ssh-ed25519 AAAA…'
   ```

   helperは既存`authorized_keys`の内容を保持して追記し、同じ鍵の再実行では増やさない。
   出力は`username`、`host_key`、`host_fingerprint`とAPK公開鍵の指紋だけ。秘密鍵は読まない。
3. Eidolonへ戻り`username`と`host_key`を入力する。画面の`SHA256:…`がTermuxの
   `host_fingerprint`と同じことを確認し、確認欄にチェックして保存する。
4. 「ターミナルを開く」で接続する。二枚→切断→アプリ再起動→再接続を確認する。

接続中・取消処理中・前回のremote回収が未確認の間は保存不可。
保存前に旧runtime/SDK所有物の停止完了を待ち、停止失敗ならpinを書き換えない。
古い設定画面からの上書きも拒否する。秘密鍵はAndroid Keystoreで保護した暗号文として保存し、UI/clipboardへ渡さない。

## 接続できない場合

| 表示・状態 | 対応 |
| --- | --- |
| 初回の接続設定が必要 | 公開鍵の登録とhost fingerprintの照合を行う |
| ホスト鍵が保存済みの鍵と違う | Termux側の鍵を確認する。自動上書きしない。切断・回収完了後に接続設定で明示変更 |
| 公開鍵認証に失敗 | helperでAPK公開鍵が登録済みか確認。APK再インストールで専用鍵が変わった場合は再ペアリング |
| Termuxでsshdを起動 | Termuxを起動してサービス状態を確認後、ターミナルを開き直す |
| 前回の終了を確認できません | Termux復旧後に再接続し、所有nonceの回収確認を待つ。無関係なprocessをkillしない |
| 停止を確認できません | 未回収ownerを診断へ保存する。新runtimeを繰り返し作らず、アプリを再起動して確認 |
| 画面が背景だけ、境界案内が前面 | HMDで追跡・境界設定を確認する。ADB到達だけではSDK表示可能とは限らない |

## ビルドと配布用成果物

```bash
scripts/build-android 128
scripts/build-release 128
```

debug/releaseそれぞれassemble・lint・JUnit・native/manifest/依存同梱検査を行い、作業rootへAPK、hash、build情報、ログを保存する。
releaseは`eidolon-release-unsigned.apk`と`eidolon-termux-tools.zip`を出力する。
debugの検査入口・fixtureはreleaseへ含めない。camera権限は要求しない。
releaseの署名鍵はrepository外で管理し、署名後の同じAPKをアップデートに使う。debug署名のAPKへ違う署名のreleaseを上書きできないため、
既存debug環境を消してreleaseへ切り替える操作は自動化していない。

```bash
# パスワードは引数や環境変数へ書かず、apksignerの対話で入力する。
"$ANDROID_HOME/build-tools/35.0.0/apksigner" sign \
  --ks /path/outside/repository/release.keystore \
  --out eidolon-release.apk eidolon-release-unsigned.apk
"$ANDROID_HOME/build-tools/35.0.0/apksigner" verify --verbose eidolon-release.apk
```

依存source・patch・licenseは[THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md)、
`native/deps.lock.json`、`Cargo.lock`とAPK内のnoticesに対応する。ストア公開は対象外。
releaseの実機・HMD確認はdebugの自動検査とは別に行う。

## 停止と長時間動作の検査

```bash
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case stop --timeout 1200

scripts/quest-observe --serial "$EIDOLON_QUEST_SERIAL" --scenario power-lifecycle \
  --condition '壁充電器・使用ケーブル・一枚foot静止' --duration 1800
scripts/quest-observe --serial "$EIDOLON_QUEST_SERIAL" --scenario soak \
  --condition '給電条件・二枚・更新条件' --duration 7200
```

`stop`はdebugの通常SDKで実footを開き、切断・再接続・runtime停止を20回行う。
保存済み設定を隔離して検査し、通常UIへの復帰と設定hashを照合する。HMDの視認・物理入力の検査ではない。
`quest-observe`はdebug APKの診断、battery/温度、PSS、FD、CPU、queue、frame/swap counterを60秒ごとに採取する。
表示や接続を開始せず、sleep/Guardianも変更しない。画面画像、入力文字、clipboard、process command lineは記録しない。
欠測・ADB断・process再起動・更新されない診断を残す。`collected`は採取完了であり、L03/L04合格を意味しない。
Menu/sleep/reboot、100回再接続、30Hz fixture、入力遅延、HMDの確認は別の受入項目として残る。
