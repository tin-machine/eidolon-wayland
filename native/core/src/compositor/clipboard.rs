//! UTF-8 selection。本文はsnapshot/logへ出さない。
use super::ProtocolState;
use crate::clipboard_shelf::{ClipboardGrant, ClipboardPermit};
use eidolon_model::ids::{ClipboardCardId, CommandSeq, WindowId};
use serde::Serialize;
use smithay::{
    reexports::wayland_server::{
        Client, DataInit, Dispatch, DisplayHandle, Resource,
        backend::{ClientId, ObjectId},
        protocol::{
            wl_data_device::WlDataDevice,
            wl_data_offer::{self, WlDataOffer},
            wl_data_source::{self, WlDataSource},
            wl_surface::WlSurface,
        },
    },
    wayland::selection::data_device::{DataDeviceState, DataSourceUserData, with_source_metadata},
};
use std::{
    collections::VecDeque,
    fs::File,
    io::{Read, Write},
    os::fd::{AsFd, AsRawFd, OwnedFd},
    sync::mpsc::{Receiver, SyncSender, sync_channel},
    time::{Duration, Instant},
};

const LIMIT: usize = 1024 * 1024;
const TIMEOUT: Duration = Duration::from_secs(5);
const MAX_TRANSFERS: usize = 4;
const SHELF_MIMES: [&str; 2] = ["text/plain;charset=utf-8", "text/plain"];

pub type SelectionCapture = Receiver<Result<Vec<u8>, &'static str>>;
fn text_mime(mime: &str) -> bool {
    matches!(
        mime,
        "text/plain" | "text/plain;charset=utf-8" | "text/plain;charset=UTF-8"
    )
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct ClipboardReport {
    pub selection: bool,
    pub active: usize,
    pub completed: u64,
    pub rejected: u64,
    pub oversized: u64,
    pub invalid_utf8: u64,
    pub timed_out: u64,
    pub cancelled: u64,
}
pub(super) struct OfferData {
    generation: u64,
    focus: u64,
    shelf: Option<ClipboardCardId>,
}
struct Ticket {
    serial: u32,
    surface: WlSurface,
    when: Instant,
    button: bool,
}
struct Transfer {
    offer: ObjectId,
    input: Option<File>,
    output: Option<File>,
    bytes: Vec<u8>,
    offset: usize,
    eof: bool,
    draining: bool,
    deadline: Instant,
    shelf_permit: Option<ClipboardPermit>,
}

struct ShelfCapture {
    input: File,
    bytes: Vec<u8>,
    deadline: Instant,
    permit: ClipboardPermit,
    complete: SyncSender<Result<Vec<u8>, &'static str>>,
}

impl ShelfCapture {
    fn poll(&mut self) -> Option<Result<Vec<u8>, &'static str>> {
        let now = Instant::now();
        if now >= self.deadline {
            return Some(Err("TIMEOUT"));
        }
        if !self.permit.is_live(now) {
            return Some(Err("CANCELLED"));
        }
        let mut budget = 64 * 1024;
        while budget > 0 {
            let mut chunk = [0; 8192];
            match self.input.read(&mut chunk[..budget.min(8192)]) {
                Ok(0) if std::str::from_utf8(&self.bytes).is_err() => {
                    return Some(Err("INVALID_UTF8"));
                }
                Ok(0) => return Some(Ok(std::mem::take(&mut self.bytes))),
                Ok(n) if self.bytes.len() + n > LIMIT => return Some(Err("TOO_LARGE")),
                Ok(n) => {
                    budget -= n;
                    self.bytes.extend_from_slice(&chunk[..n]);
                }
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => return None,
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => return None,
                Err(_) => return Some(Err("TRANSFER_FAILED")),
            }
        }
        None
    }
}

#[derive(Default)]
pub(super) struct Clipboard {
    focused_client: Option<ClientId>,
    owner: Option<WlDataSource>,
    owner_root: Option<ObjectId>,
    mimes: Vec<String>,
    generation: u64,
    focus: u64,
    offers: Vec<WlDataOffer>,
    tickets: VecDeque<Ticket>,
    transfers: Vec<Transfer>,
    shelf_capture: Option<ShelfCapture>,
    shelf_grant: Option<ClipboardGrant>,
    report: ClipboardReport,
}
fn nonblocking(fd: &OwnedFd) -> bool {
    // SAFETY: fcntl/fstatは有効なOwnedFdに対して同期的に実行する。
    unsafe {
        let mut info = std::mem::zeroed::<libc::stat>();
        if libc::fstat(fd.as_raw_fd(), &mut info) != 0
            || !matches!(info.st_mode & libc::S_IFMT, libc::S_IFIFO | libc::S_IFSOCK)
        {
            return false;
        }
        let flags = libc::fcntl(fd.as_raw_fd(), libc::F_GETFL);
        flags >= 0 && libc::fcntl(fd.as_raw_fd(), libc::F_SETFL, flags | libc::O_NONBLOCK) >= 0
    }
}
impl ProtocolState {
    pub fn clipboard_report(&self) -> ClipboardReport {
        ClipboardReport {
            selection: self.clipboard.owner.is_some() || self.clipboard.shelf_grant.is_some(),
            active: self.clipboard.transfers.len()
                + usize::from(self.clipboard.shelf_capture.is_some()),
            ..self.clipboard.report.clone()
        }
    }
    pub(super) fn input_serial(&mut self, serial: u32, surface: WlSurface, button: bool) {
        if self.clipboard.tickets.len() == 64 {
            self.clipboard.tickets.pop_front();
        }
        self.clipboard.tickets.push_back(Ticket {
            serial,
            surface,
            when: Instant::now(),
            button,
        });
    }
    pub(super) fn valid_input_serial(&self, serial: u32, client: &Client, button: bool) -> bool {
        let focus = self._seat.get_keyboard().and_then(|k| k.current_focus());
        focus.is_some_and(|s| s.client().is_some_and(|c| c.id() == client.id()))
            && self.clipboard.tickets.iter().rev().any(|t| {
                t.serial == serial
                    && (!button || t.button)
                    && t.when.elapsed() < TIMEOUT
                    && t.surface.client().is_some_and(|c| c.id() == client.id())
            })
    }
    pub(super) fn popup_serial_parent(&self, serial: u32, parent: &WlSurface) -> bool {
        self.clipboard
            .tickets
            .iter()
            .rev()
            .find(|t| t.serial == serial && t.button)
            .is_some_and(|t| self.root_for_surface(&t.surface) == self.root_for_surface(parent))
    }
    pub(super) fn selection_serial_root(&self, serial: u32, client: &Client) -> Option<ObjectId> {
        let ticket = self.clipboard.tickets.iter().rev().find(|ticket| {
            ticket.serial == serial
                && ticket.when.elapsed() < TIMEOUT
                && ticket
                    .surface
                    .client()
                    .is_some_and(|owner| owner.id() == client.id())
        })?;
        self.root_for_surface(&ticket.surface)
    }

    /// 取得元を単なるclient IDで推測せず、selectionを設定した入力serialの窓を照合する。
    pub fn capture_selection(
        &mut self,
        window: WindowId,
        focus_token: CommandSeq,
        permit: ClipboardPermit,
    ) -> Result<SelectionCapture, &'static str> {
        if !permit.is_live(Instant::now()) {
            return Err("CANCELLED");
        }
        if self.input.report.focus_window != Some(window)
            || self.input.report.focus_token != Some(focus_token)
        {
            return Err("STALE");
        }
        let focus = self
            ._seat
            .get_keyboard()
            .and_then(|keyboard| keyboard.current_focus());
        let focused_root = focus
            .as_ref()
            .and_then(|surface| self.root_for_surface(surface));
        let clipboard = &mut self.clipboard;
        if clipboard.shelf_grant.is_some() {
            return Err("SHELF_SELECTION");
        }
        let Some(source) = clipboard.owner.as_ref().filter(|source| source.is_alive()) else {
            return Err("NO_SELECTION");
        };
        if focused_root.is_none()
            || clipboard.owner_root != focused_root
            || source.client().map(|client| client.id()) != clipboard.focused_client
        {
            return Err("STALE");
        }
        if clipboard.shelf_capture.is_some() || clipboard.transfers.len() >= MAX_TRANSFERS {
            return Err("BUSY");
        }
        let mime = clipboard
            .mimes
            .iter()
            .find(|mime| text_mime(mime))
            .ok_or("NOT_TEXT")?;
        let (read, write) =
            nix::unistd::pipe2(nix::fcntl::OFlag::O_CLOEXEC).map_err(|_| "TRANSFER_FAILED")?;
        if !nonblocking(&read) {
            return Err("TRANSFER_FAILED");
        }
        let (complete, result) = sync_channel(1);
        source.send(mime.clone(), write.as_fd());
        clipboard.shelf_capture = Some(ShelfCapture {
            input: read.into(),
            bytes: Vec::new(),
            deadline: Instant::now() + TIMEOUT,
            permit,
            complete,
        });
        Ok(result)
    }

    /// 利用者が選んだ入力先だけに提示し、同一Displayの通常selectionは維持する。
    pub fn grant_shelf_clipboard(
        &mut self,
        window: WindowId,
        focus_token: CommandSeq,
        grant: ClipboardGrant,
    ) -> Result<(), &'static str> {
        if self.input.report.focus_window != Some(window)
            || self.input.report.focus_token != Some(focus_token)
            || self.clipboard.focused_client.is_none()
        {
            return Err("STALE");
        }
        if grant.text.is_empty()
            || grant.text.len() > LIMIT
            || !grant.permit.is_live(Instant::now())
        {
            return Err("INVALID");
        }
        self.cancel_shelf_capture();
        self.cancel_clipboard_transfers();
        self.advance_clipboard_generation();
        self.clipboard.shelf_grant = Some(grant);
        self.clipboard_focus(&self.display_handle.clone());
        Ok(())
    }

    pub(super) fn advertise_clipboard(&mut self, dh: &DisplayHandle, device: &WlDataDevice) {
        // SeatHandler callbackはkeyboard内部lock中にも届く。keyboardを再取得しない。
        let focused = device
            .client()
            .is_some_and(|c| Some(c.id()) == self.clipboard.focused_client);
        if !focused {
            return;
        }
        let shelf = self.clipboard.shelf_grant.as_ref().map(|grant| grant.card);
        if self.clipboard.owner.is_none() && shelf.is_none() {
            device.selection(None);
            return;
        }
        self.clipboard.offers.retain(|o| o.is_alive());
        if self.clipboard.offers.len() >= 64 {
            device.selection(None);
            self.clipboard.report.rejected += 1;
            return;
        }
        let Some(client) = device.client() else {
            return;
        };
        // stale offerもclientがdestroyするまで生きる。全体64個で上限を設ける。
        let Ok(offer) = client.create_resource::<WlDataOffer, _, Self>(
            dh,
            device.version(),
            OfferData {
                generation: self.clipboard.generation,
                focus: self.clipboard.focus,
                shelf,
            },
        ) else {
            return;
        };
        device.data_offer(&offer);
        if shelf.is_some() {
            for mime in SHELF_MIMES {
                offer.offer(mime.to_owned());
            }
        } else {
            for mime in &self.clipboard.mimes {
                offer.offer(mime.clone());
            }
        }
        device.selection(Some(&offer));
        self.clipboard.offers.push(offer);
    }
    pub(super) fn clipboard_new_focus(&mut self, focus: Option<&WlSurface>) {
        self.cancel_shelf_capture();
        self.clipboard.shelf_grant = None;
        self.clipboard.focused_client = focus.and_then(|s| s.client()).map(|c| c.id());
        self.clipboard.tickets.clear();
        self.clipboard.focus = self
            .clipboard
            .focus
            .checked_add(1)
            .expect("clipboard focus");
        self.cancel_clipboard_transfers();
    }
    pub(super) fn clipboard_focus(&mut self, dh: &DisplayHandle) {
        for device in self.data_devices.values().cloned().collect::<Vec<_>>() {
            self.advertise_clipboard(dh, &device);
        }
    }
    fn cancel_shelf_capture(&mut self) {
        if let Some(capture) = self.clipboard.shelf_capture.take() {
            let _ = capture.complete.try_send(Err("STALE"));
        }
    }

    fn cancel_clipboard_transfers(&mut self) {
        self.clipboard.report.cancelled += self.clipboard.transfers.len() as u64;
        self.clipboard.transfers.clear();
    }

    fn advance_clipboard_generation(&mut self) {
        self.clipboard.generation = self
            .clipboard
            .generation
            .checked_add(1)
            .expect("clipboard generation");
    }

    pub(super) fn set_clipboard(
        &mut self,
        dh: &DisplayHandle,
        source: Option<WlDataSource>,
        root: Option<ObjectId>,
    ) {
        self.cancel_shelf_capture();
        if let Some(old) = self.clipboard.owner.take()
            && old.is_alive()
            && source.as_ref() != Some(&old)
        {
            old.cancelled();
            self.counters.cancellations += 1;
        }
        self.cancel_clipboard_transfers();
        self.clipboard.shelf_grant = None;
        self.advance_clipboard_generation();
        self.clipboard.mimes = source
            .as_ref()
            .and_then(|s| with_source_metadata(s, |m| m.mime_types.clone()).ok())
            .unwrap_or_default();
        self.clipboard.owner_root = root.filter(|_| source.is_some());
        self.clipboard.owner = source;
        self.clipboard_focus(dh);
    }
    pub(super) fn clipboard_tick(&mut self, dh: &DisplayHandle) {
        if self
            .clipboard
            .shelf_grant
            .as_ref()
            .is_some_and(|grant| !grant.permit.is_live(Instant::now()))
        {
            self.clipboard.shelf_grant = None;
            self.cancel_clipboard_transfers();
            self.advance_clipboard_generation();
            self.clipboard_focus(dh);
        }
        if self.clipboard.owner.as_ref().is_some_and(|s| !s.is_alive()) {
            self.set_clipboard(dh, None, None);
        }
        if let Some(mut capture) = self.clipboard.shelf_capture.take() {
            if let Some(result) = capture.poll() {
                let _ = capture.complete.try_send(result);
            } else {
                self.clipboard.shelf_capture = Some(capture);
            }
        }
        self.clipboard.offers.retain(|o| o.is_alive());
        self.clipboard
            .tickets
            .retain(|t| t.surface.is_alive() && t.when.elapsed() < TIMEOUT);
        let report = &mut self.clipboard.report;
        self.clipboard.transfers.retain_mut(|t| {
            let now = Instant::now();
            if t.shelf_permit
                .as_ref()
                .is_some_and(|permit| !permit.is_live(now))
            {
                report.cancelled += 1;
                return false;
            }
            if now >= t.deadline {
                report.timed_out += 1;
                return false;
            }
            // 各transfer一tick最大64KiB。最大4本でprotocol ownerを占有しない。
            let mut budget = 64 * 1024;
            while !t.eof && budget > 0 {
                let mut chunk = [0; 8192];
                match t
                    .input
                    .as_mut()
                    .expect("client transfer input")
                    .read(&mut chunk[..budget.min(8192)])
                {
                    Ok(0) => {
                        t.eof = true;
                        break;
                    }
                    Ok(n) => {
                        budget -= n;
                        if !t.draining {
                            if t.bytes.len() + n > LIMIT {
                                t.draining = true;
                                t.output = None;
                                t.bytes.clear();
                                report.oversized += 1;
                            } else {
                                t.bytes.extend_from_slice(&chunk[..n]);
                            }
                        }
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                    Err(_) => {
                        report.cancelled += 1;
                        return false;
                    }
                }
            }
            if !t.eof {
                return true;
            }
            if t.draining {
                return false;
            }
            if std::str::from_utf8(&t.bytes).is_err() {
                report.invalid_utf8 += 1;
                return false;
            }
            while t.offset < t.bytes.len() && budget > 0 {
                let end = (t.offset + budget).min(t.bytes.len());
                match t.output.as_mut().unwrap().write(&t.bytes[t.offset..end]) {
                    Ok(0) => {
                        report.cancelled += 1;
                        return false;
                    }
                    Ok(n) => {
                        t.offset += n;
                        budget -= n;
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => return true,
                    Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                    Err(_) => {
                        report.cancelled += 1;
                        return false;
                    }
                }
            }
            if t.offset == t.bytes.len() {
                report.completed += 1;
                false
            } else {
                true
            }
        });
    }
}
impl Dispatch<WlDataOffer, OfferData> for ProtocolState {
    fn request(
        state: &mut Self,
        client: &Client,
        offer: &WlDataOffer,
        request: wl_data_offer::Request,
        data: &OfferData,
        _: &DisplayHandle,
        _: &mut DataInit<'_, Self>,
    ) {
        if let wl_data_offer::Request::Receive { mime_type, fd } = request {
            let valid = state.clipboard.generation == data.generation
                && state.clipboard.focus == data.focus
                && text_mime(&mime_type)
                && state
                    ._seat
                    .get_keyboard()
                    .is_some_and(|k| k.client_of_object_has_focus(&offer.id()))
                && offer.client().is_some_and(|c| c.id() == client.id())
                && state.clipboard.transfers.len()
                    + usize::from(state.clipboard.shelf_capture.is_some())
                    < MAX_TRANSFERS
                && nonblocking(&fd);
            if !valid {
                state.clipboard.report.rejected += 1;
                return;
            }
            if let Some(card) = data.shelf {
                let now = Instant::now();
                let grant = state.clipboard.shelf_grant.as_ref().filter(|grant| {
                    grant.card == card
                        && grant.permit.is_live(now)
                        && SHELF_MIMES.contains(&mime_type.as_str())
                });
                if let Some(grant) = grant {
                    state.clipboard.transfers.push(Transfer {
                        offer: offer.id(),
                        input: None,
                        output: Some(fd.into()),
                        bytes: grant.text.as_bytes().to_vec(),
                        offset: 0,
                        eof: true,
                        draining: false,
                        deadline: now + TIMEOUT,
                        shelf_permit: Some(grant.permit.clone()),
                    });
                } else {
                    state.clipboard.report.rejected += 1;
                }
                return;
            }
            if !state.clipboard.mimes.contains(&mime_type)
                || state
                    .clipboard
                    .owner
                    .as_ref()
                    .is_none_or(|source| !source.is_alive())
            {
                state.clipboard.report.rejected += 1;
                return;
            }
            let Ok((read, write)) = nix::unistd::pipe2(nix::fcntl::OFlag::O_CLOEXEC) else {
                state.clipboard.report.rejected += 1;
                return;
            };
            if !nonblocking(&read) {
                state.clipboard.report.rejected += 1;
                return;
            }
            state
                .clipboard
                .owner
                .as_ref()
                .unwrap()
                .send(mime_type, write.as_fd());
            state.clipboard.transfers.push(Transfer {
                offer: offer.id(),
                input: Some(read.into()),
                output: Some(fd.into()),
                bytes: Vec::new(),
                offset: 0,
                eof: false,
                draining: false,
                deadline: Instant::now() + TIMEOUT,
                shelf_permit: None,
            });
        }
    }
    fn destroyed(state: &mut Self, _: ClientId, offer: &WlDataOffer, _: &OfferData) {
        state.clipboard.transfers.retain(|t| {
            if t.offer == offer.id() {
                state.clipboard.report.cancelled += 1;
                false
            } else {
                true
            }
        });
    }
}
impl Dispatch<WlDataSource, DataSourceUserData> for ProtocolState {
    fn request(
        state: &mut Self,
        client: &Client,
        source: &WlDataSource,
        request: wl_data_source::Request,
        data: &DataSourceUserData,
        dh: &DisplayHandle,
        init: &mut DataInit<'_, Self>,
    ) {
        if let wl_data_source::Request::Offer { mime_type } = &request
            && (!text_mime(mime_type)
                || with_source_metadata(source, |m| m.mime_types.contains(mime_type))
                    .unwrap_or(true))
        {
            return;
        }
        <DataDeviceState as Dispatch<WlDataSource, DataSourceUserData, Self>>::request(
            state, client, source, request, data, dh, init,
        );
    }
    fn destroyed(
        state: &mut Self,
        client: ClientId,
        source: &WlDataSource,
        data: &DataSourceUserData,
    ) {
        <DataDeviceState as Dispatch<WlDataSource, DataSourceUserData, Self>>::destroyed(
            state, client, source, data,
        );
    }
}
