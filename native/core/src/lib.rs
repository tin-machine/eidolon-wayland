pub mod clipboard_coordinator;
pub mod clipboard_shelf;
pub mod compositor;
pub mod diagnostics;
pub mod helper;
pub mod linux_desktop_v3;
pub mod panels;
pub mod preferences;
pub mod runtime;
mod sdk_evidence;
pub mod session;
mod session_memfds;
pub mod shutdown;
pub mod startup_observation;
pub mod theme;
mod theme_store;
pub mod ui_changes;
pub(crate) mod v3_retirement;
pub mod waypipe;
pub mod window_slots;
pub mod workload;
pub mod workspace;

pub mod window_groups;

pub mod window_group_owner;

mod termux_ime;
