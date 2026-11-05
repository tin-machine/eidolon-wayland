//! seat/keymap/input状態はprotocol ownerに置き、GL/UIへxkbやWayland resourceを渡さない。
use super::{ProtocolState, XdgToplevelSurfaceData, with_states};
use eidolon_model::{
    ids::{CommandSeq, WindowId},
    keyboard::{KeyAction, KeyPacket, KeyTracker, KeyboardLayout},
    window::{LogicalSize, WindowLife},
};
use serde::Serialize;
use sha2::{Digest, Sha256};
use smithay::{
    backend::input::KeyState,
    input::keyboard::{FilterResult, Keycode},
    reexports::wayland_protocols::xdg::shell::server::xdg_toplevel,
    utils::SERIAL_COUNTER,
};

#[derive(Clone, Debug, Default, Serialize)]
pub struct InputReport {
    pub pointer: super::pointer::PointerReport,
    pub keyboard: bool,
    pub layout: Option<KeyboardLayout>,
    pub keymap_sha256: Option<String>,
    pub wire_keymap_sha256: Option<String>,
    pub include_paths: Option<usize>,
    pub focus_window: Option<WindowId>,
    pub focus_token: Option<CommandSeq>,
    pub pressed: usize,
    pub delivered: u64,
    pub resets: u64,
}

#[derive(Default)]
pub(super) struct InputState {
    pub(super) report: InputReport,
    pub(super) pointer: super::pointer::PointerState,
    keys: KeyTracker,
    next_focus: i64,
    last_key_time: u32,
}

fn keymap(layout: KeyboardLayout) -> &'static str {
    match layout {
        KeyboardLayout::Us => include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../android/app/src/main/assets/keymaps/us.xkb"
        )),
        KeyboardLayout::Jis => include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../android/app/src/main/assets/keymaps/jis.xkb"
        )),
    }
}

impl ProtocolState {
    /// client受入前の初期化。実行中のlayout変更は入力resetを実装してから接続する。
    pub fn configure_keyboard(&mut self, layout: KeyboardLayout) -> Result<(), &'static str> {
        if self._seat.get_keyboard().is_some() {
            return Err("KEYBOARD_ALREADY_CONFIGURED");
        }
        let text = keymap(layout);
        let keyboard = self
            ._seat
            .add_keyboard_from_string(text.to_owned(), 600, 25)
            .map_err(|_| "KEYMAP_INVALID")?;
        let (paths, wire_keymap_sha256) = keyboard.with_xkb_state(self, |context| {
            let xkb = context.xkb().lock().unwrap();
            // SAFETY: context参照/追加refをclosureから持ち出さず、同じownerで個数だけ読む。
            unsafe {
                (
                    xkb.context().include_paths().count(),
                    hash(
                        xkb.keymap()
                            .get_as_string(xkbcommon::xkb::KEYMAP_FORMAT_TEXT_V1)
                            .as_bytes(),
                    ),
                )
            }
        });
        if paths != 0 {
            self._seat.remove_keyboard();
            return Err("KEYMAP_EXTERNAL_INCLUDES");
        }
        self.input.report = InputReport {
            keyboard: true,
            layout: Some(layout),
            wire_keymap_sha256: Some(wire_keymap_sha256),
            include_paths: Some(paths),
            keymap_sha256: Some(
                Sha256::digest(text.as_bytes())
                    .iter()
                    .map(|b| format!("{b:02x}"))
                    .collect(),
            ),
            ..Default::default()
        };
        self.configure_pointer();
        Ok(())
    }
    pub fn change_keyboard_layout(&mut self, layout: KeyboardLayout) -> Result<(), &'static str> {
        self.reset_input();
        let keyboard = self._seat.get_keyboard().ok_or("NOT_READY")?;
        let text = keymap(layout);
        keyboard
            .set_keymap_from_string(self, text.to_owned())
            .map_err(|_| "KEYMAP_INVALID")?;
        let (paths, wire) = keyboard.with_xkb_state(self, |context| {
            let xkb = context.xkb().lock().unwrap();
            // SAFETY: pinned Contextを同じowner/closure内だけ参照する。
            unsafe {
                (
                    xkb.context().include_paths().count(),
                    hash(
                        xkb.keymap()
                            .get_as_string(xkbcommon::xkb::KEYMAP_FORMAT_TEXT_V1)
                            .as_bytes(),
                    ),
                )
            }
        });
        if paths != 0 {
            return Err("KEYMAP_EXTERNAL_INCLUDES");
        }
        self.input.report.layout = Some(layout);
        self.input.report.include_paths = Some(paths);
        self.input.report.wire_keymap_sha256 = Some(wire);
        self.input.report.keymap_sha256 = Some(hash(text.as_bytes()));
        Ok(())
    }
    pub fn input_report(&self) -> InputReport {
        let mut report = self.input.report.clone();
        report.pressed = self.input.keys.pressed_count();
        report.pointer = self.pointer_report();
        report
    }
    #[cfg(any(test, feature = "debug-fixtures"))]
    pub(crate) fn keyboard_symbol(
        &mut self,
        code: eidolon_model::keyboard::EvdevCode,
    ) -> Option<u32> {
        self._seat.get_keyboard().map(|keyboard| {
            keyboard.with_xkb_state(self, |context| {
                let xkb = context.xkb().lock().unwrap();
                // SAFETY: closure外へxkb stateを持ち出さない。
                unsafe { xkb.state().key_get_one_sym(Keycode::new(code.xkb())).raw() }
            })
        })
    }
    pub fn focus_window(&mut self, window: WindowId) -> Result<(), &'static str> {
        self.focus_keyboard(window)?;
        self.reset_pointer();
        Ok(())
    }
    pub(super) fn focus_keyboard(&mut self, window: WindowId) -> Result<(), &'static str> {
        let surface = self
            .toplevels
            .values()
            .find(|w| w.id == window && w.life == WindowLife::Mapped)
            .map(|w| w.surface.wl_surface().clone())
            .ok_or("STALE")?;
        let keyboard = self._seat.get_keyboard().ok_or("NOT_READY")?;
        if self.input.report.focus_window == Some(window) {
            return Ok(());
        }
        let next = self.input.next_focus.checked_add(1).ok_or("ID_EXHAUSTED")?;
        let token = CommandSeq::new(next).map_err(|_| "ID_EXHAUSTED")?;
        // 新しいwindowへ古い押下を持ち込まない。
        self.reset_keyboard();
        self.input.report.focus_window = Some(window);
        self.input.next_focus = next;
        self.input.report.focus_token = Some(token);
        self.set_activated(Some(window));
        keyboard.set_focus(self, Some(surface), SERIAL_COUNTER.next_serial());
        Ok(())
    }
    fn set_activated(&mut self, window: Option<WindowId>) {
        for record in self
            .toplevels
            .values_mut()
            .filter(|w| w.life == WindowLife::Mapped)
        {
            record.surface.with_pending_state(|state| {
                if Some(record.id) == window {
                    state.states.set(xdg_toplevel::State::Activated);
                } else {
                    state.states.unset(xdg_toplevel::State::Activated);
                }
            });
            if let Some(serial) = record.surface.send_pending_configure() {
                self.counters.configures += 1;
                let size = with_states(record.surface.wl_surface(), |states| {
                    states
                        .data_map
                        .get::<XdgToplevelSurfaceData>()
                        .unwrap()
                        .lock()
                        .unwrap()
                        .current_server_state()
                        .size
                });
                if let Some(size) = size.and_then(|s| LogicalSize::new(s.w as u32, s.h as u32).ok())
                {
                    // 上限時は現在のresizeを成功にせず、既知ticketだけで判定する。
                    let _ = record.resize.alias(serial.into(), size);
                }
            }
        }
    }
    pub fn key_at_focus(
        &mut self,
        window: WindowId,
        token: CommandSeq,
        key: KeyPacket,
    ) -> Result<(), &'static str> {
        if self.input.report.focus_token != Some(token) {
            return Err("STALE");
        }
        self.key(window, key)
    }
    fn key(&mut self, window: WindowId, key: KeyPacket) -> Result<(), &'static str> {
        if self.input.report.focus_window != Some(window) {
            return Err("NOT_READY");
        }
        self.refresh_input_focus();
        if self.input.report.focus_window != Some(window) {
            return Err("STALE");
        }
        let code = key.code().ok_or("INVALID_ARGUMENT")?;
        let action = match self
            .input
            .keys
            .update(key.device, code, key.action, key.repeat)
        {
            Ok(action) => action,
            Err(_) => {
                self.reset_input();
                return Err("RESOURCE_LIMIT");
            }
        };
        if let Some(action) = action {
            let keyboard = self._seat.get_keyboard().ok_or("NOT_READY")?;
            keyboard.input::<(), _>(
                self,
                Keycode::new(code.xkb()),
                match action {
                    KeyAction::Pressed => KeyState::Pressed,
                    KeyAction::Released => KeyState::Released,
                },
                SERIAL_COUNTER.next_serial(),
                key.time_ms,
                |_, _, _| FilterResult::Forward,
            );
            self.input.report.delivered += 1;
            self.input.last_key_time = key.time_ms;
        }
        Ok(())
    }
    /// urgent lane/焦点喪失から呼ぶ。keyup→modifiers→leaveをownerで送ってから忘れる。
    pub fn reset_input(&mut self) {
        self.reset_pointer();
        self.reset_keyboard();
    }
    fn reset_keyboard(&mut self) {
        let keys = self.input.keys.reset();
        let focused = self.input.report.focus_window.take().is_some();
        self.input.report.focus_token = None;
        if !focused && keys.is_empty() {
            return;
        }
        if let Some(keyboard) = self._seat.get_keyboard() {
            let now = monotonic_time_ms(self.input.last_key_time);
            for code in keys {
                keyboard.input::<(), _>(
                    self,
                    Keycode::new(code.xkb()),
                    KeyState::Released,
                    SERIAL_COUNTER.next_serial(),
                    now,
                    |_, _, _| FilterResult::Forward,
                );
                self.input.report.delivered += 1;
            }
            keyboard.set_focus(self, None, SERIAL_COUNTER.next_serial());
        }
        self.set_activated(None);
        self.input.report.resets += 1;
    }
    pub(super) fn refresh_input_focus(&mut self) {
        self.refresh_pointer();
        if let Some(window) = self.input.report.focus_window
            && !self
                .toplevels
                .values()
                .any(|w| w.id == window && w.life == WindowLife::Mapped)
        {
            self.reset_input();
        }
    }
}

/// Android uptimeMillisと同じCLOCK_MONOTONIC。wireの32bit wrapは仕様どおり維持する。
pub(crate) fn monotonic_time_ms(fallback: u32) -> u32 {
    let mut time = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: 初期化済みtimespecの有効なmutable pointerを同期callへ渡す。
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut time) } != 0
        || time.tv_sec < 0
        || !(0..1_000_000_000).contains(&time.tv_nsec)
    {
        fallback
    } else {
        ((time.tv_sec as u64).wrapping_mul(1000) + (time.tv_nsec as u64) / 1_000_000) as u32
    }
}

fn hash(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use eidolon_model::{ids::SessionId, keyboard::EvdevCode};
    use smithay::{
        backend::input::KeyState,
        input::keyboard::{FilterResult, Keycode},
        reexports::wayland_server::Display,
        utils::Serial,
    };

    #[test]
    fn standalone_keymaps_create_real_seat_keyboard_without_system_paths() {
        for (layout, shifted_two) in [(KeyboardLayout::Us, b'@'), (KeyboardLayout::Jis, b'"')] {
            let display = Display::<ProtocolState>::new().unwrap();
            let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
            state.configure_keyboard(layout).unwrap();
            let report = state.input_report();
            assert!(report.keyboard);
            assert_eq!(report.include_paths, Some(0));
            assert_eq!(report.layout, Some(layout));
            let keyboard = state._seat.get_keyboard().unwrap();
            let a = keyboard.with_xkb_state(&mut state, |context| {
                let xkb = context.xkb().lock().unwrap();
                // SAFETY: stateの参照/追加refをclosure外へ持ち出さない。
                unsafe {
                    xkb.state()
                        .key_get_one_sym(Keycode::new(EvdevCode::new(30).unwrap().xkb()))
                        .raw()
                }
            });
            assert_eq!(a, u32::from(b'a'));
            keyboard.input::<(), _>(
                &mut state,
                Keycode::new(EvdevCode::new(42).unwrap().xkb()),
                KeyState::Pressed,
                Serial::from(1),
                0,
                |_, _, _| FilterResult::Forward,
            );
            assert!(keyboard.modifier_state().shift);
            let two = keyboard.with_xkb_state(&mut state, |context| {
                let xkb = context.xkb().lock().unwrap();
                // SAFETY: stateの参照/追加refをclosure外へ持ち出さない。
                unsafe {
                    xkb.state()
                        .key_get_one_sym(Keycode::new(EvdevCode::new(3).unwrap().xkb()))
                        .raw()
                }
            });
            assert_eq!(two, u32::from(shifted_two));
            keyboard.input::<(), _>(
                &mut state,
                Keycode::new(EvdevCode::new(42).unwrap().xkb()),
                KeyState::Released,
                Serial::from(2),
                0,
                |_, _, _| FilterResult::Forward,
            );
            assert!(!keyboard.modifier_state().shift);
            assert!(
                state
                    ._seat
                    .add_keyboard_from_string("invalid keymap".into(), 600, 25)
                    .is_err()
            );
            assert_eq!(state._seat.get_keyboard(), Some(keyboard.clone()));
            let other = if layout == KeyboardLayout::Us {
                KeyboardLayout::Jis
            } else {
                KeyboardLayout::Us
            };
            state.change_keyboard_layout(other).unwrap();
            let changed = state.input_report();
            assert_eq!(changed.layout, Some(other));
            assert_eq!(changed.include_paths, Some(0));
            assert_ne!(changed.wire_keymap_sha256, report.wire_keymap_sha256);
            assert_eq!(state._seat.get_keyboard(), Some(keyboard.clone()));
            keyboard.input::<(), _>(
                &mut state,
                Keycode::new(EvdevCode::new(42).unwrap().xkb()),
                KeyState::Pressed,
                Serial::from(3),
                0,
                |_, _, _| FilterResult::Forward,
            );
            let symbol = keyboard.with_xkb_state(&mut state, |context| {
                let xkb = context.xkb().lock().unwrap();
                // SAFETY: 同じownerのclosure内だけXKB stateを参照する。
                unsafe {
                    xkb.state()
                        .key_get_one_sym(Keycode::new(EvdevCode::new(3).unwrap().xkb()))
                        .raw()
                }
            });
            assert_eq!(
                symbol,
                u32::from(if other == KeyboardLayout::Us {
                    b'@'
                } else {
                    b'"'
                })
            );
            keyboard.input::<(), _>(
                &mut state,
                Keycode::new(EvdevCode::new(42).unwrap().xkb()),
                KeyState::Released,
                Serial::from(4),
                0,
                |_, _, _| FilterResult::Forward,
            );
        }
    }
}
