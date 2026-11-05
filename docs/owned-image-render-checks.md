# 所有済み画像からAndroid EGLへの描画検証（D01）

Quest 3の同じ製品APK・標準JNI・実GPUで、owned RGBA画像をSmithay GlesRendererへ取り込み、
readbackで色、上下、8transform、scale、透過、重なり、clipを検査した。
consumerはAndroid ImageReaderで、SDK Activity/Sceneは開かない。
`synthetic_visibility=true`、`scope=native-instrumentation`、`normal_ui_returned=false`を保存する。
HMDの可読性・SDKパネル上の位置・passthrough・物理入力はD02以降へ残す。

## 所有権と座標

`native/render/src/egl.rs`がAndroidの公開`libEGL.so`でES3 contextを作り、
Smithayの`EGLContext::from_raw`へ渡す。一つのGL ownerが一つのDevice/contextを持ち、
targetごとのEGLSurfaceへ順にbindする。SDKと共有されるDisplayに`eglTerminate`しない。
NativeWindowの追加参照はEGLSurfaceの寿命まで保持する。停止はtexture→Surface→NativeWindow→contextの順で行う。

Smithay 0.7.0はEGL loader名を`libEGL.so.1`に固定していたため、Androidだけ公開名へ変える
小さな[固定patch](../native/patches/smithay/README.md)を追加した。
`renderer_gl/backend_egl`だけを追加し、DRM/GBM/udev、system libwayland backendは有効にしない。
archive・patch・全生成file・Cargo feature tree・APK ELF依存を引き続き検査する。

`WindowSnapshot`はimmutableなowned pixelだけを持つ。protocol objectと共有SHM pointerはGLへ渡さない。
packed premultiplied RGBA8をFourcc::Abgr8888で取り込み、同じcontentの再uploadを避ける。
同寸法の新contentには`update_memory`を使い、サイズ変更時は作り直す。
GPU textureの計上上限も128MiBに制限する。計上値は画素のbyte数であり、driverの実割当量の測定ではない。

windowの縦横比を保ってletterboxを作り、layerのoffset/clipと逆buffer transformを適用する。
背景は不透明な黒。[Smithayの公式最小サンプル](https://github.com/Smithay/smithay/blob/v0.7.0/examples/minimal.rs)
と同じ`Transform::Flipped180`をEGL window framebufferの出力へ適用し、canonicalな左上原点を保つ。
各layerの`wl_buffer` transformとは別の補正である。

diagnostic四色patternも同じSmithayの`draw_solid`を使う。raw GLの独立した描画stateを混在させない。
readbackだけをSmithay frameの`with_context`で行い、GL stateを変更しない。
`Frame::finish`のSyncPointを扱い、Surfaceを再bindしてからswapする。

## 再現

```bash
scripts/check-host --suite all
scripts/build-android 55
# EIDOLON_APKは上のscriptが保存したAPKの絶対path。
adb -s "$EIDOLON_QUEST_SERIAL" install -r "$EIDOLON_APK"
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" \
  --case render --instrumentation --timeout 300
EIDOLON_TEST_APK="$EIDOLON_APK" python3 -m unittest discover -s scripts/tests -v
```

`render`の通常UI caseはまだ実装していないため、`--instrumentation`なしは拒否する。
既存の鍵・profile・sshdは変更しない。試験用SurfaceはImageReaderが所有し、native detach/Stopの後に
consumerのhandler thread上で閉じる。native自身の停止を確認してからthreadをjoinする。

## 試行と検証範囲

最初はadapterのhost compileとarm64/API34 compileを行った。
GlesFrameのinherent methodとtrait methodの引数差、GlesTarget借用中のswap、SyncPointの未処理を
compileで見つけ、明示的なtrait呼出・target解放・fence待ちへ修正した。
依存lock変更後には古いXKB build markerを拒否し、固定sourceから再生成した。
再利用済みSmithay生成sourceは旧markerと全treeの一致を確認してから解析用cacheへ退避し、新patchから再構成した。

Build50のrun `20261105T112346-80ba1262`は、contextとtexture importに成功したが最初の描画で失敗した。
Build51のrun `20261105T112700-93f40868`では段階診断を追加し、GL error 0で色が合わないと判明した。
さらに、共通ownerが`COMPOSITE_MISMATCH`まで`SURFACE_LOST`へ上書きしていたため元のcodeを保持するよう修正した。
EGL window用の上下補正を入れたBuild52では全36stageが成功した。権限変更は行っていない。

Build52のrun `20261105T113001-b493e31e`は、64×64→128×96の2世代で次を確認した。

- 各世代で8transform×scale1/2の16stage、premultiplied alpha/重なり/clipと同寸法更新の2stage。
- CPUの独立した非対称ABCDEF表と既知色を使うGPU readback。GL error 0、累積420sample。
- 同寸法のtexture更新6回、終了直前のtexture3個/計上96bytes、context1個。
- ImageReader consumer54images。Stop受付0ms・全回収9ms。
- 停止後のworker/context/texture/latest imageが0、新native runtime生成・停止・registry回収。

Build53では新しいSmithay四色診断も先に検査し、同じ36stageとともに成功した。
debug/release assemble・lint・package検査、実APKのPython38件も成功した。
最終reviewでは通常detach/Stopの解放順を設計に合わせ、画像の検証codeを保持するhost回帰も追加した。

最終候補Build55のrun `20261105T114143-bb7da456`は、四色4sampleと36stageすべてがpass。
composite readback496sample、texture更新6回、ImageReader67images、Stop受付0ms・全16msだった。
停止後のworker/context/texture/latest imageは0で、新native runtimeとregistry回収も成功した。
Rust69件、Python実APK38件、launcher16件、fmt/clippy/designとdebug/release assemble/lint/packageが成功した。

APK SHA256は`d90935fda6a5bac043ccb3a0a1c5912d7c5c596cfcbfe81eff3b1f987050700f`、
source digestは`a86b3edaad9ef4d8b073722463cc52271dd86e83a711bafeb21690ac70a08775`。
`c0d50ce`からのdirty buildとして保存し、後のclean commitから再buildしたAPKとは区別する。
Build54はAPK生成に成功したがClippyのif書式検査が失敗したため最終候補にせず、55で修正・全検査を行った。

同sourceの実機回帰はすべてnative scopeでpassした。

| case | run |
| --- | --- |
| SSH経由の100hash/foot/回収 | `20261105T114150-d487a364` |
| Waypipe起動停止 | `20261105T114157-f1707f02` |
| Window state 10case | `20261105T114207-2d4e7430` |
| SHM 9case | `20261105T114214-20f70b29` |
| 独立truncate/回復 | `20261105T114222-3f1dc631` |
| native依存10cycle | `20261105T114228-094ded0c` |
| SSH forward | `20261105T114234-c4fb77e3` |
| remote回収7故障/lease | `20261105T114242-e4ff1cc7` |

試験後にWaypipe/footは残らず、元のTermux shell・sshdは維持された。

hostでは1000枚のsubmitをattach前に最新1枚へまとめ、古いArcが解放されること、
古いframe拒否、attach直後の最新描画、停止回収を検査する。
描画誤りや資源残留を含む改変診断がPC判定を通らないことも検査した。
これらのhost testは実GPUの代用にせず、上のQuest結果と分けて記録する。

raw logsは設定された作業rootの`eidolon-phase5-implementation/d01-*`、
`d01-builds/`、`checks/<run>/`に保存する。公開記録に端末識別子・室内画像・terminal本文を含めない。
