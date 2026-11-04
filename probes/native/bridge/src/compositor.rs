//! Android上で最小featureのlink・初期化を検証する。SHM fault耐性は別試験。
use serde_json::{Value, json};
use smithay::{
    delegate_shm,
    reexports::wayland_server::{Display, protocol::wl_buffer::WlBuffer},
    wayland::{
        buffer::BufferHandler,
        shm::{ShmHandler, ShmState},
    },
};
use xkbcommon::xkb;

struct State {
    shm: ShmState,
}
impl BufferHandler for State {
    fn buffer_destroyed(&mut self, _: &WlBuffer) {}
}
impl ShmHandler for State {
    fn shm_state(&self) -> &ShmState {
        &self.shm
    }
}
delegate_shm!(State);

pub fn run() -> Result<Value, Box<dyn std::error::Error>> {
    let mut display = Display::<State>::new()?;
    let mut state = State {
        shm: ShmState::new::<State>(&display.handle(), vec![]),
    };
    display.dispatch_clients(&mut state)?;
    display.flush_clients()?;
    let context = xkb::Context::new(xkb::CONTEXT_NO_DEFAULT_INCLUDES);
    // Androidにsystem xkeyboard-configがあるとは仮定しない。単一keyを含む自己完結fixture。
    let source = r#"xkb_keymap {
        xkb_keycodes { minimum = 8; maximum = 255; <AC01> = 38; };
        xkb_types { type "ONE_LEVEL" { modifiers = None; level_name[Level1] = "Any"; }; };
        xkb_compatibility {};
        xkb_symbols { key <AC01> { [ a ] }; };
    };"#;
    let keymap = xkb::Keymap::new_from_string(
        &context,
        source.into(),
        xkb::KEYMAP_FORMAT_TEXT_V1,
        xkb::KEYMAP_COMPILE_NO_FLAGS,
    )
    .ok_or("keymap compile failed")?;
    let keyboard = xkb::State::new(&keymap);
    if keyboard.key_get_utf8(38u32.into()) != "a" {
        return Err("keymap result mismatch".into());
    }
    Ok(
        json!({"pass":true,"smithay":"0.7.0","xkbcommon":"1.8.1","display_created":true,"wl_shm_global":true,"inline_keymap":true,"shm_buffer":"not_run","sigbus_fault":"not_run","gles_adapter":"not_run"}),
    )
}
