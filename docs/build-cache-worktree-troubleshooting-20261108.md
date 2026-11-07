# 複数checkoutとビルド容量の検証記録

## shared host targetの所有

同じhost targetを複数checkoutで共有した時、別checkoutのfixture cacheを再使用し、
存在するcapacity moduleを参照できない失敗が起きた。source欠落ではなく、checkoutを切り替えた時の
mtime/fingerprintによる古い成果物の採用を確認した。Cargoのbuild lockだけでこの条件は解消しない。

この作業ではhost Cargoを専用のowner lockで直列化し、lock内で自checkoutのworkspace entrypointの
mtimeだけを更新してからbuild/test、件数・固定fixture名と実wire結果の確認までを行う。
内容・revision・source digestは変えない。第三者依存cacheを毎回削除せず、新しいtargetも複製しない。
Android用targetは別で、Gradle/APK操作はroot担当一つに固定する。

## 再生成可能なpackage成果物だけを整理

先行Build158は空き不足でlintが失敗した。未検証APKと失敗記録を保持し、検証済みへ扱い直していない。
host debug cacheの整理後、159以降のAPK検査は成功した。

Build164完了時にも空きが約3GiBになった。compiler終了とAPKのSHA、JUnit保全を確認し、
`target/native/<NDK>/<crate-version>`に限定したCargo自身のpackage cleanを使った。
dry-runは6491 file/1.3GiB、実行は6489 file/1.3GiB。空きは約4.1GiBへ回復した。
異なる時点のfile数差を隠さず記録し、source/APK/log、host target、第三者依存、releaseを保持した。

```sh
CARGO_INCREMENTAL=0 cargo clean --dry-run --profile dev --target aarch64-linux-android \
  --target-dir "$EIDOLON_NATIVE_TARGET" \
  -p eidolon-core -p eidolon-model -p eidolon-render -p eidolon-android -p eidolon-theme
# 対象、compiler停止、成果物保全を確認した後、同じ引数から--dry-runだけを外す。
```

今後も`CARGO_INCREMENTAL=0`を使い、3GiB未満では重い追加compileを始めず容量を調整する。
これは検証済みAPKや再開記録の保存期間を短くする運用ではない。
