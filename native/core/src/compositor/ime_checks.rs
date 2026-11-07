//! 固定文字列だけを使う実Wayland wire fixture。追加client依存や実入力を必要としない。
//! clientはsocketへv1 requestをencodeし、serverが返すeventを独立にdecodeする。
use super::ProtocolState;
use crate::compositor::checks::object_counts;
use eidolon_model::{
    ids::{SessionId, WindowId},
    ime::{IME_TEXT_BYTES, ImePreedit, ImeSnapshot, ImeTransaction},
    keyboard::KeyboardLayout,
};
use serde::Serialize;
use smithay::reexports::wayland_server::Display;
use std::{
    io::{ErrorKind, Read, Write},
    os::unix::net::UnixStream,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

#[derive(Debug, Default, Serialize)]
pub struct Report {
    pub state: &'static str,
    pub fixture_revision: &'static str,
    pub uid: u32,
    pub synthetic_input: bool,
    pub physical_input_verified: bool,
    pub checkpoints: u32,
    pub manager_version: u32,
    pub disabled_default_safe: bool,
    pub all_objects_entered: bool,
    pub stale_sent_no_events: bool,
    pub active_destroy_recovered: bool,
    pub enable_reset_pending: bool,
    pub geometry_race_preserved: bool,
    pub semantic_changes_rejected: bool,
    pub connection_invalidated: bool,
    pub serial_wrap_verified: bool,
    pub seeded_serial_wrap: bool,
    pub client_quota_isolated: bool,
    pub invalid_clients_isolated: u32,
    pub clients_after: usize,
    pub objects_after: usize,
    pub surfaces_after: usize,
    pub ime_objects_after: usize,
    pub delivered: u64,
    pub rejected: u64,
    pub error: Option<&'static str>,
}

type Result<T> = std::result::Result<T, &'static str>;

fn require(condition: bool, error: &'static str) -> Result<()> {
    if condition { Ok(()) } else { Err(error) }
}

struct Event {
    object: u32,
    opcode: u16,
    body: Vec<u8>,
}

fn word(value: u32) -> Vec<u8> {
    value.to_ne_bytes().to_vec()
}
fn words(values: &[u32]) -> Vec<u8> {
    values.iter().flat_map(|v| v.to_ne_bytes()).collect()
}
fn string(bytes: &[u8]) -> Vec<u8> {
    let mut encoded = word(bytes.len() as u32 + 1);
    encoded.extend_from_slice(bytes);
    encoded.push(0);
    while !encoded.len().is_multiple_of(4) {
        encoded.push(0);
    }
    encoded
}
fn read_word(bytes: &[u8], offset: usize) -> Result<u32> {
    let bytes = bytes.get(offset..offset + 4).ok_or("WIRE_SHORT_WORD")?;
    Ok(u32::from_ne_bytes(
        bytes.try_into().map_err(|_| "WIRE_WORD")?,
    ))
}
fn read_string(bytes: &[u8], offset: usize) -> Result<(&str, usize)> {
    let len = read_word(bytes, offset)? as usize;
    if len == 0 {
        return Ok(("", offset + 4));
    }
    let text = bytes
        .get(offset + 4..offset + 4 + len)
        .ok_or("WIRE_SHORT_STRING")?;
    require(text.last() == Some(&0), "WIRE_STRING_NUL")?;
    Ok((
        std::str::from_utf8(&text[..len - 1]).map_err(|_| "WIRE_UTF8")?,
        offset + 4 + len.next_multiple_of(4),
    ))
}

struct Peer {
    stream: UnixStream,
    buffer: Vec<u8>,
    next_id: u32,
    registry: u32,
    compositor: u32,
    manager: u32,
    seat: u32,
    surface: u32,
    inputs: [u32; 2],
}

impl Peer {
    fn allocate(&mut self) -> u32 {
        let id = self.next_id;
        self.next_id += 1;
        id
    }
    fn send(&mut self, object: u32, opcode: u16, body: Vec<u8>) -> Result<()> {
        let mut message = words(&[object, ((body.len() as u32 + 8) << 16) | opcode as u32]);
        message.extend(body);
        self.stream.write_all(&message).map_err(|_| "CLIENT_WRITE")
    }
    fn receive(&mut self) -> Result<Vec<Event>> {
        let mut bytes = [0; 8192];
        loop {
            match self.stream.read(&mut bytes) {
                Ok(0) => break,
                Ok(n) => self.buffer.extend_from_slice(&bytes[..n]),
                Err(e)
                    if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::ConnectionReset) =>
                {
                    break;
                }
                Err(_) => return Err("CLIENT_READ"),
            }
        }
        let mut events = vec![];
        while self.buffer.len() >= 8 {
            let header = read_word(&self.buffer, 4)?;
            let size = (header >> 16) as usize;
            require(size >= 8 && size.is_multiple_of(4), "WIRE_SIZE")?;
            if self.buffer.len() < size {
                break;
            }
            events.push(Event {
                object: read_word(&self.buffer, 0)?,
                opcode: header as u16,
                body: self.buffer[8..size].to_vec(),
            });
            self.buffer.drain(..size);
        }
        Ok(events)
    }
    fn sync(
        &mut self,
        display: &mut Display<ProtocolState>,
        state: &mut ProtocolState,
        stop: &AtomicBool,
    ) -> Result<Vec<Event>> {
        let callback = self.allocate();
        self.send(1, 0, word(callback))?;
        let deadline = Instant::now() + Duration::from_secs(2);
        let mut events = vec![];
        loop {
            require(
                !stop.load(Ordering::Acquire) && Instant::now() < deadline,
                "CANCELLED_OR_TIMEOUT",
            )?;
            display
                .dispatch_clients(state)
                .map_err(|_| "SERVER_DISPATCH")?;
            display.flush_clients().map_err(|_| "SERVER_FLUSH")?;
            events.extend(self.receive()?);
            if events.iter().any(|e| e.object == 1 && e.opcode == 0) {
                return Err("CLIENT_PROTOCOL_ERROR");
            }
            if events.iter().any(|e| e.object == callback && e.opcode == 0) {
                return Ok(events);
            }
            thread::sleep(Duration::from_millis(1));
        }
    }
    fn connect(
        display: &mut Display<ProtocolState>,
        state: &mut ProtocolState,
        stop: &AtomicBool,
    ) -> Result<Self> {
        let (server, stream) = UnixStream::pair().map_err(|_| "SOCKET_PAIR")?;
        stream
            .set_nonblocking(true)
            .map_err(|_| "SOCKET_NONBLOCK")?;
        state.accept(&display.handle(), server)?;
        let mut peer = Self {
            stream,
            buffer: vec![],
            next_id: 2,
            registry: 0,
            compositor: 0,
            manager: 0,
            seat: 0,
            surface: 0,
            inputs: [0, 0],
        };
        peer.registry = peer.allocate();
        peer.send(1, 1, word(peer.registry))?;
        let globals = peer.sync(display, state, stop)?;
        let registry = peer.registry;
        for event in globals
            .iter()
            .filter(|e| e.object == registry && e.opcode == 0)
        {
            let name = read_word(&event.body, 0)?;
            let (interface, offset) = read_string(&event.body, 4)?;
            let version = read_word(&event.body, offset)?;
            let bind_version = match interface {
                "wl_compositor" => 4,
                "wl_seat" => 5,
                "zwp_text_input_manager_v3" => {
                    require(version == 1, "MANAGER_VERSION")?;
                    1
                }
                _ => continue,
            };
            let id = peer.allocate();
            let mut bind = word(name);
            bind.extend(string(interface.as_bytes()));
            bind.extend(words(&[bind_version, id]));
            peer.send(peer.registry, 0, bind)?;
            match interface {
                "wl_compositor" => peer.compositor = id,
                "wl_seat" => peer.seat = id,
                _ => peer.manager = id,
            }
        }
        require(
            peer.compositor != 0 && peer.manager != 0 && peer.seat != 0,
            "MISSING_GLOBAL",
        )?;
        peer.surface = peer.allocate();
        peer.send(peer.compositor, 0, word(peer.surface))?;
        for index in 0..2 {
            peer.inputs[index] = peer.allocate();
            peer.send(peer.manager, 1, words(&[peer.inputs[index], peer.seat]))?;
        }
        peer.sync(display, state, stop)?;
        Ok(peer)
    }
    fn enable(&mut self, input: u32) -> Result<()> {
        self.send(input, 1, vec![])?;
        self.send(input, 7, vec![])
    }
    fn surrounding(&mut self, input: u32, text: &[u8], cursor: u32, anchor: u32) -> Result<()> {
        let mut body = string(text);
        body.extend(words(&[cursor, anchor]));
        self.send(input, 3, body)
    }
}

fn transaction(snapshot: &ImeSnapshot, sequence: u64, text: Option<&str>) -> ImeTransaction {
    ImeTransaction {
        target: snapshot.target,
        edit_revision: snapshot.edit_revision,
        sequence,
        commit: text.map(str::to_owned),
        preedit: None,
        delete_before: 0,
        delete_after: 0,
    }
}
fn snapshot(state: &ProtocolState) -> Result<ImeSnapshot> {
    state.ime_snapshot(5).ok_or("NO_IME_SNAPSHOT")
}
fn focus(state: &mut ProtocolState) -> Result<()> {
    let surface = state
        .surfaces_for_check()
        .last()
        .cloned()
        .ok_or("NO_SURFACE")?;
    // 小fixtureはbuffer/xdgを使わない。実keyboard ownerのsurface焦点経路を通す。
    state.focus_keyboard_surface(WindowId::new(1).unwrap(), surface)
}
fn cleanup(display: &mut Display<ProtocolState>, state: &mut ProtocolState) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        display
            .dispatch_clients(state)
            .map_err(|_| "CLEANUP_DISPATCH")?;
        if object_counts(display).0 == 0 {
            return Ok(());
        }
        require(Instant::now() < deadline, "CLEANUP_TIMEOUT")?;
        thread::sleep(Duration::from_millis(1));
    }
}

fn exercise(
    display: &mut Display<ProtocolState>,
    state: &mut ProtocolState,
    stop: &AtomicBool,
    report: &mut Report,
) -> Result<()> {
    let mut peer = Peer::connect(display, state, stop)?;
    report.manager_version = 1;
    let [a, b] = peer.inputs;
    focus(state)?;
    let events = peer.sync(display, state, stop)?;
    require(
        [a, b]
            .into_iter()
            .all(|id| events.iter().any(|e| e.object == id && e.opcode == 0)),
        "ALL_OBJECT_ENTER",
    )?;
    report.all_objects_entered = true;
    peer.enable(a)?;
    peer.sync(display, state, stop)?;
    require(
        state.ime_snapshot(5).is_none() && !state.ime_report().backend_enabled,
        "DEFAULT_BACKEND_ENABLED",
    )?;
    report.disabled_default_safe = true;
    state.set_ime_backend_enabled(true);
    let first = snapshot(state)?;
    let mut normal = transaction(&first, 1, Some("日本語"));
    normal.preedit = Some(ImePreedit {
        text: "に".into(),
        cursor_begin: 3,
        cursor_end: 3,
    });
    state.apply_ime(5, &normal)?;
    let events = peer.sync(display, state, stop)?;
    let commits: Vec<_> = events
        .iter()
        .filter(|e| e.object == a && e.opcode == 3)
        .collect();
    require(
        commits.len() == 1 && read_string(&commits[0].body, 0)?.0 == "日本語",
        "NORMAL_COMMIT",
    )?;
    require(
        events.iter().any(|e| {
            e.object == a && e.opcode == 2 && read_string(&e.body, 0).is_ok_and(|(s, _)| s == "に")
        }),
        "NORMAL_PREEDIT",
    )?;
    require(
        events.iter().any(|e| {
            e.object == a && e.opcode == 5 && read_word(&e.body, 0) == Ok(first.target.serial)
        }),
        "NORMAL_SERIAL",
    )?;
    report.checkpoints += 1;

    let mut stale = transaction(&first, 2, Some("古い文字"));
    stale.target.serial = stale.target.serial.wrapping_sub(1);
    require(
        state.apply_ime(5, &stale) == Err("STALE"),
        "STALE_SERIAL_ACCEPTED",
    )?;
    state.apply_ime(5, &transaction(&first, 2, None))?;
    let events = peer.sync(display, state, stop)?;
    require(
        events
            .iter()
            .filter(|e| e.object == a)
            .map(|e| e.opcode)
            .collect::<Vec<_>>()
            == [5],
        "STALE_LEAKED_INTO_EMPTY_DONE",
    )?;
    require(
        state.apply_ime(5, &normal) == Err("STALE"),
        "DUPLICATE_SEQUENCE",
    )?;
    require(
        peer.sync(display, state, stop)?
            .iter()
            .all(|e| e.object != a),
        "DUPLICATE_SENT_EVENT",
    )?;
    report.stale_sent_no_events = true;
    report.checkpoints += 1;

    // 全target要素を検査。epochは外側runtimeの最新値と照合する。
    for field in 0..6 {
        let mut bad = transaction(&first, 3, Some("遅延"));
        match field {
            0 => bad.target.input_epoch += 1,
            1 => bad.target.activation += 1,
            2 => bad.target.window = WindowId::new(2).unwrap(),
            3 => bad.target.session = SessionId::new(2).unwrap(),
            4 => bad.target.surface = eidolon_model::ids::SurfaceId::new(i64::MAX).unwrap(),
            _ => bad.target.focus_token = eidolon_model::ids::CommandSeq::new(i64::MAX).unwrap(),
        }
        require(
            state.apply_ime(5, &bad) == Err("STALE"),
            "STALE_TARGET_ACCEPTED",
        )?;
    }
    let mut oversized = transaction(&first, 3, None);
    oversized.commit = Some("x".repeat(IME_TEXT_BYTES + 1));
    require(
        state.apply_ime(5, &oversized) == Err("INVALID_ARGUMENT"),
        "OVERSIZE_BACKEND_ACCEPTED",
    )?;
    let mut invalid = transaction(&first, 3, Some("部分配送禁止"));
    invalid.preedit = Some(ImePreedit {
        text: "日".into(),
        cursor_begin: 1,
        cursor_end: 3,
    });
    require(
        state.apply_ime(5, &invalid) == Err("INVALID_ARGUMENT"),
        "BAD_PREEDIT_BOUNDARY",
    )?;
    require(
        peer.sync(display, state, stop)?
            .iter()
            .all(|e| e.object != a),
        "INVALID_PARTIAL_EVENTS",
    )?;
    report.checkpoints += 1;

    peer.surrounding(a, b"old-field", 9, 9)?;
    peer.send(a, 5, words(&[0x80, 8]))?;
    peer.enable(a)?;
    peer.sync(display, state, stop)?;
    let reset = snapshot(state)?;
    require(
        reset.surrounding.is_none()
            && reset.content_hint == 0
            && reset.content_purpose == 0
            && reset.target.activation != first.target.activation,
        "ENABLE_PENDING_RESET",
    )?;
    report.enable_reset_pending = true;
    // Bのenable/disableは有効なAを奪えない。
    peer.enable(b)?;
    peer.send(b, 2, vec![])?;
    peer.send(b, 7, vec![])?;
    peer.sync(display, state, stop)?;
    require(
        snapshot(state)?.target == reset.target,
        "OTHER_OBJECT_STOLE_ACTIVE",
    )?;
    peer.send(a, 0, vec![])?;
    peer.sync(display, state, stop)?;
    require(
        state.ime_snapshot(5).is_none(),
        "DESTROY_ACTIVE_STILL_ENABLED",
    )?;
    peer.enable(b)?;
    peer.sync(display, state, stop)?;
    let next = snapshot(state)?;
    state.apply_ime(5, &transaction(&next, 1, Some("次")))?;
    require(
        peer.sync(display, state, stop)?
            .iter()
            .any(|e| e.object == b && e.opcode == 3),
        "DESTROY_RECOVERY",
    )?;
    report.active_destroy_recovered = true;
    report.checkpoints += 1;

    // pending/currentとUTF-8 byte削除。確定後の古いsurroundingを再利用しない。
    peer.surrounding(b, "日🙂".as_bytes(), 3, 3)?;
    peer.sync(display, state, stop)?;
    require(
        snapshot(state)?.surrounding.is_none(),
        "SURROUNDING_BEFORE_COMMIT",
    )?;
    peer.send(b, 7, vec![])?;
    peer.sync(display, state, stop)?;
    let with_surrounding = snapshot(state)?;
    require(
        with_surrounding
            .surrounding
            .as_ref()
            .is_some_and(|s| s.text == "日🙂"),
        "SURROUNDING_MISSING",
    )?;
    require(
        state.apply_ime(5, &transaction(&next, 2, Some("old serial"))) == Err("STALE"),
        "CLIENT_COMMIT_SERIAL",
    )?;
    let mut deletion = transaction(&with_surrounding, 2, Some("新"));
    deletion.delete_before = 2;
    require(
        state.apply_ime(5, &deletion) == Err("INVALID_ARGUMENT"),
        "DELETE_UTF8_BOUNDARY",
    )?;
    deletion.delete_before = 3;
    state.apply_ime(5, &deletion)?;
    let events = peer.sync(display, state, stop)?;
    require(
        events
            .iter()
            .filter(|e| e.object == b)
            .map(|e| e.opcode)
            .collect::<Vec<_>>()
            == [4, 3, 5],
        "DELETE_TRANSACTION_ORDER",
    )?;
    require(
        snapshot(state)?.surrounding.is_none(),
        "STALE_SURROUNDING_REUSED",
    )?;
    deletion.sequence += 1;
    require(
        state.apply_ime(5, &deletion) == Err("INVALID_ARGUMENT"),
        "STALE_SURROUNDING_DELETE",
    )?;
    report.checkpoints += 1;

    peer.send(b, 2, vec![])?;
    peer.sync(display, state, stop)?;
    require(state.ime_snapshot(5).is_some(), "DISABLE_BEFORE_COMMIT")?;
    peer.send(b, 7, vec![])?;
    peer.sync(display, state, stop)?;
    require(state.ime_snapshot(5).is_none(), "DISABLE_COMMIT")?;
    require(
        state.apply_ime(5, &transaction(&with_surrounding, 4, Some("遅延"))) == Err("NOT_READY"),
        "DISABLED_BACKEND_EVENT",
    )?;
    peer.enable(b)?;
    peer.sync(display, state, stop)?;
    let active = snapshot(state)?;
    let mut preedit = transaction(&active, 1, None);
    preedit.preedit = Some(ImePreedit {
        text: "未確定".into(),
        cursor_begin: 9,
        cursor_end: 9,
    });
    state.apply_ime(5, &preedit)?;
    peer.sync(display, state, stop)?;
    state.set_ime_backend_enabled(false);
    let events = peer.sync(display, state, stop)?;
    require(
        events
            .iter()
            .filter(|e| e.object == b)
            .map(|e| e.opcode)
            .collect::<Vec<_>>()
            == [2, 5],
        "BACKEND_DISABLE_CANCEL",
    )?;
    require(state.ime_snapshot(5).is_none(), "BACKEND_DISABLED_SNAPSHOT")?;
    state.set_ime_backend_enabled(true);
    require(
        snapshot(state)?.target.activation != active.target.activation,
        "BACKEND_GENERATION",
    )?;
    report.checkpoints += 1;

    state.reset_input();
    let events = peer.sync(display, state, stop)?;
    require(
        events.iter().any(|e| e.object == b && e.opcode == 1),
        "RESET_LEAVE",
    )?;
    require(state.ime_snapshot(5).is_none(), "RESET_SNAPSHOT")?;
    peer.enable(b)?;
    peer.sync(display, state, stop)?;
    require(state.ime_snapshot(5).is_none(), "UNFOCUSED_ENABLE")?;
    focus(state)?;
    peer.sync(display, state, stop)?;
    require(state.ime_snapshot(5).is_none(), "OLD_ENABLE_REUSED")?;
    peer.enable(b)?;
    peer.sync(display, state, stop)?;
    let focused = snapshot(state)?;
    require(
        focused.target.focus_token != active.target.focus_token,
        "FOCUS_GENERATION",
    )?;
    peer.send(peer.surface, 0, vec![])?;
    peer.sync(display, state, stop)?;
    require(state.ime_snapshot(5).is_none(), "DESTROYED_SURFACE")?;
    require(
        state.apply_ime(5, &transaction(&focused, 1, Some("遅延"))) == Err("NOT_READY"),
        "DEAD_SURFACE_EVENT",
    )?;
    report.checkpoints += 1;
    drop(peer);
    cleanup(display, state)?;

    // malformed requestを別clientごとに試し、後の正常clientが同じDisplayで動くことを確認する。
    for invalid in 0..8 {
        state.reset_input();
        let mut bad = Peer::connect(display, state, stop)?;
        focus(state)?;
        bad.sync(display, state, stop)?;
        let id = bad.inputs[0];
        match invalid {
            0 => bad.surrounding(id, &vec![b'x'; IME_TEXT_BYTES + 1], 0, 0)?,
            1 => bad.surrounding(id, "日".as_bytes(), 1, 3)?,
            2 => bad.surrounding(id, &[0xff], 0, 0)?,
            3 => bad.send(id, 4, word(u32::MAX))?,
            4 => bad.send(id, 5, words(&[0, u32::MAX]))?,
            5 => bad.send(id, 6, words(&[0, 0, u32::MAX, 1]))?,
            6 => bad.send(id, 5, words(&[0x400, 0]))?,
            _ => {
                for _ in 2..17 {
                    let input = bad.allocate();
                    bad.send(bad.manager, 1, words(&[input, bad.seat]))?;
                }
            }
        }
        let rejected = state.ime_report().rejected;
        // serverのprotocol errorでこのclientのみ切断。sync成功として扱わない。
        require(
            bad.sync(display, state, stop).is_err(),
            "INVALID_CLIENT_NOT_REJECTED",
        )?;
        require(
            state.ime_report().rejected > rejected,
            "INVALID_CLIENT_COUNTER",
        )?;
        drop(bad);
        cleanup(display, state)?;
        report.invalid_clients_isolated += 1;
    }
    state.reset_input();
    let mut good = Peer::connect(display, state, stop)?;
    focus(state)?;
    good.sync(display, state, stop)?;
    good.enable(good.inputs[0])?;
    good.sync(display, state, stop)?;
    state.apply_ime(5, &transaction(&snapshot(state)?, 1, Some("復帰")))?;
    require(
        good.sync(display, state, stop)?
            .iter()
            .any(|e| e.object == good.inputs[0] && e.opcode == 3),
        "NORMAL_CLIENT_RECOVERY",
    )?;
    // 最後のobjectをdestroyしても同じsurface/seatで新しいobjectへ再接続できる。
    good.send(good.inputs[1], 0, vec![])?;
    good.send(good.inputs[0], 0, vec![])?;
    good.sync(display, state, stop)?;
    require(state.ime_snapshot(5).is_none(), "LAST_OBJECT_DESTROY")?;
    let fresh = good.allocate();
    good.send(good.manager, 1, words(&[fresh, good.seat]))?;
    require(
        good.sync(display, state, stop)?
            .iter()
            .any(|e| e.object == fresh && e.opcode == 0),
        "NEW_OBJECT_ENTER",
    )?;
    good.enable(fresh)?;
    good.sync(display, state, stop)?;
    state.apply_ime(5, &transaction(&snapshot(state)?, 1, Some("再接続")))?;
    require(
        good.sync(display, state, stop)?
            .iter()
            .any(|e| e.object == fresh && e.opcode == 3),
        "NEW_OBJECT_COMMIT",
    )?;
    drop(good);
    cleanup(display, state)?;
    report.checkpoints += 1;
    Ok(())
}

fn exercise_revisions_and_quotas(
    display: &mut Display<ProtocolState>,
    state: &mut ProtocolState,
    stop: &AtomicBool,
    report: &mut Report,
) -> Result<()> {
    state.reset_input();
    let mut peer = Peer::connect(display, state, stop)?;
    let a = peer.inputs[0];
    focus(state)?;
    peer.sync(display, state, stop)?;
    peer.enable(a)?;
    peer.sync(display, state, stop)?;
    let first = snapshot(state)?;
    let mut composition = transaction(&first, 1, None);
    composition.preedit = Some(ImePreedit {
        text: "継続".into(),
        cursor_begin: 6,
        cursor_end: 6,
    });
    state.apply_ime(5, &composition)?;
    peer.sync(display, state, stop)?;
    let before_geometry = snapshot(state)?;
    require(before_geometry.last_sequence == 1, "LAST_SEQUENCE_MISSING")?;
    let mut queued = transaction(&before_geometry, 2, Some("幾何"));
    queued.preedit = composition.preedit.clone();
    // snapshot取得・queue後にクライアントのgeometry-only commitが割り込む実順序。
    peer.send(a, 6, words(&[3, 4, 1, 1]))?;
    peer.send(a, 7, vec![])?;
    peer.sync(display, state, stop)?;
    let after_geometry = snapshot(state)?;
    require(
        after_geometry.edit_revision == before_geometry.edit_revision
            && after_geometry.target.serial != queued.target.serial,
        "GEOMETRY_REVISION_CHANGED",
    )?;
    state.apply_ime(5, &queued)?;
    let events = peer.sync(display, state, stop)?;
    require(
        events
            .iter()
            .filter(|e| e.object == a)
            .map(|e| e.opcode)
            .collect::<Vec<_>>()
            == [3, 2, 5],
        "GEOMETRY_TRANSACTION",
    )?;
    require(
        events.iter().any(|e| {
            e.object == a
                && e.opcode == 3
                && read_string(&e.body, 0).is_ok_and(|(s, _)| s == "幾何")
        }),
        "GEOMETRY_COMMIT",
    )?;
    require(
        events.iter().any(|e| {
            e.object == a
                && e.opcode == 2
                && read_string(&e.body, 0).is_ok_and(|(s, _)| s == "継続")
        }),
        "GEOMETRY_COMPOSITION",
    )?;
    require(
        events.iter().any(|e| {
            e.object == a
                && e.opcode == 5
                && read_word(&e.body, 0) == Ok(after_geometry.target.serial)
        }),
        "GEOMETRY_DONE_SERIAL",
    )?;
    require(
        queued.target == before_geometry.target && snapshot(state)?.last_sequence == 2,
        "QUEUED_PAYLOAD_RETAGGED",
    )?;
    let mut future = transaction(&after_geometry, 3, Some("未来"));
    future.target.serial = after_geometry.target.serial.wrapping_add(1);
    require(
        state.apply_ime(5, &future) == Err("STALE"),
        "FUTURE_SERIAL_ACCEPTED",
    )?;
    future.target.serial = before_geometry.target.serial.wrapping_sub(1);
    require(
        state.apply_ime(5, &future) == Err("STALE"),
        "PRE_REVISION_SERIAL_ACCEPTED",
    )?;
    future.target = after_geometry.target;
    future.edit_revision += 1;
    require(
        state.apply_ime(5, &future) == Err("STALE"),
        "UNKNOWN_EDIT_REVISION",
    )?;
    require(
        peer.sync(display, state, stop)?
            .iter()
            .all(|e| e.object != a),
        "BAD_REVISION_EVENT",
    )?;
    report.geometry_race_preserved = true;
    report.checkpoints += 1;

    // surrounding内容・selection・content属性・明示otherの各変更は別の編集状態。
    for kind in 0..4 {
        let previous = snapshot(state)?;
        let queued = transaction(&previous, previous.last_sequence + 1, Some("旧編集状態"));
        match kind {
            0 => peer.surrounding(a, b"abc", 1, 1)?,
            1 => peer.surrounding(a, b"abc", 2, 2)?,
            2 => peer.send(a, 5, words(&[0, 13]))?,
            _ => peer.send(a, 4, word(1))?,
        }
        peer.send(a, 7, vec![])?;
        peer.sync(display, state, stop)?;
        let current = snapshot(state)?;
        require(
            current.target.activation == previous.target.activation
                && current.edit_revision != previous.edit_revision
                && current.last_sequence == previous.last_sequence,
            "SEMANTIC_REVISION",
        )?;
        require(
            state.apply_ime(5, &queued) == Err("STALE"),
            "SEMANTIC_STALE_ACCEPTED",
        )?;
        require(
            peer.sync(display, state, stop)?
                .iter()
                .all(|e| e.object != a),
            "SEMANTIC_STALE_EVENT",
        )?;
        state.apply_ime(5, &transaction(&current, current.last_sequence + 1, None))?;
        peer.sync(display, state, stop)?;
    }
    // cause=otherの次commitは初期値へ戻るだけなら新しい外部編集ではない。
    let before = snapshot(state)?;
    peer.send(a, 6, words(&[5, 6, 1, 1]))?;
    peer.send(a, 7, vec![])?;
    peer.sync(display, state, stop)?;
    require(
        snapshot(state)?.edit_revision == before.edit_revision,
        "CAUSE_RESET_REVISION",
    )?;
    state.apply_ime(
        5,
        &transaction(&before, before.last_sequence + 1, Some("次")),
    )?;
    peer.sync(display, state, stop)?;
    report.semantic_changes_rejected = true;
    report.checkpoints += 1;

    let current = snapshot(state)?;
    let mut before_restart = transaction(&current, current.last_sequence + 1, None);
    before_restart.preedit = composition.preedit.clone();
    state.apply_ime(5, &before_restart)?;
    peer.sync(display, state, stop)?;
    let old_connection = snapshot(state)?;
    let queued = transaction(
        &old_connection,
        old_connection.last_sequence + 1,
        Some("旧接続"),
    );
    state.invalidate_ime_connection();
    let cancelled = peer.sync(display, state, stop)?;
    require(
        cancelled
            .iter()
            .filter(|e| e.object == a)
            .map(|e| e.opcode)
            .collect::<Vec<_>>()
            == [2, 5],
        "CONNECTION_CANCEL",
    )?;
    let fresh_connection = snapshot(state)?;
    require(
        fresh_connection.target.activation != old_connection.target.activation
            && fresh_connection.edit_revision != old_connection.edit_revision
            && fresh_connection.last_sequence == 0,
        "CONNECTION_RESTART_SNAPSHOT",
    )?;
    require(
        state.apply_ime(5, &queued) == Err("STALE"),
        "OLD_CONNECTION_QUEUED_EVENT",
    )?;
    state.apply_ime(5, &transaction(&fresh_connection, 1, Some("再開")))?;
    peer.sync(display, state, stop)?;
    report.connection_invalidated = true;
    report.checkpoints += 1;

    // counterだけをseedし、MAX-1→MAX→0の実request/eventを検査する。
    state.invalidate_ime_connection();
    state.ime_fixture_rebase_serial(u32::MAX - 1);
    let near_wrap = snapshot(state)?;
    let queued = transaction(&near_wrap, 1, Some("跨ぐ"));
    for x in [8, 9] {
        peer.send(a, 6, words(&[x, 1, 1, 1]))?;
        peer.send(a, 7, vec![])?;
    }
    peer.sync(display, state, stop)?;
    let wrapped = snapshot(state)?;
    require(
        wrapped.target.serial == 0 && wrapped.edit_revision == near_wrap.edit_revision,
        "SERIAL_WRAP_REVISION",
    )?;
    state.apply_ime(5, &queued)?;
    let events = peer.sync(display, state, stop)?;
    require(
        events
            .iter()
            .any(|e| e.object == a && e.opcode == 5 && read_word(&e.body, 0) == Ok(0)),
        "SERIAL_WRAP_DONE",
    )?;
    let mut future = transaction(&wrapped, 2, Some("wrap未来"));
    future.target.serial = 1;
    require(
        state.apply_ime(5, &future) == Err("STALE"),
        "WRAP_FUTURE_SERIAL",
    )?;
    future.target.serial = u32::MAX - 2;
    require(
        state.apply_ime(5, &future) == Err("STALE"),
        "WRAP_PRE_REVISION_SERIAL",
    )?;
    report.serial_wrap_verified = true;
    report.seeded_serial_wrap = true;
    report.checkpoints += 1;
    drop(peer);
    cleanup(display, state)?;

    // Aがclient別quotaを使い切ってもBは同時に有効化・確定できる。
    state.reset_input();
    let mut heavy = Peer::connect(display, state, stop)?;
    for _ in 2..16 {
        let input = heavy.allocate();
        heavy.send(heavy.manager, 1, words(&[input, heavy.seat]))?;
    }
    heavy.sync(display, state, stop)?;
    require(state.ime_report().objects == 16, "CLIENT_QUOTA_SETUP")?;
    let mut normal = Peer::connect(display, state, stop)?;
    focus(state)?;
    normal.sync(display, state, stop)?;
    normal.enable(normal.inputs[0])?;
    normal.sync(display, state, stop)?;
    let active = snapshot(state)?;
    state.apply_ime(5, &transaction(&active, 1, Some("他client")))?;
    require(
        normal
            .sync(display, state, stop)?
            .iter()
            .any(|e| e.object == normal.inputs[0] && e.opcode == 3),
        "OTHER_CLIENT_STARVED",
    )?;
    let excessive = heavy.allocate();
    heavy.send(heavy.manager, 1, words(&[excessive, heavy.seat]))?;
    require(
        heavy.sync(display, state, stop).is_err(),
        "CLIENT_QUOTA_OVERFLOW",
    )?;
    drop(heavy);
    normal.sync(display, state, stop)?;
    require(
        counts_for_quota(display) == 1
            && state.ime_report().objects == 2
            && snapshot(state)?.target == active.target,
        "OTHER_CLIENT_DISCONNECTED",
    )?;
    state.apply_ime(5, &transaction(&snapshot(state)?, 2, Some("継続")))?;
    require(
        normal
            .sync(display, state, stop)?
            .iter()
            .any(|e| e.object == normal.inputs[0] && e.opcode == 3),
        "OTHER_CLIENT_RECOVERY",
    )?;
    report.client_quota_isolated = true;
    report.checkpoints += 1;
    drop(normal);
    cleanup(display, state)?;
    Ok(())
}

fn counts_for_quota(display: &Display<ProtocolState>) -> usize {
    object_counts(display).0
}

pub fn run(stop: Arc<AtomicBool>) -> Report {
    let mut report = Report {
        state: "running",
        fixture_revision: "eidolon-text-input-v3-wire-v2",
        synthetic_input: true,
        ..Default::default()
    };
    // SAFETY: getuidは引数を取らずprocessの実UIDを返す。
    report.uid = unsafe { libc::getuid() };
    let outcome = (|| {
        let mut display = Display::<ProtocolState>::new().map_err(|_| "DISPLAY_CREATE")?;
        let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
        state.configure_keyboard(KeyboardLayout::Us)?;
        let outcome = exercise(&mut display, &mut state, &stop, &mut report).and_then(|_| {
            exercise_revisions_and_quotas(&mut display, &mut state, &stop, &mut report)
        });
        // 失敗経路でも局所peerをdropしてから全client/objectを回収する。
        let cleaned = cleanup(&mut display, &mut state);
        (report.clients_after, report.objects_after) = object_counts(&display);
        report.surfaces_after = state.surface_count();
        let ime = state.ime_report();
        report.ime_objects_after = ime.objects;
        report.delivered = ime.delivered;
        report.rejected = ime.rejected;
        outcome?;
        cleaned?;
        require(
            report.clients_after == 0
                && report.objects_after == 0
                && report.surfaces_after == 0
                && report.ime_objects_after == 0,
            "RESOURCE_LEAK",
        )?;
        Ok(())
    })();
    match outcome {
        Ok(()) => report.state = "pass",
        Err(error) => {
            report.state = "fail";
            report.error = Some(error);
        }
    }
    report
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn text_input_v3_real_wire_transactions_lifetime_and_invalid_clients() {
        let _guard = crate::compositor::FIXTURE_LOCK.lock().unwrap();
        let report = run(Arc::new(AtomicBool::new(false)));
        assert_eq!(report.state, "pass", "{report:?}");
        assert_eq!(report.checkpoints, 13);
        assert_eq!(report.invalid_clients_isolated, 8);
        assert_eq!(report.delivered, 19);
        assert_eq!(report.rejected, 34);
        assert!(!report.physical_input_verified);
        let diagnostic = serde_json::to_string(&report).unwrap();
        assert!(!diagnostic.contains("日本語"));
        assert!(!diagnostic.contains("old-field"));
    }

    #[test]
    fn cancelled_fixture_still_closes_local_client_and_resources() {
        let _guard = crate::compositor::FIXTURE_LOCK.lock().unwrap();
        let report = run(Arc::new(AtomicBool::new(true)));
        assert_eq!(report.state, "fail");
        assert_eq!(report.error, Some("CANCELLED_OR_TIMEOUT"));
        assert_eq!(
            (
                report.clients_after,
                report.objects_after,
                report.surfaces_after,
                report.ime_objects_after
            ),
            (0, 0, 0, 0)
        );
    }
}
