# C2 Waypipe起動prefix共通化：小さいLLM向け詳細設計

状態: proposed。製品コードの実装前にレビューする詳細設計。基準はPR #34/#36がmerge済みの
`c890f50fbb0d63aeb6de6171e2eb192269285ea0`。この文書PRは実装、実GUI、Quest受入の完了を意味しない。

## 1. 調査結果と選定理由

PR #36のCI行番号fixture修正後、merge済みsourceにCPython **3.14.4 final/0**で
`repository-deep` を1回実施し、同じsource/parserで `verify` と一つの `targeted-deep` を実行した。
生成物はCodexのローカルtask directoryに保持し、Gitへ含めない。

| 観測 | 結果 |
| --- | --- |
| Git追跡file / source候補 | 997 / 703 |
| Python parse成功 / 他言語等unsupported | 263 / 440 |
| 関数 / CFG・DFG対応 / 未対応 | 3715 / 1443 / 2272 |
| cross-module call候補 | 1507。runtime dispatchの証明ではない |
| repository artifact SHA-256 | `21ae1fc57e1f2ff807abe5080eb6b1e1788615ad39fea125eb56e41ae1703d4c` |
| generator SHA-256 | `6e0abc3425c8742d8d4e26f8f250d45bc8ff12179f098ebe6d492c9964798473` |
| profile SHA-256 | `2f0978765b257b71aac4ac138a99f08cb804cdbc78e64eca69eb2c98e328ce68` |

seedは `scripts/linux/eidolon_linux_desktop_v3.py::launch_mousepad@471`、hops=1、
max_nodes=80、max_edges=160、max_bytes=65536。sliceのcomplete=true、omitted nodes/edges=0、
outside_hop_edges=86。completeはこの近傍の省略なしを意味する。
`Try`、暗黙例外、heap状態、call効果などはunknownであり、起動関数2つのCFGはunsupported。
source読み合わせにより次の具体的な重複を確認した。

| 基準symbol | sourceで確認した責務 |
| --- | --- |
| `eidolon_linux_desktop_v3.py::launch_mousepad@471` | socketのlstat/type/UID検査、Waypipe prefix、固定Mousepad argv、Documents cwd、追跡spawn |
| `eidolon_linux_desktop_session_v3.py::_start_demo@153` | 同じsocket検査・prefix、検証済みdemo argv、nonce由来run-id、追跡spawn |
| `eidolon_linux_desktop_v3.py::spawn_tracked@410` | owner.spawn → profile.track_process → drain.add → 同じchildを返す |
| `eidolon_linux_desktop_v3.py::cleanup_owner@484` | owner完了 → pipes EOF → profile.release。所有・回収の順序 |

最初の2つの共通部分をAccidental Complexityの候補とする。単なる関数長・fan-outによる選定ではない。
ownerの追跡と回収順序はEssential、例外時の全所有境界とruntime dispatchはUnknownとして残す。
`_validate` 等の大きな関数も自動候補に上がったが、今回の分割対象へ広げない。

参照契約: [C2](linux-real-app-c2-helper-20261110.md#inspect--start)、
[統合計画](linux-real-app-v3-integration-plan-20261111.md)、
[Deepの意味と制限](source-structure-deep-analysis.md)、
[過去の探索評価](source-structure-deep-import-evaluation-20261115.md)。

## 2. この実装単位の変更範囲

| file | 許可する変更 |
| --- | --- |
| `scripts/linux/eidolon_linux_desktop_v3.py` | `launch_mousepad`の直前へ下記helperを追加し、同関数を呼び替え |
| `scripts/linux/eidolon_linux_desktop_session_v3.py` | `_start_demo`内の同一検査/prefix部分をhelper呼出に置換 |
| `scripts/tests/test_linux_desktop_v3_launch.py`（新規） | 下記characterizationとhelperの回帰 |
| `scripts/linux-desktop-v3-sources.json` | 製品変更commit確定後、別commitでintegration.c2_commitだけ更新 |
| この設計文書 | 実施commit、実測結果、環境不足を追記 |

新module、framework、依存package、公開wire fieldは追加しない。B1 vector、v2 frozen source、
settings probe/HOME、owner、journal/finalizer、deadline/lease、SDK/JNI、保存処理を変更しない。
socketの検査とspawn間には既存のpath-basedな時間差がある。この共通化をTOCTOU対策とは呼ばない。
安全条件を強化する提案が出た場合は、この動作維持の実装単位から切り離す。

## 3. 追加helperの完全な入出力

関数名は `waypipe_server_prefix(paths)`。既存core module内の通常関数とし、型やclassは新設しない。
入力は既存のpaths object。`wayland_socket`、`home`、`runtime` は既存Path値をそのまま使う。
owner/profile/drain/env、任意app argv、UI/wire入力、callbackを引数へ追加しない。

処理は次の順で固定する。

1. `paths.wayland_socket.lstat()` を一度呼ぶ。
2. `FileNotFoundError`だけを捕捉し、既存と同じ
   `raise HelperError("APP_STATE_INVALID", "start") from None` とする。
3. `stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()` を既存順で評価し、
   `require(..., "APP_STATE_INVALID", "start")` を呼ぶ。
4. 検査後に `paths.home / ".local/lib/eidolon-linux/waypipe"` を求める。
5. 次の順の**新しいlist[str]**を返す。

```python
[
    str(waypipe),
    "--no-gpu", "--compress", "none", "--threads", "1",
    "--socket", str(paths.wayland_socket),
    "--display", str(paths.runtime / "w"),
    "server", "--",
]
```

実装では固定flagの部分に既存 `*FIXED_WAYPIPE_ARGS` を使う。定数の内容・tuple型は変更しない。
戻りlistをcache/共有しない。検査前にapp側値の取得を行わず、helperからspawnしない。
`PermissionError`等の他OSErrorは従来どおり伝播させる。resolve/statへの置換、retry、
`exists()`による事前検査、symlink追跡、全Exception捕捉、任意path補正は追加しない。

## 4. callerごとの置換内容

### Mousepad

`launch_mousepad(owner, profile, drain, env, paths)` のsignatureを維持する。
`command = waypipe_server_prefix(paths) + list(FIXED_APP)` とし、以下を維持する。

```python
return spawn_tracked(owner, profile, drain, command, env,
                     cwd=str(profile.paths.documents))
```

app suffixは `/usr/bin/mousepad --disable-server` の2要素のみ。
Documents cwdの取得はprefix検査成功後。childの返却identity、envのobject identityも維持する。

### GTK demo

`_start_demo(owner, profile, drain, env, paths, verified_bundle, context)` のsignatureを維持する。
最初に `command = core.waypipe_server_prefix(paths)` を呼び、その後に次を連結する。

```python
command += [str(verified_bundle.directory / "eidolon-gtk3-desktop"),
            "--case", "input", "--run-id", context.record()["nonce"] * 2]
return core.spawn_tracked(owner, profile, drain, command, env)
```

demoではcwd keywordを追加しない。nonceの重複方式、context.recordの評価時点を変えない。
このhelper追加を理由に `spawn_tracked` へ汎用launch APIや任意argv入口を追加しない。
importは既存 `core` を使う。stat/osのimport削除は各fileの他用途をrgで確認した場合だけ行い、
不要なimport整理を同時に進めない。

## 5. テスト設計：先に動作を固定する

新規test fileは既存C2 testと同じscripts/scripts-linuxのsys.path設定でcoreとsessionを読み込む。
top-levelでmainを呼ばないことを確認する。ProcessOwnerや実アプリを起動せず、
`core.spawn_tracked` をpatchして引数を観測する。source全文・行番号のgolden比較にはしない。
テストの期待argvは上記のliteralで組み立て、helperを使って期待値を生成しない。

| ID | fixture / 操作 | 必須の確認 |
| --- | --- | --- |
| T01 | Mousepad、socket型/current UIDのlstat結果 | argv全要素と順番、env/owner/profile/drain identity、Documents cwd、spawn1回、返却child identity |
| T02 | demo、同じ正常socket | prefixとdemo suffix全要素、nonce×2、cwd keywordなし、spawn1回、返却identity |
| T03 | 両caller、lstatがFileNotFoundError | HelperError.public_reason=APP_STATE_INVALID / failure_reason=start、spawn0回、context.record未呼出 |
| T04 | 両caller、regular file・directory・symlink・FIFOをsubTest | 同reason、spawn0回。symlinkはlstatのmodeで拒否 |
| T05 | 両caller、socket型で異なるUID | 同reason、spawn0回 |
| T06 | 両caller、lstatがPermissionError | 例外identityを維持して伝播、spawn0回 |
| T07 | prefixを2回取得し一方へsuffix追加 | listが別identity、もう一方とFIXED_WAYPIPE_ARGSに変化なし、各lstat1回 |
| T08 | socket検査失敗、documents/context取得に失敗するsentinelを置く | socket由来HelperErrorを先に観測、後続値を取得しない |
| T09 | 両caller、spawn_trackedが例外sentinelを送出 | 同じ例外が伝播、retryせず1回だけ呼出 |
| T10 | prefix helper単体 | flags/server/--/socket/display順、絶対appを含まないこと、lstat1回 |

T01–T06/T08/T09は元の2関数にも適用できるcharacterizationとして先に実行し、抽出後も維持する。
T07/T10はhelper追加時に加える。`SimpleNamespace`とmock Pathのlstat等で作れるfixtureを使い、
実他UID account、socket権限変更、Docker/rootをunitの前提にしない。
実kernel/SSHの確認は既存C2/CIで別に行う。mock成功を実spawn成功と報告しない。

## 6. 小さいLLM向け作業順序とcommit境界

1. 実装開始時のmain SHA、PR36 merge状態、上記2関数/定数/既存テストを再確認する。
   sourceが変わっていれば、symbolの行番号を固定せずpath/nameから現定義を読む。
2. T01–T06/T08/T09を追加。元実装で成功することを確認する。
3. coreへhelperを追加しMousepadだけ置換。T01/T03–T10を実行する。
4. demoを置換し、新規test全件と既存Deep/import testsを実行する。
5. `spawn_tracked`、cleanup_owner、wire、FIXED_APP、FIXED_WAYPIPE_ARGSに意図しない差分がないかreviewする。
6. 製品source変更と回帰をcommit Aへ確定する。まだinventoryへAのSHAを書かない。
7. 実際の `git rev-parse HEAD` が返すAの40桁SHAを確認し、別commit Bで
   `integration.c2_commit` に設定する。c1_commit、sources/generated集合を変更しない。
8. Bのclean sourceからbundle/binaryを新規生成して最終検証する。旧bundle/receiptを流用しない。
9. 実装PRにA/B、全検証の版と結果、skip/環境不足、未実施のGUI/Questを記録する。

自己commitのSHAを同じcommitへ埋め込まない。
commit A/Bは同じ実装PRでレビューし、Bまで揃った最終headを検証・mergeする。A単独でのmergeや配備はしない。
source closureは既存15fileのまま。Rustのinclude_bytes/current_hashesが現在bytesを読む既存契約を維持し、
固定hashを手書きで補完しない。compilerやbundle mismatchを無効化してテストを通さない。

## 7. 検証コマンドと完了条件

repository rootで実行する。Pythonは実装環境の完全版を記録する。

```sh
python3 -m unittest discover -s scripts/tests -p 'test_linux_desktop_v3_launch.py' -v
python3 -m unittest discover -s scripts/tests -p 'test_linux_desktop_v3_c2.py' -v
python3 -m unittest discover -s scripts/tests -p 'test_linux_desktop_v3_bundle.py' -v
python3 -m unittest discover -s scripts/tests -p 'test_linux_desktop_v3_bootstrap.py' -v
python3 -m unittest discover -s scripts/tests -p '*deep*.py' -v
python3 scripts/docs.py validate
python3 scripts/check-design
git diff --check
```

必要なkernel/権限がない環境では、その制限を記録して既存CIへ回す。期限延長やskip追加で補わない。
CIは既存host/Android、GIO、A0 foundation、N01、CodeQLを確認する。
N01はInspect-onlyであり、Mousepad/demoのLaunch回帰の代替にならない。
既存C2のStart/Stop/Recover・EOF・receipt・v2互換検査も成功することを要求する。

完了条件はT01–T10、既存検査、source pinのA/B整合、実装PRの全必要CIが成立した状態。
実GUI/Questの成功は実施した別証拠がある場合だけ記載する。

## 8. 実装者へ渡す指示

「この文書の基準と現在sourceを照合し、§6の順で進める。変更は§2だけに限定し、
§3のhelperと§4の2callerを実装する。最初にcharacterizationを通し、各段階でtest結果を記録する。
socket拒否reason、argv順、cwd差、評価順、追跡spawnとcleanupの責務を保持する。
未対応環境や新しい差分があれば観測事実を報告する。別のリファクタリングを同時に行わない。
commit A/Bのsource pinを整合し、既存CIの成功を確認できたら実装PRをレビューへ出す。」

この文書PR自体では製品コードを変更していない。改善効果の時間/token比較も未測定である。
