//! Physical key grab and the return path for keys Fcitx did not consume.
use super::super::input::hash;
use super::{
    ClientState, ProtocolState, SERIAL_COUNTER, ZwpInputMethodKeyboardGrabV2, wl_keyboard,
};
use smithay::{
    backend::input::KeyState,
    input::keyboard::{KeyboardTarget, Keycode, ModifiersState},
    reexports::{
        wayland_protocols_misc::zwp_virtual_keyboard_v1::server::{
            zwp_virtual_keyboard_manager_v1::{self as manager, ZwpVirtualKeyboardManagerV1},
            zwp_virtual_keyboard_v1::{self as keyboard, ZwpVirtualKeyboardV1},
        },
        wayland_server::{
            Client, DataInit, Dispatch, DisplayHandle, GlobalDispatch, New, Resource,
            backend::GlobalId,
        },
    },
};
use std::{
    fs::File,
    os::unix::fs::FileExt,
    sync::atomic::{AtomicBool, Ordering},
};
pub(in super::super) fn keyboard_global(dh: &DisplayHandle) -> GlobalId {
    dh.create_global::<ProtocolState, ZwpVirtualKeyboardManagerV1, _>(1, ())
}
pub(super) struct KeyboardBinding {
    activation: Option<u64>,
    has_keymap: AtomicBool,
}

impl ProtocolState {
    pub(super) fn install_ime_grab(&mut self, object: ZwpInputMethodKeyboardGrabV2) {
        let Some(active) = self.external_ime_activation() else {
            return;
        };
        let Some(keyboard) = self._seat.get_keyboard() else {
            return;
        };
        let sent = keyboard.with_sealed_keymap_fd(|fd| {
            let fd = fd?;
            let size = File::from(fd.try_clone_to_owned().ok()?)
                .metadata()
                .ok()?
                .len();
            object.keymap(wl_keyboard::KeymapFormat::XkbV1, fd, size.try_into().ok()?);
            Some(())
        });
        if sent.is_none() {
            return;
        }
        object.repeat_info(25, 600);
        let m = keyboard.modifier_state().serialized;
        object.modifiers(
            SERIAL_COUNTER.next_serial().into(),
            m.depressed,
            m.latched,
            m.locked,
            m.layout_effective,
        );
        self.external_ime.last_modifiers = Some(m);
        self.external_ime.grab = Some((object, active));
    }
    pub(in super::super) fn send_ime_key(
        &mut self,
        code: u32,
        state: KeyState,
        serial: u32,
        time: u32,
        mods: &ModifiersState,
    ) -> bool {
        let Some((grab, activation)) = &self.external_ime.grab else {
            return false;
        };
        if !grab.is_alive() || self.external_ime_activation() != Some(*activation) {
            return false;
        }
        grab.key(serial, time, code, state.into());
        let m = mods.serialized;
        if self.external_ime.last_modifiers != Some(m) {
            grab.modifiers(serial, m.depressed, m.latched, m.locked, m.layout_effective);
            self.external_ime.last_modifiers = Some(m);
        }
        true
    }
}

impl GlobalDispatch<ZwpVirtualKeyboardManagerV1, ()> for ProtocolState {
    fn can_view(client: Client, _: &()) -> bool {
        client
            .get_data::<ClientState>()
            .is_some_and(|c| c.input_method)
    }
    fn bind(
        _: &mut Self,
        _: &DisplayHandle,
        _: &Client,
        resource: New<ZwpVirtualKeyboardManagerV1>,
        _: &(),
        init: &mut DataInit<'_, Self>,
    ) {
        init.init(resource, ());
    }
}
impl Dispatch<ZwpVirtualKeyboardManagerV1, ()> for ProtocolState {
    fn request(
        state: &mut Self,
        _: &Client,
        manager: &ZwpVirtualKeyboardManagerV1,
        req: manager::Request,
        _: &(),
        _: &DisplayHandle,
        init: &mut DataInit<'_, Self>,
    ) {
        if let manager::Request::CreateVirtualKeyboard { seat, id } = req {
            let activation = state.external_ime_activation();
            init.init(
                id,
                KeyboardBinding {
                    activation,
                    has_keymap: AtomicBool::new(false),
                },
            );
            if !state._seat.owns(&seat) {
                manager.post_error(0u32, "unknown seat");
            }
        }
    }
}
impl Dispatch<ZwpVirtualKeyboardV1, KeyboardBinding> for ProtocolState {
    fn request(
        state: &mut Self,
        _: &Client,
        obj: &ZwpVirtualKeyboardV1,
        req: keyboard::Request,
        binding: &KeyboardBinding,
        _: &DisplayHandle,
        _: &mut DataInit<'_, Self>,
    ) {
        if binding.activation.is_none() || binding.activation != state.external_ime_activation() {
            return;
        }
        let Some(kbd) = state._seat.get_keyboard() else {
            return;
        };
        match req {
            keyboard::Request::Keymap { format, fd, size } => {
                // Fcitx returns the keymap delivered with the grab. Keep the session's own layout.
                let file = File::from(fd);
                let matches = (1..=1024 * 1024).contains(&size)
                    && format == wl_keyboard::KeymapFormat::XkbV1 as u32
                    && file
                        .metadata()
                        .is_ok_and(|m| m.is_file() && m.len() == u64::from(size))
                    && {
                        let mut bytes = vec![0; size as usize];
                        file.read_exact_at(&mut bytes, 0).is_ok()
                            && bytes.last() == Some(&0)
                            && state.input.report.wire_keymap_sha256.as_deref()
                                == Some(&hash(&bytes[..bytes.len() - 1]))
                    };
                binding.has_keymap.store(matches, Ordering::Relaxed);
                if !matches {
                    obj.post_error(0u32, "unexpected IME keymap");
                }
            }
            keyboard::Request::Key {
                key,
                time,
                state: key_state,
            } if key <= 0x2ff && key_state <= 1 && binding.has_keymap.load(Ordering::Relaxed) => {
                let action = if key_state == 0 {
                    KeyState::Released
                } else {
                    KeyState::Pressed
                };
                kbd.input_forward(
                    state,
                    Keycode::new(key + 8),
                    action,
                    SERIAL_COUNTER.next_serial(),
                    time,
                    false,
                );
            }
            keyboard::Request::Modifiers {
                mods_depressed,
                mods_latched,
                mods_locked,
                group,
            } if binding.has_keymap.load(Ordering::Relaxed) => {
                if let Some(focus) = kbd.current_focus() {
                    let mut mods = kbd.modifier_state();
                    mods.serialized.depressed = mods_depressed;
                    mods.serialized.latched = mods_latched;
                    mods.serialized.locked = mods_locked;
                    mods.serialized.layout_effective = group;
                    focus.modifiers(
                        &state._seat.clone(),
                        state,
                        mods,
                        SERIAL_COUNTER.next_serial(),
                    );
                }
            }
            _ => {}
        }
    }
}
