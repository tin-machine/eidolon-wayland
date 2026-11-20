# NX16: 九つの独立Wayland接続のhost検査

Build169の九footは一つのWayland clientを共有する。これと別に、同じ私有Displayへ九つのUnixStream接続を作り、各Connection/registry/toplevel/SHM ownerを持つ実Wayland clientを並行実行した。

全九窓のMappedと九client/九poolを同時に観測するまでframe callbackを保留する。その後、各surfaceで固定100frameの実画素hashを順に確認し、合計900copy/callbackと全clientの100frame完了を照合する。途中の個別client終了を、最初の九窓同時保持と取り違えない。

終了後は各socketをinterrupt可能な所有handleで管理し、成功/失敗のいずれでも全workerをjoinする。client/object/surface/window/pixel/pool/fixture FD/mapが全てゼロになることを確認した。検査1件とcore all-targets clippyが成功した。

これは同一host process内の九つの独立Wayland接続と仮想presentを使う検査で、九つのOS process、SSH/Waypipe越しの九client、Quest SDKでの九独立client、HMDの結果とは分ける。既存の[単一foot serverの九窓SDK受入れ](nx16c-nine-window-integration-20261108.md)も保持する。

```sh
CARGO_INCREMENTAL=0 cargo test --locked -p eidolon-core independent_clients_checks:: -- --test-threads=1
CARGO_INCREMENTAL=0 cargo clippy --locked -p eidolon-core --all-targets -- -D warnings
```

shared targetを使う場合は、所有lockと自checkoutのentrypoint mtime刷新を行ってから実行する。thread終了後にcallbackを余分に作ったり、frame不足を自己申告counterだけで合格にしたりしない。
