//! popupは親panel内。明示grabの入力は同じpopup chainへ限定する。
use super::{PopupSurface, ProtocolState, Seat, Serial, WlSeat, WlSurface};
use smithay::reexports::wayland_server::Resource;

impl ProtocolState {
    pub(super) fn grab_popup(&mut self, popup: PopupSurface, seat: WlSeat, serial: Serial) {
        let parent = popup.get_parent_surface();
        let valid = (|| {
            let parent = parent.as_ref()?;
            let client = popup.wl_surface().client()?;
            let keyboard = self._seat.get_keyboard()?;
            let focus = keyboard.current_focus()?;
            let root = self.root_for_surface(parent)?;
            if Seat::<Self>::from_resource(&seat).as_ref() != Some(&self._seat)
                || !self.valid_input_serial(serial.into(), &client, true)
                || self.input_report().pointer.pressed == 0
                || !self.popup_serial_parent(serial.into(), parent)
                || self.root_for_surface(&focus) != Some(root)
                || self.popup_grabs.len() >= 8
            {
                return None;
            }
            if let Some(top) = self.popup_grabs.last() {
                if *top != parent.id() {
                    return None;
                }
            } else if !self.toplevels.contains_key(&parent.id()) {
                return None;
            }
            if self.pixels.contains_key(&popup.wl_surface().id()) {
                return None;
            }
            Some(())
        })()
        .is_some();
        if valid {
            self.popup_grabs.push(popup.wl_surface().id());
        } else {
            self.dismiss_popup_tree(popup.wl_surface());
            if self.dismissed_popups.insert(popup.wl_surface().id()) {
                popup.send_popup_done();
            }
        }
    }
    pub(super) fn popup_allows(&self, surface: &WlSurface) -> bool {
        if self.popup_grabs.is_empty() {
            return true;
        }
        let mut current = surface.clone();
        for _ in 0..=eidolon_model::limits::SURFACES {
            if self.popup_grabs.contains(&current.id()) {
                return true;
            }
            if let Some(parent) = smithay::wayland::compositor::get_parent(&current) {
                current = parent;
            } else {
                return false;
            }
        }
        false
    }
    pub(super) fn dismiss_popup_grab(&mut self, restore: bool) {
        let Some(first) = self.popup_grabs.first().cloned() else {
            return;
        };
        let root = self
            .popups
            .iter()
            .find(|p| p.wl_surface().id() == first)
            .and_then(|p| self.root_for_surface(p.wl_surface()));
        self.popup_grabs.clear();
        if let Some(root) = root
            && let Some(record) = self.toplevels.get(&root)
        {
            let surface = record.surface.wl_surface().clone();
            let window = record.id;
            self.dismiss_popup_tree(&surface);
            self.refresh_window(&surface.id());
            if restore {
                let _ = self.focus_keyboard(window);
            }
        }
    }
    pub(super) fn refresh_popup_focus(&mut self) {
        self.popup_grabs.retain(|id| {
            !self.dismissed_popups.contains(id)
                && self
                    .popups
                    .iter()
                    .any(|p| p.wl_surface().id() == *id && p.wl_surface().is_alive())
        });
        let popup = self.popup_grabs.iter().rev().find_map(|id| {
            self.popups
                .iter()
                .find(|p| p.wl_surface().id() == *id && self.pixels.contains_key(id))
        });
        if let Some(surface) = popup.map(|p| p.wl_surface().clone()) {
            self.popup_focused = Some(surface.clone());
            if let Some(root) = self.root_for_surface(&surface)
                && let Some(record) = self.toplevels.get(&root)
            {
                let _ = self.focus_keyboard_surface(record.id, surface);
            }
        } else if let Some(old) = self.popup_focused.take()
            && let Some(keyboard) = self._seat.get_keyboard()
            && keyboard.current_focus() == Some(old)
            && let Some(window) = self.input.report.focus_window
        {
            let _ = self.focus_keyboard(window);
        }
    }
    pub(super) fn popup_keyboard_surface(
        &self,
        window: eidolon_model::ids::WindowId,
    ) -> Option<WlSurface> {
        let id = self.popup_grabs.last()?;
        let surface = self
            .popups
            .iter()
            .find(|p| p.wl_surface().id() == *id)?
            .wl_surface();
        let root = self.root_for_surface(surface)?;
        self.toplevels
            .get(&root)
            .filter(|r| r.id == window)
            .map(|_| surface.clone())
    }
}
