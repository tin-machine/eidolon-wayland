//! Wayland IME connection created by the session owner. Ordinary clients use the display listener.
use super::{ClientState, ProtocolState};
use eidolon_model::ime::{ImePreedit, ImeSnapshot, ImeTransaction, valid_text};
use smithay::{
    reexports::{
        wayland_protocols::wp::text_input::zv3::server::zwp_text_input_v3::{
            ChangeCause, ContentHint, ContentPurpose,
        },
        wayland_protocols_misc::zwp_input_method_v2::server::{
            zwp_input_method_keyboard_grab_v2::{self as grab, ZwpInputMethodKeyboardGrabV2},
            zwp_input_method_manager_v2::{self as manager, ZwpInputMethodManagerV2},
            zwp_input_method_v2::{self as method, ZwpInputMethodV2},
            zwp_input_popup_surface_v2::{self as popup, ZwpInputPopupSurfaceV2},
        },
        wayland_server::{
            Client, DataInit, Dispatch, DisplayHandle, GlobalDispatch, New, Resource,
            backend::{ClientId, GlobalId},
            protocol::{wl_keyboard, wl_surface::WlSurface},
        },
    },
    utils::SERIAL_COUNTER,
    wayland::compositor::give_role,
};
use std::os::unix::net::UnixStream;

#[path = "external_ime_keyboard.rs"]
mod keyboard;
pub(super) use keyboard::keyboard_global;

#[derive(Default)]
struct Pending {
    commit: Option<String>,
    preedit: Option<ImePreedit>,
    delete: (u32, u32),
}

#[derive(Default)]
pub(super) struct ExternalIme {
    client: Option<ClientId>,
    method: Option<ZwpInputMethodV2>,
    // The client counts done events for the lifetime of this method, including deactivations.
    serial: u32,
    snapshot: Option<ImeSnapshot>,
    pending: Pending,
    grab: Option<(ZwpInputMethodKeyboardGrabV2, u64)>,
    last_modifiers: Option<smithay::input::keyboard::SerializedMods>,
    popup: Option<CandidatePopup>,
}
pub(super) struct CandidatePopup {
    pub(super) object: ZwpInputPopupSurfaceV2,
    pub(super) surface: WlSurface,
    pub(super) parent: WlSurface,
    pub(super) window: eidolon_model::ids::WindowId,
    activation: u64,
    pub(super) rectangle: Option<eidolon_model::ime::ImeRectangle>,
}
impl ExternalIme {
    pub(super) fn connected(&self, dh: &DisplayHandle) -> bool {
        self.client
            .as_ref()
            .is_some_and(|id| dh.backend_handle().get_client_data(id.clone()).is_ok())
    }
    pub(super) fn attached(&self) -> bool {
        self.method.as_ref().is_some_and(Resource::is_alive)
    }
    fn done(&mut self, im: &ZwpInputMethodV2) {
        im.done();
        self.serial = self.serial.wrapping_add(1);
    }
}

pub(super) fn method_global(dh: &DisplayHandle) -> GlobalId {
    dh.create_global::<ProtocolState, ZwpInputMethodManagerV2, _>(1, ())
}

impl ProtocolState {
    /// Return only the peer of a newly created connection. Never elevate an already accepted guest.
    pub fn open_input_method_connection(&mut self) -> Result<UnixStream, &'static str> {
        if !self.new_roles_open || self._seat.get_keyboard().is_none() {
            return Err("NOT_READY");
        }
        let dh = self.display_handle.clone();
        if self.external_ime.connected(&dh) {
            return Err("IME_ALREADY_CONNECTED");
        }
        let (server, peer) = UnixStream::pair().map_err(|_| "CLIENT_ACCEPT")?;
        let client = self.accept_captured(&dh, server, None, true)?;
        self.external_ime = ExternalIme {
            client: Some(client.id()),
            ..Default::default()
        };
        Ok(peer)
    }
    /// Immediately revoke the privileged peer on owner change, including queued input.
    pub fn close_input_method_connection(&mut self) {
        if let Some(client) = self.external_ime.client.take() {
            self.display_handle.backend_handle().kill_client(
                client,
                smithay::reexports::wayland_server::backend::DisconnectReason::ConnectionClosed,
            );
            self.clear_candidate_popup();
            self.external_ime = ExternalIme::default();
            self.invalidate_ime_connection();
        }
    }
    pub(super) fn sync_external_ime(&mut self) {
        let Some(im) = self
            .external_ime
            .method
            .as_ref()
            .filter(|im| im.is_alive())
            .cloned()
        else {
            return;
        };
        let stamp = self.focused_ime_stamp();
        if self
            .external_ime
            .snapshot
            .as_ref()
            .map(|s| (s.target.activation, s.target.serial))
            == stamp
        {
            return;
        }
        let next = self.focused_ime_snapshot(0);
        let old = self.external_ime.snapshot.take();
        let changed =
            old.as_ref().map(|s| s.target.activation) != next.as_ref().map(|s| s.target.activation);
        if changed {
            self.external_ime.pending = Pending::default();
            self.external_ime.grab = None;
            self.clear_candidate_popup();
            if old.is_some() {
                im.deactivate();
                self.external_ime.done(&im);
            }
        }
        if let Some(snapshot) = &next
            && old.as_ref().is_none_or(|before| {
                changed
                    || before.edit_revision != snapshot.edit_revision
                    || before.surrounding != snapshot.surrounding
                    || before.content_hint != snapshot.content_hint
                    || before.content_purpose != snapshot.content_purpose
                    || before.change_cause != snapshot.change_cause
            })
        {
            if changed {
                im.activate();
            }
            if let Some(s) = &snapshot.surrounding {
                im.surrounding_text(s.text.clone(), s.cursor, s.anchor);
            }
            if let Ok(cause) = ChangeCause::try_from(snapshot.change_cause) {
                im.text_change_cause(cause);
            }
            if let Ok(purpose) = ContentPurpose::try_from(snapshot.content_purpose) {
                im.content_type(
                    ContentHint::from_bits_truncate(snapshot.content_hint),
                    purpose,
                );
            }
            self.external_ime.done(&im);
        }
        let cursor_changed = next.as_ref().and_then(|s| s.cursor_rectangle)
            != old.as_ref().and_then(|s| s.cursor_rectangle);
        self.external_ime.snapshot = next;
        if cursor_changed && let Some(p) = &self.external_ime.popup {
            let root = self.root_for_surface(&p.parent);
            if let Some(root) = root {
                self.refresh_window(&root);
            }
        }
    }
    pub(super) fn candidate_popup(&self) -> Option<&CandidatePopup> {
        self.external_ime.popup.as_ref().filter(|p| {
            self.external_ime_activation() == Some(p.activation) && p.surface.is_alive()
        })
    }
    fn clear_candidate_popup(&mut self) {
        if let Some(p) = self.external_ime.popup.take()
            && let Some(root) = self.root_for_surface(&p.parent)
        {
            self.refresh_window(&root);
        }
    }
    pub(super) fn candidate_caret(&self) -> eidolon_model::ime::ImeRectangle {
        self.external_ime
            .snapshot
            .as_ref()
            .and_then(|s| s.cursor_rectangle)
            .unwrap_or(eidolon_model::ime::ImeRectangle {
                x: 0,
                y: 0,
                width: 0,
                height: 0,
            })
    }
    pub(super) fn update_candidate_rectangle(
        &mut self,
        rectangle: eidolon_model::ime::ImeRectangle,
    ) {
        if let Some(p) = self.external_ime.popup.as_mut()
            && p.rectangle != Some(rectangle)
        {
            p.object.text_input_rectangle(
                rectangle.x,
                rectangle.y,
                rectangle.width,
                rectangle.height,
            );
            p.rectangle = Some(rectangle);
        }
    }
    fn external_ime_activation(&self) -> Option<u64> {
        let activation = self.external_ime.snapshot.as_ref()?.target.activation;
        self.focused_ime_stamp()
            .filter(|(a, _)| *a == activation)
            .map(|(a, _)| a)
    }
    fn external_ime_commit(&mut self, serial: u32) {
        let pending = std::mem::take(&mut self.external_ime.pending);
        if serial != self.external_ime.serial {
            return;
        }
        let Some(s) = &self.external_ime.snapshot else {
            return;
        };
        let Some(seq) = self
            .focused_ime_snapshot(0)
            .and_then(|s| s.last_sequence.checked_add(1))
        else {
            return;
        };
        let tx = ImeTransaction {
            target: s.target,
            edit_revision: s.edit_revision,
            sequence: seq,
            commit: pending.commit,
            preedit: pending.preedit,
            delete_before: pending.delete.0,
            delete_after: pending.delete.1,
        };
        let _ = self.apply_focused_ime(0, &tx);
    }
}

impl GlobalDispatch<ZwpInputMethodManagerV2, ()> for ProtocolState {
    fn can_view(client: Client, _: &()) -> bool {
        client
            .get_data::<ClientState>()
            .is_some_and(|c| c.input_method)
    }
    fn bind(
        _: &mut Self,
        _: &DisplayHandle,
        _: &Client,
        resource: New<ZwpInputMethodManagerV2>,
        _: &(),
        init: &mut DataInit<'_, Self>,
    ) {
        init.init(resource, ());
    }
}
impl Dispatch<ZwpInputMethodManagerV2, ()> for ProtocolState {
    fn request(
        state: &mut Self,
        _: &Client,
        _: &ZwpInputMethodManagerV2,
        req: manager::Request,
        _: &(),
        _: &DisplayHandle,
        init: &mut DataInit<'_, Self>,
    ) {
        if let manager::Request::GetInputMethod { seat, input_method } = req {
            let im = init.init(input_method, ());
            if !state._seat.owns(&seat) || state.external_ime.attached() {
                im.unavailable();
                return;
            }
            state.invalidate_ime_connection();
            state.external_ime.serial = 0;
            state.external_ime.method = Some(im);
            state.sync_external_ime();
        }
    }
}
impl Dispatch<ZwpInputMethodV2, ()> for ProtocolState {
    fn request(
        state: &mut Self,
        _: &Client,
        im: &ZwpInputMethodV2,
        req: method::Request,
        _: &(),
        _: &DisplayHandle,
        init: &mut DataInit<'_, Self>,
    ) {
        let current = state.external_ime.method.as_ref() == Some(im);
        match req {
            method::Request::GetInputPopupSurface { id, surface } => {
                let object = init.init(id, ());
                if !current {
                    return;
                }
                let role = smithay::wayland::input_method::INPUT_POPUP_SURFACE_ROLE;
                let reusable = smithay::wayland::compositor::get_role(&surface) == Some(role)
                    && !state
                        .external_ime
                        .popup
                        .as_ref()
                        .is_some_and(|p| p.surface == surface && p.object.is_alive());
                if !reusable && give_role(&surface, role).is_err() {
                    im.post_error(0u32, "surface already has a role");
                    return;
                }
                if let Some(activation) = state.external_ime_activation()
                    && let Some(parent) = state._seat.get_keyboard().and_then(|k| k.current_focus())
                    && let Some(window) = state.input.report.focus_window
                {
                    state.clear_candidate_popup();
                    state.external_ime.popup = Some(CandidatePopup {
                        object,
                        surface,
                        parent,
                        window,
                        activation,
                        rectangle: None,
                    });
                }
            }
            method::Request::GrabKeyboard { keyboard } => {
                let object = init.init(keyboard, ());
                if current {
                    state.install_ime_grab(object);
                }
            }
            method::Request::CommitString { text } if current => {
                if valid_text(&text) {
                    state.external_ime.pending.commit = Some(text);
                } else {
                    im.post_error(0u32, "invalid commit");
                }
            }
            method::Request::SetPreeditString {
                text,
                cursor_begin,
                cursor_end,
            } if current => {
                let p = ImePreedit {
                    text,
                    cursor_begin,
                    cursor_end,
                };
                if p.valid() {
                    state.external_ime.pending.preedit = Some(p);
                } else {
                    im.post_error(0u32, "invalid preedit");
                }
            }
            method::Request::DeleteSurroundingText {
                before_length,
                after_length,
            } if current => {
                state.external_ime.pending.delete = (before_length, after_length);
            }
            method::Request::Commit { serial } if current => state.external_ime_commit(serial),
            _ => {}
        }
    }
    fn destroyed(state: &mut Self, _: ClientId, im: &ZwpInputMethodV2, _: &()) {
        if state.external_ime.method.as_ref() == Some(im) {
            state.clear_candidate_popup();
            let client = state.external_ime.client.take();
            state.external_ime = ExternalIme {
                client,
                ..Default::default()
            };
            state.invalidate_ime_connection();
        }
    }
}
impl Dispatch<ZwpInputMethodKeyboardGrabV2, ()> for ProtocolState {
    fn request(
        _: &mut Self,
        _: &Client,
        _: &ZwpInputMethodKeyboardGrabV2,
        _: grab::Request,
        _: &(),
        _: &DisplayHandle,
        _: &mut DataInit<'_, Self>,
    ) {
    }
    fn destroyed(state: &mut Self, _: ClientId, obj: &ZwpInputMethodKeyboardGrabV2, _: &()) {
        if state
            .external_ime
            .grab
            .as_ref()
            .is_some_and(|(g, _)| g == obj)
        {
            state.external_ime.grab = None;
        }
    }
}
impl Dispatch<ZwpInputPopupSurfaceV2, ()> for ProtocolState {
    fn request(
        _: &mut Self,
        _: &Client,
        _: &ZwpInputPopupSurfaceV2,
        _: popup::Request,
        _: &(),
        _: &DisplayHandle,
        _: &mut DataInit<'_, Self>,
    ) {
    }
    fn destroyed(state: &mut Self, _: ClientId, obj: &ZwpInputPopupSurfaceV2, _: &()) {
        if state
            .external_ime
            .popup
            .as_ref()
            .is_some_and(|p| &p.object == obj)
        {
            state.clear_candidate_popup();
        }
    }
}
