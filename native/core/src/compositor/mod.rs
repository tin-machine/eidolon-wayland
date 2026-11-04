//! 製品の依存・IPC gate。SHM clientのmappingとSIGBUS耐性はW02で検証する。
#[cfg(any(test, feature = "debug-fixtures"))]
pub mod checks;
pub mod primitives;
pub mod protocol;
pub mod shm;
mod shm_budget;
