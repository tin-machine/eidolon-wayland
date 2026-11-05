//! 同じDisplay内だけのUTF-8 selection。本文はsnapshot/logへ出さない。
use super::ProtocolState;
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
    time::{Duration, Instant},
};

const LIMIT: usize = 1024 * 1024;
const TIMEOUT: Duration = Duration::from_secs(5);
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
}
struct Ticket {
    serial: u32,
    surface: WlSurface,
    when: Instant,
    button: bool,
}
struct Transfer {
    offer: ObjectId,
    input: File,
    output: Option<File>,
    bytes: Vec<u8>,
    offset: usize,
    eof: bool,
    draining: bool,
    deadline: Instant,
}
#[derive(Default)]
pub(super) struct Clipboard {
    focused_client: Option<ClientId>,
    owner: Option<WlDataSource>,
    mimes: Vec<String>,
    generation: u64,
    focus: u64,
    offers: Vec<WlDataOffer>,
    tickets: VecDeque<Ticket>,
    transfers: Vec<Transfer>,
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
            selection: self.clipboard.owner.is_some(),
            active: self.clipboard.transfers.len(),
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
    pub(super) fn advertise_clipboard(&mut self, dh: &DisplayHandle, device: &WlDataDevice) {
        // SeatHandler callbackはkeyboard内部lock中にも届く。keyboardを再取得しない。
        let focused = device
            .client()
            .is_some_and(|c| Some(c.id()) == self.clipboard.focused_client);
        if !focused || self.clipboard.owner.is_none() {
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
            },
        ) else {
            return;
        };
        device.data_offer(&offer);
        for mime in &self.clipboard.mimes {
            offer.offer(mime.clone());
        }
        device.selection(Some(&offer));
        self.clipboard.offers.push(offer);
    }
    pub(super) fn clipboard_new_focus(&mut self, focus: Option<&WlSurface>) {
        self.clipboard.focused_client = focus.and_then(|s| s.client()).map(|c| c.id());
        self.clipboard.tickets.clear();
        self.clipboard.focus = self
            .clipboard
            .focus
            .checked_add(1)
            .expect("clipboard focus");
        self.clipboard.report.cancelled += self.clipboard.transfers.len() as u64;
        self.clipboard.transfers.clear();
    }
    pub(super) fn clipboard_focus(&mut self, dh: &DisplayHandle) {
        for device in self.data_devices.values().cloned().collect::<Vec<_>>() {
            self.advertise_clipboard(dh, &device);
        }
    }
    pub(super) fn set_clipboard(&mut self, dh: &DisplayHandle, source: Option<WlDataSource>) {
        if let Some(old) = self.clipboard.owner.take()
            && old.is_alive()
            && source.as_ref() != Some(&old)
        {
            old.cancelled();
            self.counters.cancellations += 1;
        }
        self.clipboard.report.cancelled += self.clipboard.transfers.len() as u64;
        self.clipboard.transfers.clear();
        self.clipboard.generation = self
            .clipboard
            .generation
            .checked_add(1)
            .expect("clipboard generation");
        self.clipboard.mimes = source
            .as_ref()
            .and_then(|s| with_source_metadata(s, |m| m.mime_types.clone()).ok())
            .unwrap_or_default();
        self.clipboard.owner = source;
        self.clipboard_focus(dh);
    }
    pub(super) fn clipboard_tick(&mut self, dh: &DisplayHandle) {
        if self.clipboard.owner.as_ref().is_some_and(|s| !s.is_alive()) {
            self.set_clipboard(dh, None);
        }
        self.clipboard.offers.retain(|o| o.is_alive());
        self.clipboard
            .tickets
            .retain(|t| t.surface.is_alive() && t.when.elapsed() < TIMEOUT);
        let report = &mut self.clipboard.report;
        self.clipboard.transfers.retain_mut(|t| {
            if Instant::now() >= t.deadline {
                report.timed_out += 1;
                return false;
            }
            // 各transfer一tick最大64KiB。最大4本でprotocol ownerを占有しない。
            let mut budget = 64 * 1024;
            while !t.eof && budget > 0 {
                let mut chunk = [0; 8192];
                match t.input.read(&mut chunk[..budget.min(8192)]) {
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
                && state.clipboard.mimes.contains(&mime_type)
                && state.clipboard.owner.as_ref().is_some_and(|s| s.is_alive())
                && state
                    ._seat
                    .get_keyboard()
                    .is_some_and(|k| k.client_of_object_has_focus(&offer.id()))
                && offer.client().is_some_and(|c| c.id() == client.id())
                && state.clipboard.transfers.len() < 4
                && nonblocking(&fd);
            if !valid {
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
                input: read.into(),
                output: Some(fd.into()),
                bytes: Vec::new(),
                offset: 0,
                eof: false,
                draining: false,
                deadline: Instant::now() + TIMEOUT,
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
