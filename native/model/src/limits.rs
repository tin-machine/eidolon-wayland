//! 初期予算。性能保証や、入力検査済みという意味ではない。
pub const COMMAND_BYTES: usize = 16 * 1024;
pub const CORE_COMMANDS: usize = 256;
pub const UI_COMMANDS: usize = 128;
pub const POLL_EVENTS: usize = 64;
pub const POLL_BYTES: usize = 64 * 1024;
pub const DIAGNOSTIC_BYTES: usize = 1024 * 1024;
pub const TOPLEVELS: usize = crate::window_slots::CAPACITY;
pub const VISIBLE_PANELS: usize = crate::window_slots::CAPACITY;
pub const SDK_BINDINGS: usize = crate::window_slots::CAPACITY;
// 九つの描画clientと補助/空接続のためのbounded headroom。表示枠の上限とは独立。
pub const CLIENTS: usize = 12;
// 実foot 1.28: 六窓で61、九窓で少なくとも91surface。byte予算とは別の有限metadata枠。
pub const SURFACES: usize = 128;
pub const BUFFER_DIMENSION: u32 = 2048;
pub const BUFFER_BYTES: usize = 16 * 1024 * 1024;
pub const CLIENT_SHM_BYTES: usize = 64 * 1024 * 1024;
pub const TOTAL_SHM_BYTES: usize = 128 * 1024 * 1024;
// 単一foot serverの各窓が別poolを保有する。九窓と一部更新の余地だけを許す。
pub const CLIENT_SHM_POOLS: usize = 16;
pub const PIXEL_BYTES: usize = 128 * 1024 * 1024;
pub const POSITIONER_COORDINATE: u32 = 32_768;
pub const FORWARD_CHANNELS: usize = 12;
pub const EXEC_RING_BYTES: usize = 64 * 1024;
