// render単体のtestでも、coreと同じ検証済みの固定static archiveを使う。
// CARGO_MANIFEST_DIRはどちらもnative/<crate>なので、同じroot/marker検査を共有できる。
include!("../core/build.rs");
