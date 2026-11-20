# 公開EGL APIによるCPU screen初期化の分離

SDLの固定検証は、libc++の整合後も `eglInitialize` の段階で失敗した。Mesa26.2.6 の既知ログは `dri2_screen_failed` で、固定 softpipe 対照も同じ結果だった。ここでは Wayland・SDL を使わず、同じ Mesa の CPU screen 生成経路を一回診断する。描画、SDK、入力、HMD の合格にはしない。

## 実機の診断結果

Build161でprobe `56c51628a7948f75dfa68e32b9da3cf9`を実行した。
surfaceless拡張あり、core entrypointでdisplay取得成功だったが、初期化はfalse、
直後のerrorは`EGL_NOT_INITIALIZED`（12289）。続くterminateはtrue/`EGL_SUCCESS`であり、
初回失敗を上書きしていない。known分類は`dri2_screen_failed`1件、未知2行、計176byte。
childはcode1で自然終了し、reap/group回収/pipe drainとruntime不変を確認した。
operatorの`diagnosed`/終了0はこの失敗を最後まで観測できた意味で、描画成功ではない。

Wayland/SDLを使わなくても同じscreen生成失敗が起きたため、Wayland serverだけを原因とは考えにくい。
次は共通Mesa runtime/DRI config生成を調べる。固定tokenの読取りで実libgalliumに
softpipe/llvmpipe/swrastの文字列があることは確認したが、driverの完全な動作証明にはしない。
同条件の再試行で成功を待たず、次の識別情報を追加してから検証する。

## 根拠

[Mesa26.2.6 dri_util.c](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.6/src/gallium/frontends/dri/dri_util.c) の `driCreateNewScreen3` は、構造体確保、`drisw_init_screen` の pipe_screen、DRI config の生成で NULL を返し得る。SWRAST loader の最低版は1なので、Wayland の版2は対象内。

[surfaceless 実装](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.6/src/egl/drivers/dri2/platform_surfaceless.c) は、software 時に DRM なしの swrast 経路へ fallback でき、同じ `dri2_create_screen` を使う。[Khronos の拡張仕様](https://registry.khronos.org/EGL/extensions/MESA/EGL_MESA_platform_surfaceless.txt) は native window system に依存しない platform を定義する。私設 DRI ABI や ABI shim は使わない。

## 一回実行

実機 operator が承認済み Quest 3 を指定する。APKの起動・停止や現行foot/leaseへの操作は不要。C、Mesa、APK の再build、package追加も不要。

```sh
scripts/quest-probe-egl-surfaceless \
  --serial "$EIDOLON_QUEST_SERIAL" \
  --artifact-root "$EIDOLON_TASK_ROOT/egl-surfaceless"
```

CLIが受け付けるのはADB対象とprivate artifact保存先のみ。PID、library path、exec argv、任意env、待機時間は指定できない。ADB bootstrap は zlib/base64 で圧縮し、command全体を49KiB以下に制限する。

Termux UID は10158、runtimeは次の既知SHAを固定する。更新版への自動追随はせず、変化したら停止して再調査する。

| 対象 | SHA-256 |
| --- | --- |
| libEGL.so.1.1.0 | `88ccad71e437eadaead1729d43ff8bd0fed9a5a70956d0dcd98532f0558ee6e8` |
| libEGL_mesa.so | `4c07cd55d4d59c4db60035abb2cb517776f50829cac5486de2f5c18ccf72f228` |
| libc++_shared.so（30） | `05bef9d3901910c3ff8ab450b03bc8b28383d363a2290f2f7b9aed7331ea6ff7` |

同UID、非共有書込み、regular file、固定alias、hash、size、読取り前後のidentityを照合する。Mesaの固定vendor JSONだけを選ぶため `__EGL_VENDOR_LIBRARY_FILENAMES` を内部で固定し、JSONの所有・内容も検査する。これはユーザー指定envの口ではない。`HOME` はrun-asの値を信用せず、実Python prefixから導出する。

子のenvは `LIBGL_ALWAYS_SOFTWARE=1`、`GALLIUM_DRIVER=softpipe`、`EGL_LOG_LEVEL=debug` と固定runtime/private HOME設定だけ。`WAYLAND_DISPLAY`、`XDG_RUNTIME_DIR`、`DISPLAY`、`EGL_PLATFORM`、`LD_PRELOAD` を継承しない。

## 記録と判定

- `eglQueryString(EGL_NO_DISPLAY,EGL_EXTENSIONS)` は拡張対応のboolだけを残す。`eglGetPlatformDisplay` は `EGL_PLATFORM_SURFACELESS_MESA` と `EGL_DEFAULT_DISPLAY` 固定。core の属性型は `EGLAttrib*`、EXT fallback は `EGLint*` として宣言する。EGLint/EGLBooleanは32bitを検査する。
- display取得と `eglInitialize` の直後に `eglGetError` を取得し、整数値と固定enumを保存する。**API eventをflushした後**に `eglTerminate` を実行し、その結果を別欄へ残す。terminate時のerrorや後続crashで初回errorを上書きしない。
- 子stdoutは固定schemaのみ受理。stderrは上限付きでdrainして既知分類・回数・未分類件数だけを残す。raw本文、path、symbol、extension全文、全envは保存しない。
- 実際にspawnしたhandle/groupを期限付きで所有し、取消/EOF/timeout/runtime変化でTERM、猶予後にKILL、reap/group回収を記録する。footや観測PIDはsignalしない。
- remoteの `~/.local/state/eidolon/egl-init/<probe-id>` は新規0700、reportは0600、既存runを再利用しない。hostにも新規0700 directory/0600 `result.json` を残す。

`status=diagnosed` とCLI終了0は、負のAPI結果も含めて診断と回収を完了した意味。APIの `initialize.returned` と `error` を別に読む。子の元終了コード、signal、timeout、回収失敗は保持する。`drawing/wayland/sdk/input/hmd` はすべて `not_run`。

surfacelessも失敗するなら共有CPU screen/config/runtime側を優先して調べる。surfaceless成功・Waylandのみ失敗ならloader/config等の差を調べる。いずれも単独結果から原因を断定せず、同条件の無根拠retryやMesa全rebuildはしない。

独立検証:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_egl_surfaceless_probe.py -v
```


## v2: 実loaded imageと固定ファイルを照合する

Build161のsurfaceless+softpipeも `EGL_NOT_INITIALIZED` / `dri2_screen_failed` となった。実ELFのsoftware descriptorはsoftpipe/llvmpipe両分岐を含み、単純な未組込み説を否定できる。一方、`dri_fill_in_modes` の詳細messageは `MESA_DEBUG` 有効buildだけの `debug_printf` であり、今回のrelease ELFには当該message自体が無い。log分類に現れないことからconfig生成失敗を除外しない。環境変数だけの同条件retryは増やさない。

`eidolon-egl-surfaceless-v2-loaded-identity` は同じ固定の非描画初期化へ、loaded imageの照合を加える。EGL API/errorをflushした直後、terminate前に既知の `libEGL_mesa.so` / `libgallium-26.2.6.so` / `libc++_shared.so` の3個だけを採取する。既存のlibEGL file pinも保持し、file検査は4個になる。

- 公開 `dl_iterate_phdr` のLP64共通prefixとcallback sizeを使い、固定名だけ選ぶ。追加fieldや全library名、ASLRアドレスは記録しない。既知名が複数imageにあれば失敗とする。
- `/proc/self/maps` は最大1MiBをメモリ内で読み、既知3名だけを選ぶ。全maps、アドレス、他library/pathは保存・出力しない。選択したmappingのdevice major/minorとinodeを、固定canonical pathを開いたfdのfstatへ照合する。deleted、別path、inode違い、実行mapping無しを拒否する。
- UID10158・mode・size・hashと、load前に検証したdev/inode/mtime/ctimeを照合する。hash読取り後もlinker列挙とmappingが同一であることを再検査する。結果のpathはTermuxの既知prefixだけである。これはfile-backed imageのidentity確認であり、実行中メモリ全体のhashや内部のNULL分岐の証明ではない。
- libgalliumの追加pinは `2684bf73cd4cec80f40c19ffd4e787af4680b9c03912650e1bdc7320670934f1`（17,669,920 bytes）。既知のlibEGL/libEGL_mesa/libc++30 pinも維持する。期待値を現在の実ファイルから自動生成しない。
- 初回EGL結果・元child終了code・terminate・group/reap/pipe回収は従来どおり残す。identity未確認は別 `loaded_libraries` のfixed reasonで記録し、初回API failureを上書きしない。`diagnosed` はこの診断の完了を表す。描画、SDK、入力、HMDは未実行のままである。

CLI引数は既存のserial/artifact-rootだけ。新probe ID/新runを作り、旧失敗を上書きしない。remote sourceの4 SHA（新identity moduleを含む）とoperator v2を記録する。C/SDL binaryや配布manifestの更新は不要である。

```sh
scripts/quest-probe-egl-surfaceless --serial "$EIDOLON_QUEST_SERIAL" \
  --artifact-root "$EIDOLON_TASK_ROOT/egl-loaded-identity"
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_egl_surfaceless_probe.py -v
```

一次資料: [AOSP Bionic link.h](https://android.googlesource.com/platform/bionic/+/331070e41ddd0e3669b8fc2625fd71a26045f0a3/libc/include/link.h)、[dl_iterate_phdr manual](https://man7.org/linux/man-pages/man3/dl_iterate_phdr.3.html)、[proc_pid_maps manual](https://man7.org/linux/man-pages/man5/proc_pid_maps.5.html)、[Mesa26.2.6 u_debug.h](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.6/src/util/u_debug.h)、[software driver helper](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.6/src/gallium/auxiliary/target-helpers/sw_helper.h)。

実loaded imageの一致を確認した後も同じ初期化失敗だった。内部NULL分岐へ進む前段は、
[非EGLの固定子ptrace能力probe](ptrace-capability-probe-20261108.md)で分ける。
この前段も診断専用であり、EGL/SDL描画の合格にはしない。

実機でptraceの取得能力が成立した後の固定3分岐観測は
[EGL NULL stage probe](egl-null-stages-probe-20261108.md)に分離した。
従来の公開APIだけのoperatorは維持し、hardware設定を暗黙に追加しない。

## v3: EGL frontendのKopperを一項だけ無効にする

実mode分類で最初のscreen候補は`kopper`、FD-1と確認できた。`GALLIUM_DRIVER=softpipe`を
指定していたが、これだけでEGL frontendもSWRASTになるとは確認できていなかった。
過去のAPI欄`driver=softpipe`は固定envの要求を示し、実frontendの証明ではない。

Termux固定tree `fbdb3c03610c697322ac608e77cea2b553f49ef3`の
[Mesa recipe](https://github.com/termux/termux-packages/blob/fbdb3c03610c697322ac608e77cea2b553f49ef3/packages/mesa/build.sh)と
[0017 patch](https://github.com/termux/termux-packages/blob/fbdb3c03610c697322ac608e77cea2b553f49ef3/packages/mesa/0017-preserve-egl-support-in-zink.patch)を
再確認した。patchはX11だけでなく共通`eglInitialize`のZink選択も変え、
`MESA_LOADER_DRIVER_OVERRIDE`が無い場合にもZinkを既定にする。
今回の固定envはoverrideを渡さないため、観測したKopper選択と整合する。

[Mesa26.2.6のEGL共通選択](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.6/src/egl/drivers/dri2/egl_dri2.c)の
`dri2_detect_swrast_kopper`は`LIBGL_KOPPER_DISABLE`を直接読む。GLX限定の設定ではない。
これをtrueにするとKopperがfalseとなり、ForceSoftwareからswrast/swrast_not_kmsへ進む条件が成立する。
下流のsoftpipe指定とは別のfrontend選択を分けるため、次の固定対照を用意した。

```sh
scripts/quest-probe-egl-surfaceless --serial "$EIDOLON_QUEST_SERIAL" \
  --variant kopper-disabled --artifact-root "$EIDOLON_TASK_ROOT/egl-kopper-disabled"
```

`eidolon-egl-surfaceless-v3-kopper-variant`は`baseline`（既定）と`kopper-disabled`だけを受け取る。
後者が足すenvは`LIBGL_KOPPER_DISABLE=1`の一個だけ。baselineのenv、4library pin、
vendor JSON、loaded identity、初回error保存と回収は維持する。
variantはrequest/host/remote結果へ固定名で記録し、応答の一致を確認する。任意env、preload、library変更はない。
新runの元API結果と子終了codeを比較し、成功時にも過去の12289/終了1を上書きしない。
公開APIが成功しても描画・SDL renderer・GUI・入力・HMDは未実行である。

初期化の一回対照を先に行い、同じ条件を内部mode/3 NULL段階へ接続する。
その後にSDLの既存load/context/renderer段階へ進む。正常結果を仮定して次段を合格にしない。

2026-11-08の固定一回対照では、initialize=true/EGL1.5/`EGL_SUCCESS`、子はcode0で自然終了した。
terminate、runtime不変、reap/group/pipe回収も成立し、同じ4library/vendor pinとloaded identityを維持した。
Kopper無効で公開EGL初期化が成立した一方、baselineのKopper/12289/終了1は記録を保持する。
この時点ではcontext作成、SDL描画、GUI、入力、HMDをまだ受け入れていない。

続く同条件の内部観測 `9c038c971947deff4d546e297d5b572d` では `dri_type=swrast`、FD=-1の条件が一致し、
screen allocation、pipe screen、DRI configsの3段階すべてが非NULLだった。固定library/instruction/mapping照合と
hardware breakpointの3hit・解除、公開APIのEGL1.5初期化・terminate・自然終了0・全回収も成立した。
`prior_initialize_failure_matches=false` をそのまま残し、baselineのKopper失敗との相違を記録した。
観測は型と真偽値だけで、実アドレスやregister dump、入力本文は保存しない。
