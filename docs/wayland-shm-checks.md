# WaylandとSHMの実装・検証（W02）

最小protocolと共有mappingから所有済みRGBA8へのcopyを実装し、
host/Quest Build43で正常100frame、同期cache、悪性client切断後の同Displayでの回復に成功した。
Build62でSDKとの共存・通常UI復帰も確認した。正常/異常9caseは
`20261105T130325-8e1010de`、truncate隔離は`20261105T130333-e0e97eec`でpass。W02はdone。
W03の完全なsurface lifecycle・snapshot、GL表示、SSH転送、footの成功とは区別する。

`ProtocolState`はrenderer/SSHを持たず、protocol ownerでdispatchする。
Smithayの既存global handlerを保持したまま、client受入前に広告versionを上限へ絞る。
実clientが取得したregistryはcompositor4、subcompositor1、shm1、output3、seat5、
xdg_wm_base1、data_device_manager3。outputは1024×768/60000mHz/scale1。
seatに入力capabilityはなく、selection/dragに有効なfocus/serialがない段階ではsourceを取消する。
clipboard転送は行わない。title/app_idは512 UTF-8 bytes以内へ制限する。

SHMのraw readは`native/core/src/compositor/shm.rs`だけに置く。
SmithayのSIGBUS保護callback内でformat・寸法・offset・stride・pool境界を検査し、
共有mappingからsliceを作らず各行を所有済みallocationへcopyする。
BGRA→RGBAへ並び替え、ARGBのpremultiplied alphaを保ち、XRGBのalphaは255にする。
GLへ渡す型はmodelの`Arc<OwnedRgba>`。同allocationの複数参照は二重計上せず、
最後の参照で予算を戻す。所有済みpixel全体128MiB、各buffer16MiB/最大2048pixel。
poolはclient64MiB/8個、全体128MiB。実解放まで保持する
[Smithay補助patch](../native/patches/smithay/README.md)を使う。

固定clientはmemfdの32×32色パターンとframe番号を送る。
初回bufferなしcommit→configure→ack→attach/commitの順で進め、
100回のcopy hash・release・frame callback、output enter/leave、空selection・取消を照合する。
frame callbackにはこのprotocol試験だけの仮想presentを使う。HMDへの表示成功ではない。
pool resourceを先に破棄してもbufferから読めること、切断後のFD/mapping/object/予算0も確認する。
同じbufferを、別rootの同期childの未適用commitにも入れる検査を追加した。
そのparentがcommitするまでreleaseを返さず、両consumerのcopy完了後に一度だけ返す。
使用tokenをSmithayの`Cacheable`としてSurfaceAttributesと同じtransactionに保持する。

```bash
scripts/check-host --suite compositor
scripts/build-android 43
# build scriptが示したAPKを、識別済みのQuestだけへadb install -rする。
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case shm --instrumentation --timeout 300
# SIGBUS/ART共存は別instrumentation起動で行い、同Displayでの正常client回復も必須にする。
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case shm-truncate --instrumentation --timeout 300
```

instrumentationはActivity/Sceneを起動せず、製品と同じAPK UID/JNIを使う。
`scope=native-instrumentation`、`normal_ui_returned=false`を保存する。
SIGBUSでAPKがcrashする場合は失敗とし、保護を外して通さない。
debug fixture/classはreleaseのELF/DEX/manifestから除外する。

試行錯誤:

1. Smithayの既定global versionは設計上限より高かった。公開backend APIで同じhandlerを
   再登録し、実registryとbindでversionを確認した。非公開seat/output dataを複製していない。
2. 当初はpool/buffer userdataの弱参照で予算を追う案を検討したが、上流の遅延drop queueに
   実mappingが残るため不十分だった。guardを実InnerPoolへ保持する最小patchへ変更した。
3. `DataInit::post_error`はglobal bind用で、通常requestではerrorの受け手が一時値だった。
   pool作成/不正bufferの拒否はcreating objectへの`post_error`でclient切断に収束させた。
4. 初回compileはcached attributesのcurrent/pendingを同時にmutable borrowし失敗。
   順に検査してbuffer IDを収集するよう修正した。SHA2 0.11のdigestはLowerHex未実装のため
   byteごとのhexへ変更した。acceptのDisplayHandleも複製してmutable APIへ渡す。
5. host suiteへfixture crateを登録する際、重複coreだけをskipする処理に変更した。
   compositor group全体をskipして新crateの検査まで抜かさない。
6. Build40はdebug入口のnullableなcase名をString引数へ渡してKotlin compileが失敗した。
   入口でnullを明示拒否し、Build41でassemble/lint/packageと実機基本検査が成功した。
7. 同期childの未適用commitはcached current/pendingの走査だけでは見えず、releaseを早く返す
   不具合を固定clientで再現した（`RELEASE_BEFORE_CACHED_CONSUMER`）。
   使用tokenをtransactionへ保持する修正後に同じ検査が成功した。Build42はdebug検査に成功し、
   不正寸法/stride/offset/formatの実client検査を追加したBuild43でnative検証を完了した。

## Build43のnative検証結果

APK SHA256は`51e73ef669c6f362a91ec28b6c7274c06e6eae722ac1d9d28ed34dbfc83401ba`、
source digestは`0a40f055373f112e28e999b23c118336c608a13fcac173a8419101ac939176c6`。
基準commit `fc4b0af`からのdirty buildであり、このdigestで実装内容を同定する。

| 実機run | 検査 | 結果 |
| --- | --- | --- |
| `20261105T081300-c29d34b8` | shm 9case | 全pass。正常と7拒否後の回復で各100hash一致、307〜430ms。同期cacheは49ms、2consumerのhash一致・release一度・未適用中releaseなし |
| `20261105T081308-c8b851a9` | 独立truncate | SIGBUS→BadMap1→対象client終了→同Display正常100hash、429ms、pass |
| `20261105T081314-44161d00` | native依存10cycle | pass |
| `20261105T081320-401c4534` | SSH forward | pass |
| `20261105T081330-53e1da0e` | remote回収7故障 | 実60秒lease失効を含み全pass |

SHM全caseの終了時FD数は70で一定、fixture FD/mapping・Wayland client/object・surface・
pool/pixel予算は0。shmの停止4ms、truncateの停止65ms、受付はいずれも100ms未満。
fresh runtimeの生成・停止・registry回収もinstrumentationで確認した。
host Rust60件、Python25件、launcher12件、実APK Python29件、debug/release assemble/lint/packageが成功。
生成済みSmithay sourceへの改変と追加fileが拒否され、原本へ戻すと再び通ることも実検査した。

W02は通常UI復帰とScene稼働時SDK共存が未確認のためin_progressを維持する。
このnative基盤を使ってW03へ進み、HMD確認は最終段階にまとめる。

rawログ・端末情報・APKは作業rootの`eidolon-phase5-implementation/`に保存する。
公開可能な判断と受入結果をこの文書と`progress.md`へ集約する。
