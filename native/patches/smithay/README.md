# Smithay 0.7.0の固定patch

`0003-compiled-keymap-constructor.patch`は同梱compiled XKBから直接seatを作るAPIを追加する。
system rulesからの初期化を挟まず、contextのdefault includeを無効にする。
既存RMLVO APIは維持し、tracingへkeymap本文を出さない。

`0004-keymap-fd-nul-android.patch`は旧client向けのkeymap FDにも末尾NULを含める。
Androidではclientごとのunsealed memfdコピーを使い、`/tmp`やprocess環境変数へ依存しない。
新client向けのsealed FDと非Androidの一時file経路は維持する。
[検出したwire失敗と再現](../../../docs/keyboard-checks.md)を参照する。

`0002-android-egl-library.patch`はAndroidだけEGLの公開library名`libEGL.so`を使う。
その他のtargetでは上流の`libEGL.so.1`を維持する。EGL function/extensionの動作は変えない。
外部で初期化したAndroid EGLDisplay/ContextをSmithayの`from_raw`へ渡し、
SDKと共有されるDisplayへ`eglTerminate`しない契約を維持する。

`0001-shm-pool-accounting-lifetime.patch`はpool作成時にdrop guardを預けるAPIと、
現在のmapping sizeを読むAPIを追加する。Wayland handler、SIGBUS handler、map/copy手順は変更しない。

上流の`Pool::drop`は`InnerPool`をprocess共通の別threadへ渡し、後からunmap/closeする。
wl_shm_pool resourceの破棄後もwl_bufferがmappingを参照できる。
そのためresourceの個数や弱参照だけで使用予算を戻してはいけない。
guardは実際の`InnerPool`に保持し、mappingとFDを解放した後にdropする。
compositorはmap前に予算を確保し、resize前に追加分を確保する。失敗時は対象clientを終了する。

原本はcrates.ioの公式`.crate`。archive SHA256、patch SHA256、MIT licenseを
`native/deps.lock.json`へ固定した。`scripts/prepare-smithay`が原本からpatchを適用し、
Git対象外の`native/generated/smithay`を構成する。毎回全fileを再構成した結果と比較し、
生成済みsourceの未記録編集・追加file・symlinkを拒否する。
`scripts/check-native-deps`はCargoがこのpathと固定版をresolveしていることも確認する。

```bash
scripts/build-native-deps --target host
scripts/check-host --suite compositor
```

検査clientは8つのpoolを作り、それぞれのbufferを残したままpool resourceを破棄する。
9つ目のpoolが拒否されることと、client切断後の実mapping・FD・予算回収を確認する。
上流のprocess共通drop thread自体はprocess終了まで存続する。
sessionが所有するworkerとは別であり、Stopでは実pool予算が0に戻ったことを照合する。
