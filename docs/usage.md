# Questからの起動・接続設定

Build 125からライブラリのアプリ名は **Eidolon**。以前のAPKでは **Eidolon Phase 0** と表示する。
Questのアプリライブラリの「提供元不明 / Unknown Sources」から起動する。
通常利用はQuest内のAPK→Termux（127.0.0.1:8022）で完結し、PCやWi-Fi ADBは不要。
初回のAPK/Termux導入と、配布APKの署名は別途必要。

## 再起動後（既に設定済みの端末）

1. Termuxを起動する。既存の`eidolon-sshd`サービスを設定した環境では次を実行する。

   ```bash
   . "$PREFIX/etc/profile.d/start-services.sh"
   sv up eidolon-sshd
   sv status eidolon-sshd
   ```

   `run:`を確認する。Quest再起動でTermux/sshdも停止する。アプリの自動起動は未対応。
   サービスを導入していない新規環境は、下の初回準備でsshdを起動する。
2. ライブラリからEidolonを開く。境界・追跡の案内があれば解消する。
3. 「ターミナルを開く」を押す。さらに押すとfootを追加できる。同時表示は二枚。
   接続処理中の重複押下はまとめて処理する。
4. ウィンドウを選択して入力する。US配列は既定。「現実を背景に」でパススルーへ戻せる。
   終了は「SSH切断」。再接続時にshellの作業内容は引き継がれない。

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

1. Eidolonの「接続設定・初回ペアリング」を開き、「APK公開鍵をコピー」を押す。
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
