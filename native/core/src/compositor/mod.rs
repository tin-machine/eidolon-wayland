//! 製品の依存・IPC gate。SHM clientのmappingとSIGBUS耐性はW02で検証する。
#[cfg(any(test, feature = "debug-fixtures"))]
pub mod checks;
#[cfg(any(test, feature = "debug-fixtures"))]
pub mod input_checks;
#[cfg(any(test, feature = "debug-fixtures"))]
pub mod pointer_checks;
#[cfg(any(test, feature = "debug-fixtures"))]
pub mod state_checks;
#[cfg(test)]
pub(crate) static FIXTURE_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
pub mod primitives;
pub mod protocol;
pub mod shm;
mod shm_budget;
