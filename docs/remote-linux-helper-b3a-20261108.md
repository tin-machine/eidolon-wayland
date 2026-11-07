# NX18 B3a: Linux専用helperと固定SHM転送

B2のAPK UIDからの固定SSH probeとは別に、Linux側のhelperと、隔離したhost SSH経路でのSHM転送を実装した。Termux helper、そのhash、既存のTermux設定、通常transportのcapabilityは変更しない。今回のLinux起動対象は固定100フレームのSHM fixtureだけであり、foot・catalog・任意GUIは拒否する。

## 配置と起動契約

Linux版は `scripts/linux/eidolon-linux-session.py`。通常の起動は固定 `/usr/bin/python3 -I -S` を使い、引数は `prepare <16桁hex nonce>` または `cleanup <同nonce>` だけ。host/path/任意argvをhelper制御JSONへ渡さない。

- `/usr/bin/python3` と解決先および祖先はroot所有かつgroup/world書込み不可とする。Pythonのisolated/no-site flagを検査する。system binaryをユーザー所有であることの検査と混同しない。
- HOMEは環境変数ではなく、実UIDと一致する `pwd.getpwuid()` のaccount HOMEから取得する。root実行、UIDとeffective UIDの不一致、symlink HOME、他者が書けるHOMEは拒否する。root所有のsystem HOMEへの自動fallbackはしない。
- bundleは固定 `$HOME/.local/lib/eidolon-linux/`、0700。`bundle.json` とPython sourceは0600、regular file・same UID・link数1・nofollow。binaryは同UID・owner executable・group/world書込み不可。祖先directoryも検査する。
- bundleはhelper source、固定Waypipe 0.11.3、`eidolon-shm-fixture` の3ファイルだけ。manifestは16KiB以内・重複key拒否・厳密な型とschemaを用い、全ファイルのSHA-256を照合する。inspect後、起動直前にもhashを再確認する。Ubuntuの別Waypipeへ自動fallbackしない。
- 子環境はPATH、HOME、locale、private XDG_RUNTIME_DIR/TMPDIR、SHELLだけを新規構築する。Termux path、run-as、既存desktop DISPLAY/DBUS、SSH agent、LD/PYTHON環境は使わない。ユーザーのshell設定を書き換えない。

manifestの形は次のとおり。hashは事前に確認した配布物から生成し、helperの自己申告値だけを信頼しない。後続のAPK adapterも期待hashを保持して照合する必要がある。

```json
{
  "schema": 1,
  "backend": "linux_posix",
  "revision": "eidolon-linux-shm-v1",
  "files": {
    "eidolon-linux-session.py": "<SHA-256>",
    "waypipe": "<SHA-256>",
    "eidolon-shm-fixture": "<SHA-256>"
  }
}
```

## session、転送、回収

sessionは `$HOME/.eidolon-linux/s-<nonce>/`、0700。owner/leaseは0600で、backend・revision・nonce・UID・PID/starttime/process groupを束縛する。Unix socket長上限を起動前に確認する。既存sessionやreceiptと同じnonceは再利用しない。最大8 session、root内80 entry、完了receipt64件に制限する。

制御行は最大16KiB、出力待ち32KiB、子stdout/stderr保持は各64KiB。正規の操作はheartbeat、inspect_tools、startのpatternモード、stopだけ。inspectは固定 `--version` を3秒・4KiBに制限し、Waypipe versionとfeature表示を固定bundleに合わせて確認する。startには同UIDのStreamLocal socketが先に必要であり、既存display socketは拒否する。

起動するcommandは次の固定構造である。`<bundle>` と `<session>` は検査済み固定配置から内部で組み立てる。

```text
<bundle>/waypipe --no-gpu --compress none --threads 1 \
  --socket <session>/p --display <session>/w server -- \
  <bundle>/eidolon-shm-fixture --pattern
```

leaseはheartbeatから60秒、session全体は最大120秒。EOF、stop、signal、期限切れ、失敗時は自分がspawnしたprocess groupだけをTERM/KILLし、子をwaitしてpipeとsocketを回収する。group leaderを [waitidのWNOWAIT](https://docs.python.org/3/library/os.html#os.waitid) で保持している間に回収し、PID再利用後のgroupへsignalしない。後日cleanupは保存されたPIDへsignalせず、回収を証明できないsessionをpendingとして残す。未知entry、UID不一致、symlinkを黙って削除しない。

出力するのは固定reason、UID、長さ・件数、終了/回収bool、固定fixtureの検証済みcounterだけ。任意の子stdout/stderr本文は公開しない。`pattern_verified` は子の報告の厳密検査であり、受信側のpixel hash検査と別の条件とする。

## hostでの再現

実行には既存の固定Waypipe host build、system OpenSSH client/server、root所有のPython、既存Cargo cacheを用いる。新しいtarget cacheを複製しない。`--output` は未使用のprivate作業directoryを指定する。

```sh
CARGO_INCREMENTAL=0 CARGO_TARGET_DIR="$EIDOLON_TARGET_DIR" cargo build --locked \
  -p eidolon-core --example linux_shm_server \
  -p eidolon-shm-fixture --bin eidolon-shm-fixture
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -W error scripts/tests/test_linux_helper.py
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 scripts/check-linux-helper-shm \
  --target-dir "$EIDOLON_TARGET_DIR" --output "$EIDOLON_CHECK_DIR"
```

`check-linux-helper-shm` は一時HOME・新規key pair・loopback専用の独立sshd・known_hosts・SSH ControlMasterを自分で所有する。system sshdや既存SSH設定を変更しない。host fixtureだけはhelperの内部test injectionで一時HOMEを渡す。通常CLIのaccount HOME取得はpure/process testで検査し、本wireの一時HOMEを実account HOMEでの導入成功と呼ばない。

実経路は SSH認証 → remote StreamLocal forward → Linux helper → Waypipe server → Waypipe client → 実 `ProtocolState` の順。compositor fixtureは100種類の受信pixel SHA-256を固定期待値と照合し、frame callbackを返す。frame clockはvirtualであり、GPU描画やHMD表示は検証していない。

OpenSSHの [AllowStreamLocalForwarding](https://man.openbsd.org/sshd_config#AllowStreamLocalForwarding) はUnix socket転送を制御する。本環境のOpenSSH 10.2p1では `AllowTcpForwarding no` と組み合わせるとremote Unix転送が拒否された。固定版の [serverloop.c](https://github.com/openssh/openssh-portable/blob/V_10_2_P1/serverloop.c) と [channels.c](https://github.com/openssh/openssh-portable/blob/V_10_2_P1/channels.c) のremote転送permission経路も確認した。隔離fixtureに限り `AllowTcpForwarding remote` と `PermitListen 127.0.0.1:1` を指定し、実際にはTCP転送を要求せずUnix転送だけを使う。これはこの版と検査設定での観測であり、既存sshd全般の設定変更を要求するものではない。

## 検証結果と試行錯誤

2026-11-08、Linux host上で以下を確認した。

- Pythonのpure/process test 10件PASS。bundle hash/権限/symlink、HOMEと環境、nonce/UID/starttime、固定argv、EOF、lease期限、全体期限、TERMを無視する子と孫の回収、無関係な自分のfixture processの存続、任意起動/重複key拒否を含む。
- actual SSH wire PASS。100フレーム、100 copy、100 callback、100種類の受信pixel hashを照合。子のconfigures/acksは各1、releasesは100。
- 終了時にclient/object/pixel bytes/pool bytesは全て0。forward取消、全spawn ownerのwait、session directory消失、回収receiptを確認した。
- 対象exampleのclippy `-D warnings`、rustfmt、diff checkはPASS。固定vendor Smithayに既存warningがあるが、今回の変更対象には新しいwarningを残していない。
- helper/source/binaryのhash、基準revision、結果をprivate artifactに記録した。公開文書にはendpoint、鍵、個人HOME、室内画像を含めない。

初回はcompositor socket作成前にWaypipeを起動し接続に失敗したため、socketとcompositor生存をdeadline内で待つよう修正した。次にSSH認証は成功しても前述のremote転送permissionで失敗し、隔離sshdの設定を修正した。その後、Waypipeの初期能力確認と本アプリが別々のWayland connectionを使うことが分かり、fixtureの「物理connectionは1本だけ」という誤った仮定を修正した。物理connectionは最大4、本アプリwindowは最大1とし、観測は2 connectionだった。これらの失敗を描画成功として扱わず、回収結果も分けて保存した。

このcheckpointはLinux helperとhost wireの成立まで。APKからのhelper接続、実account HOMEへのbundle導入、Android側のforward owner、実GPU draw、通常Linux GUI、物理HMDの受入れは次段階。B2の `ProbeReady` を `GUIReady` へ昇格させず、固定probeのcapabilityとsession/forward/process ownerを分けて接続する。
