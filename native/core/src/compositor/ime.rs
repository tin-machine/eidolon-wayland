//! Android backendのためのbounded text-input-v3/version 1 dispatch。
//! Smithayのinput-method delegateを使わず、編集transaction全体を検査してから送る。
use super::ProtocolState;
use eidolon_model::ime::{ImeRectangle, ImeSnapshot, ImeSurrounding, ImeTarget, ImeTransaction};
use serde::Serialize;
use smithay::reexports::{
    wayland_protocols::wp::text_input::zv3::server::{
        zwp_text_input_manager_v3::{self, ZwpTextInputManagerV3},
        zwp_text_input_v3::{self, ZwpTextInputV3},
    },
    wayland_server::{
        Client, DataInit, Dispatch, DisplayHandle, GlobalDispatch, New, Resource,
        backend::{
            ClientId, GlobalId, Handle, ObjectData, ObjectId,
            protocol::{Argument, Message},
        },
        protocol::wl_surface::WlSurface,
    },
};
use std::{os::fd::OwnedFd, sync::Arc};

const MAX_TEXT_INPUTS: usize = 64;
// CLIENTS=4で一clientが他clientの枠を占有しない。global上限は別に維持する。
const MAX_TEXT_INPUTS_PER_CLIENT: usize = 16;
const SERIAL_HALF_RANGE: u32 = 1 << 31;

fn serial_in_revision(first: u32, current: u32, candidate: u32) -> bool {
    let span = current.wrapping_sub(first);
    span < SERIAL_HALF_RANGE && candidate.wrapping_sub(first) <= span
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct ImeReport {
    pub backend_enabled: bool,
    pub objects: usize,
    pub active: bool,
    pub activation: u64,
    pub edit_revision: u64,
    pub delivered: u64,
    pub rejected: u64,
    pub resets: u64,
}

#[derive(Clone, Default)]
struct Properties {
    surrounding: Option<ImeSurrounding>,
    content_hint: u32,
    content_purpose: u32,
    rectangle: Option<ImeRectangle>,
    change_cause: u32,
}

struct TextInput {
    resource: ZwpTextInputV3,
    serial: u32,
    current: Properties,
    pending: Properties,
    pending_enabled: Option<bool>,
    pending_surrounding: bool,
    surrounding_fresh: bool,
}

#[derive(Default)]
pub(super) struct ImeState {
    backend_enabled: bool,
    focus: Option<WlSurface>,
    instances: Vec<TextInput>,
    active: Option<ObjectId>,
    activation: u64,
    edit_revision: u64,
    revision_serial: u32,
    exhausted: bool,
    sequence: u64,
    preedit: bool,
    report: ImeReport,
}

impl ImeState {
    fn invalidate(&mut self) {
        if let Some(next) = self.activation.checked_add(1) {
            self.activation = next;
        } else {
            self.exhausted = true;
        }
        self.sequence = 0;
        self.preedit = false;
        self.advance_edit_revision();
        self.report.resets += 1;
    }

    fn advance_edit_revision(&mut self) {
        if let Some(next) = self.edit_revision.checked_add(1) {
            self.edit_revision = next;
        } else {
            self.exhausted = true;
        }
        self.revision_serial = self.active().map_or(0, |input| input.serial);
    }

    fn accepts_serial(&self, serial: u32) -> bool {
        self.active()
            .is_some_and(|active| serial_in_revision(self.revision_serial, active.serial, serial))
    }

    fn active(&self) -> Option<&TextInput> {
        let focus = self.focus.as_ref().filter(|s| s.is_alive())?;
        self.instances.iter().find(|instance| {
            Some(&instance.resource.id()) == self.active.as_ref()
                && instance.resource.is_alive()
                && instance.resource.id().same_client_as(&focus.id())
        })
    }

    fn cancel_preedit(&mut self) {
        if self.preedit
            && let Some(active) = self.active()
        {
            active.resource.preedit_string(None, 0, 0);
            active.resource.done(active.serial);
        }
        self.preedit = false;
    }
}

pub(super) fn create_global(dh: &DisplayHandle) -> GlobalId {
    dh.create_global::<ProtocolState, ZwpTextInputManagerV3, _>(1, ())
}

impl ProtocolState {
    /// 採用gate前はfalse。通常キー経路やinput-method-v2 globalを変更しない。
    pub fn set_ime_backend_enabled(&mut self, enabled: bool) {
        if self.ime.backend_enabled == enabled {
            return;
        }
        self.ime.cancel_preedit();
        self.ime.invalidate();
        self.ime.backend_enabled = enabled;
    }

    /// 同じWayland入力欄のInputConnectionだけを作り直す際にも旧queueを失効させる。
    /// ownerで呼出完了後のsnapshotを取得してから新editorをbindする。
    pub fn invalidate_ime_connection(&mut self) {
        self.ime.cancel_preedit();
        self.ime.invalidate();
    }

    pub fn ime_report(&self) -> ImeReport {
        ImeReport {
            backend_enabled: self.ime.backend_enabled,
            objects: self.ime.instances.len(),
            active: self.ime.active().is_some(),
            activation: self.ime.activation,
            edit_revision: self.ime.edit_revision,
            ..self.ime.report.clone()
        }
    }

    /// 通常snapshot/logへ載せない。Android editor専用の所有済みsnapshot。
    pub fn ime_snapshot(&self, input_epoch: u64) -> Option<ImeSnapshot> {
        if !self.ime.backend_enabled || self.ime.exhausted {
            return None;
        }
        let active = self.ime.active()?;
        let focus = self.ime.focus.as_ref()?;
        Some(ImeSnapshot {
            target: ImeTarget {
                session: self.session,
                window: self.input.report.focus_window?,
                surface: self.surface_meta.get(&focus.id())?.id,
                focus_token: self.input.report.focus_token?,
                input_epoch,
                activation: self.ime.activation,
                serial: active.serial,
            },
            edit_revision: self.ime.edit_revision,
            last_sequence: self.ime.sequence,
            surrounding: active
                .surrounding_fresh
                .then(|| active.current.surrounding.clone())
                .flatten(),
            content_hint: active.current.content_hint,
            content_purpose: active.current.content_purpose,
            cursor_rectangle: active.current.rectangle,
            change_cause: active.current.change_cause,
        })
    }

    /// runtimeの最新epochを受け、入力全体を検査してから一つのdoneで送出する。
    pub fn apply_ime(
        &mut self,
        input_epoch: u64,
        transaction: &ImeTransaction,
    ) -> Result<(), &'static str> {
        let result = self.check_ime(input_epoch, transaction);
        if let Err(error) = result {
            self.ime.report.rejected += 1;
            return Err(error);
        }
        let active = self.ime.active().ok_or("STALE")?;
        let resource = active.resource.clone();
        let serial = active.serial;
        // この点より前にprotocol eventを送らない。順序・UTF/range失敗で部分送信しない。
        if transaction.delete_before != 0 || transaction.delete_after != 0 {
            resource.delete_surrounding_text(transaction.delete_before, transaction.delete_after);
        }
        if let Some(text) = &transaction.commit {
            resource.commit_string(Some(text.clone()));
        }
        if let Some(preedit) = &transaction.preedit {
            resource.preedit_string(
                Some(preedit.text.clone()),
                preedit.cursor_begin,
                preedit.cursor_end,
            );
        }
        resource.done(serial);
        self.ime.sequence = transaction.sequence;
        self.ime.preedit = transaction
            .preedit
            .as_ref()
            .is_some_and(|p| !p.text.is_empty());
        // クライアントが新しい周辺snapshotをcommitするまで編集済み文字を再利用しない。
        if (transaction.commit.is_some()
            || transaction.delete_before != 0
            || transaction.delete_after != 0
            || transaction.preedit.is_some())
            && let Some(instance) = self
                .ime
                .instances
                .iter_mut()
                .find(|i| i.resource == resource)
        {
            instance.surrounding_fresh = false;
        }
        self.ime.report.delivered += 1;
        Ok(())
    }

    fn check_ime(
        &self,
        input_epoch: u64,
        transaction: &ImeTransaction,
    ) -> Result<(), &'static str> {
        if !transaction.valid() {
            return Err("INVALID_ARGUMENT");
        }
        let snapshot = self.ime_snapshot(input_epoch).ok_or("NOT_READY")?;
        // wire done serialは常に適用時の最新値。payload自体は変更・再送しない。
        // 同じ編集revisionの既知serial区間だけを許し、geometry更新との競合を避ける。
        let mut identity = transaction.target;
        identity.serial = snapshot.target.serial;
        if identity != snapshot.target
            || transaction.edit_revision != snapshot.edit_revision
            || transaction.sequence <= snapshot.last_sequence
            || !self.ime.accepts_serial(transaction.target.serial)
        {
            return Err("STALE");
        }
        if !transaction.valid_deletion(snapshot.surrounding.as_ref()) {
            return Err("INVALID_ARGUMENT");
        }
        Ok(())
    }

    /// SeatHandlerはkeyboard内部lock中にも呼ばれる。keyboardを再lockしない。
    pub(super) fn ime_new_focus(&mut self, focus: Option<&WlSurface>) {
        if self.ime.focus.as_ref() == focus {
            return;
        }
        if let Some(old) = self.ime.focus.take() {
            for input in &mut self.ime.instances {
                if input.resource.id().same_client_as(&old.id()) {
                    if old.is_alive() && input.resource.is_alive() {
                        input.resource.leave(&old);
                    }
                    input.current = Properties::default();
                    input.pending = Properties::default();
                    input.pending_enabled = None;
                    input.pending_surrounding = false;
                    input.surrounding_fresh = false;
                }
            }
        }
        self.ime.active = None;
        self.ime.invalidate();
        self.ime.focus = focus.cloned();
        if let Some(surface) = focus {
            for input in &self.ime.instances {
                if input.resource.id().same_client_as(&surface.id()) {
                    input.resource.enter(surface);
                }
            }
        }
    }

    pub(super) fn ime_surface_destroyed(&mut self, surface: &WlSurface) {
        if self.ime.focus.as_ref() == Some(surface) {
            self.ime_new_focus(None);
        }
    }
}

impl GlobalDispatch<ZwpTextInputManagerV3, ()> for ProtocolState {
    fn bind(
        _: &mut Self,
        _: &DisplayHandle,
        _: &Client,
        resource: New<ZwpTextInputManagerV3>,
        _: &(),
        data_init: &mut DataInit<'_, Self>,
    ) {
        data_init.init(resource, ());
    }
}

impl Dispatch<ZwpTextInputManagerV3, ()> for ProtocolState {
    fn request(
        state: &mut Self,
        _: &Client,
        manager: &ZwpTextInputManagerV3,
        request: zwp_text_input_manager_v3::Request,
        _: &(),
        _: &DisplayHandle,
        data_init: &mut DataInit<'_, Self>,
    ) {
        if let zwp_text_input_manager_v3::Request::GetTextInput { id, seat } = request {
            let resource = data_init.custom_init(id, Arc::new(StrictTextInput));
            let client_objects = state
                .ime
                .instances
                .iter()
                .filter(|input| input.resource.id().same_client_as(&resource.id()))
                .count();
            if !state._seat.owns(&seat)
                || client_objects >= MAX_TEXT_INPUTS_PER_CLIENT
                || state.ime.instances.len() >= MAX_TEXT_INPUTS
            {
                state.ime.report.rejected += 1;
                manager.post_error(0u32, "invalid seat or text-input resource limit");
                return;
            }
            if let Some(focus) = &state.ime.focus
                && resource.id().same_client_as(&focus.id())
            {
                resource.enter(focus);
            }
            state.ime.instances.push(TextInput {
                resource,
                serial: 0,
                current: Properties::default(),
                pending: Properties::default(),
                pending_enabled: None,
                pending_surrounding: false,
                surrounding_fresh: false,
            });
        }
    }
}

fn text_input_request(
    state: &mut ProtocolState,
    resource: &ZwpTextInputV3,
    request: zwp_text_input_v3::Request,
) {
    let Some(index) = state
        .ime
        .instances
        .iter()
        .position(|i| i.resource == *resource)
    else {
        return;
    };
    if matches!(request, zwp_text_input_v3::Request::Commit) {
        state.ime.instances[index].serial = state.ime.instances[index].serial.wrapping_add(1);
    }
    if !state
        .ime
        .focus
        .as_ref()
        .is_some_and(|focus| focus.is_alive() && resource.id().same_client_as(&focus.id()))
    {
        return;
    }
    let input = &mut state.ime.instances[index];
    let invalid = match request {
        zwp_text_input_v3::Request::Enable => {
            input.pending = Properties::default();
            input.pending_enabled = Some(true);
            input.pending_surrounding = false;
            false
        }
        zwp_text_input_v3::Request::Disable => {
            input.pending_enabled = Some(false);
            false
        }
        zwp_text_input_v3::Request::SetSurroundingText {
            text,
            cursor,
            anchor,
        } => {
            let value = ImeSurrounding {
                text,
                cursor: cursor as u32,
                anchor: anchor as u32,
            };
            if cursor < 0 || anchor < 0 || !value.valid() {
                true
            } else {
                input.pending.surrounding = Some(value);
                input.pending_surrounding = true;
                false
            }
        }
        zwp_text_input_v3::Request::SetTextChangeCause { cause } => match cause.into_result() {
            Ok(value) => {
                input.pending.change_cause = value as u32;
                false
            }
            Err(_) => true,
        },
        zwp_text_input_v3::Request::SetContentType { hint, purpose } => {
            match (hint.into_result(), purpose.into_result()) {
                (Ok(hint), Ok(purpose)) if hint.bits() & !0x3ff == 0 => {
                    input.pending.content_hint = hint.bits();
                    input.pending.content_purpose = purpose as u32;
                    false
                }
                _ => true,
            }
        }
        zwp_text_input_v3::Request::SetCursorRectangle {
            x,
            y,
            width,
            height,
        } => {
            let value = ImeRectangle {
                x,
                y,
                width,
                height,
            };
            if value.valid() {
                input.pending.rectangle = Some(value);
                false
            } else {
                true
            }
        }
        zwp_text_input_v3::Request::Commit => {
            let enabled = input.pending_enabled.take();
            let semantic_change = input.pending.surrounding != input.current.surrounding
                || input.pending.content_hint != input.current.content_hint
                || input.pending.content_purpose != input.current.content_purpose
                || input.pending.change_cause == 1;
            if state
                .ime
                .active
                .as_ref()
                .is_some_and(|id| id != &resource.id())
            {
                // 他のobjectは同じseatの有効入力をdisable/変更できない。
                input.pending = input.current.clone();
                input.pending.change_cause = 0;
                input.pending_surrounding = false;
                return;
            }
            if enabled == Some(false) {
                state.ime.cancel_preedit();
                state.ime.active = None;
                state.ime.invalidate();
                let input = &mut state.ime.instances[index];
                input.current = Properties::default();
                input.pending = Properties::default();
                input.pending_surrounding = false;
                input.surrounding_fresh = false;
                return;
            }
            if enabled == Some(true) {
                state.ime.cancel_preedit();
                state.ime.active = Some(resource.id());
                state.ime.invalidate();
                state.ime.instances[index].surrounding_fresh = false;
            } else if state.ime.active.as_ref() != Some(&resource.id()) {
                return;
            } else if semantic_change
                || state.ime.instances[index]
                    .serial
                    .wrapping_sub(state.ime.revision_serial)
                    >= SERIAL_HALF_RANGE
            {
                // 2^31を越すserial区間は比較が曖昧になるため、geometryだけでも失効。
                state.ime.advance_edit_revision();
            }
            let input = &mut state.ime.instances[index];
            input.current = input.pending.clone();
            input.pending.change_cause = 0;
            input.surrounding_fresh |= input.pending_surrounding;
            input.pending_surrounding = false;
            false
        }
        zwp_text_input_v3::Request::Destroy => false,
        _ => true,
    };
    if invalid {
        state.ime.report.rejected += 1;
        resource.post_error(0u32, "invalid text-input state");
    }
}

#[cfg(any(test, feature = "debug-fixtures"))]
impl ProtocolState {
    /// wrap境界のsynthetic counter seed。4G回のclient requestを実行した証拠にはしない。
    pub(super) fn ime_fixture_rebase_serial(&mut self, serial: u32) {
        let active = self.ime.active.clone();
        if let Some(input) = self
            .ime
            .instances
            .iter_mut()
            .find(|input| Some(&input.resource.id()) == active.as_ref())
        {
            input.serial = serial;
            self.ime.revision_serial = serial;
        }
    }
}

// wayland-scanner 0.31はstringをlossy decodeする。変換前のwire bytesをここで検査する。
// text-input-v3にはnew_id requestがないのでchild resourceの初期化は不要。
struct StrictTextInput;
impl ObjectData<ProtocolState> for StrictTextInput {
    fn request(
        self: Arc<Self>,
        handle: &Handle,
        state: &mut ProtocolState,
        _: ClientId,
        message: Message<ObjectId, OwnedFd>,
    ) -> Option<Arc<dyn ObjectData<ProtocolState>>> {
        let dh = DisplayHandle::from(handle.clone());
        let resource = ZwpTextInputV3::from_id(&dh, message.sender_id.clone()).ok()?;
        if message.args.iter().any(|arg| {
            matches!(arg, Argument::Str(Some(text)) if std::str::from_utf8(text.as_bytes()).is_err())
        }) {
            state.ime.report.rejected += 1;
            resource.post_error(0u32, "invalid UTF-8 text-input state");
            return None;
        }
        match ZwpTextInputV3::parse_request(&dh, message) {
            Ok((_, request)) => text_input_request(state, &resource, request),
            Err(_) => {
                state.ime.report.rejected += 1;
                resource.post_error(0u32, "invalid text-input request");
            }
        }
        None
    }

    fn destroyed(
        self: Arc<Self>,
        _: &Handle,
        state: &mut ProtocolState,
        _: ClientId,
        object: ObjectId,
    ) {
        state.ime.instances.retain(|i| i.resource.id() != object);
        if state.ime.active.as_ref() == Some(&object) {
            state.ime.active = None;
            state.ime.invalidate();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::serial_in_revision;

    #[test]
    fn serial_window_accepts_wrap_and_rejects_future_past_and_ambiguous_ranges() {
        for accepted in [u32::MAX - 1, u32::MAX, 0] {
            assert!(serial_in_revision(u32::MAX - 1, 0, accepted));
        }
        assert!(!serial_in_revision(u32::MAX - 1, 0, 1));
        assert!(!serial_in_revision(u32::MAX - 1, 0, u32::MAX - 2));
        assert!(!serial_in_revision(0, 1 << 31, 0));
        assert!(serial_in_revision(10, 10, 10));
        assert!(!serial_in_revision(10, 10, 9));
        assert!(!serial_in_revision(10, 10, 11));
    }
}
