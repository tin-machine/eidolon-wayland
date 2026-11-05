use memmap2::MmapOptions;
use nix::{
    fcntl::{FcntlArg, FdFlag, fcntl},
    sys::{
        memfd::{MFdFlags, memfd_create},
        socket::{
            AddressFamily, ControlMessage, ControlMessageOwned, MsgFlags, SockFlag, SockType,
            recvmsg, sendmsg, socketpair,
        },
    },
    unistd::ftruncate,
};
use serde::Serialize;
use sha2::{Digest, Sha256};
use smithay::{
    delegate_shm,
    reexports::wayland_server::{Display, protocol::wl_buffer::WlBuffer},
    wayland::{
        buffer::BufferHandler,
        shm::{ShmHandler, ShmState},
    },
};
use std::{
    fs::{self, File},
    io::{IoSlice, IoSliceMut, Read, Write},
    os::{
        fd::{AsFd, AsRawFd, FromRawFd, OwnedFd},
        unix::{
            ffi::OsStrExt,
            fs::{DirBuilderExt, FileTypeExt, MetadataExt},
            net::{UnixListener, UnixStream},
        },
    },
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
use xkbcommon::xkb;

type Error = &'static str;
#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub state: &'static str,
    pub uid: u32,
    pub nonce: String,
    pub display_created: bool,
    pub shm_global: bool,
    pub sync_done: bool,
    pub sync_delete_id: bool,
    pub received_fds: usize,
    pub fd_cloexec: bool,
    pub same_memfd: bool,
    pub mapped_bytes: usize,
    pub mapped_regions_after: usize,
    pub audited_fds: usize,
    pub owned_fds_closed: bool,
    pub socket_cleaned: bool,
    pub elapsed_ms: u64,
    pub keymaps: Vec<KeymapReport>,
    pub error: Option<Error>,
}
#[derive(Clone, Debug, Serialize)]
pub struct KeymapReport {
    pub layout: &'static str,
    pub sha256: String,
    pub bytes: usize,
    pub include_paths: usize,
    pub base: String,
    pub shifted: String,
    pub shifted_two: String,
}
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

struct Directory {
    path: PathBuf,
    identity: (u64, u64),
}
impl Directory {
    fn create(parent: &Path, nonce: &str) -> Result<Self, Error> {
        if nonce.len() != 16
            || !nonce
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err("CONFIG_INVALID");
        }
        let parent = parent.canonicalize().map_err(|_| "CONFIG_INVALID")?;
        let meta = parent.symlink_metadata().map_err(|_| "CONFIG_INVALID")?;
        // SAFETY: getuidは引数・ポインターを持たない。
        if !meta.is_dir() || meta.uid() != unsafe { libc::getuid() } {
            return Err("CONFIG_INVALID");
        }
        let path = parent.join(format!("d-{nonce}"));
        if path.join("w").as_os_str().as_bytes().len() > 103 {
            return Err("RESOURCE_LIMIT");
        }
        fs::DirBuilder::new()
            .mode(0o700)
            .create(&path)
            .map_err(|_| "CONFIG_INVALID")?;
        let meta = path.symlink_metadata().map_err(|_| "CONFIG_INVALID")?;
        Ok(Self {
            path,
            identity: (meta.dev(), meta.ino()),
        })
    }
    fn cleanup(&self) -> Result<(), Error> {
        let meta = self.path.symlink_metadata().map_err(|_| "CLEANUP_FAILED")?;
        if !meta.is_dir() || (meta.dev(), meta.ino()) != self.identity {
            return Err("CLEANUP_FAILED");
        }
        let path = self.path.join("w");
        match path.symlink_metadata() {
            Ok(meta) if meta.file_type().is_socket() => {
                fs::remove_file(path).map_err(|_| "CLEANUP_FAILED")?
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            _ => return Err("CLEANUP_FAILED"),
        }
        // 予期しないfileは残す。所有directoryでも再帰削除しない。
        fs::remove_dir(&self.path).map_err(|_| "CLEANUP_FAILED")
    }
}
impl Drop for Directory {
    fn drop(&mut self) {
        if self.path.exists() {
            let _ = self.cleanup();
        }
    }
}

enum AuditedFd {
    File(i32, u64, u64),
    Epoll,
}
struct EpollMarker {
    fd: OwnedFd,
    tag: u64,
}
#[derive(Default)]
struct Audit {
    entries: Vec<AuditedFd>,
    markers: Vec<EpollMarker>,
}
impl Audit {
    fn record(&mut self, fd: &impl AsFd) -> Result<(), Error> {
        let raw = fd.as_fd().as_raw_fd();
        let meta = fs::metadata(format!("/proc/self/fd/{raw}")).map_err(|_| "IPC_FAILED")?;
        if fs::read_link(format!("/proc/self/fd/{raw}")).map_err(|_| "IPC_FAILED")?
            == Path::new("anon_inode:[eventpoll]")
        {
            // eventpollは異なるopenでもdev/inodeが一致する。永続する非readyの
            // eventfdをwatchに入れ、番号再利用と本物のepoll/dup漏れを区別する。
            // SAFETY: 引数は固定flag、返された新規fdだけを一度所有する。
            let marker = unsafe { libc::eventfd(0, libc::EFD_CLOEXEC | libc::EFD_NONBLOCK) };
            if marker < 0 {
                return Err("IPC_FAILED");
            }
            // SAFETY: eventfdが返した新規fdで、他のownerへ渡していない。
            let fd = unsafe { OwnedFd::from_raw_fd(marker) };
            let tag = 0xe1d0_10fd_a0d1_7000_u64 ^ marker as u64;
            let mut event = libc::epoll_event {
                events: libc::EPOLLIN as u32,
                u64: tag,
            };
            // SAFETY: 借用中のepollと所有eventfd、初期化済みepoll_eventを渡す。
            if unsafe { libc::epoll_ctl(raw, libc::EPOLL_CTL_ADD, marker, &mut event) } != 0 {
                return Err("IPC_FAILED");
            }
            self.markers.push(EpollMarker { fd, tag });
            self.entries.push(AuditedFd::Epoll);
        } else {
            self.entries
                .push(AuditedFd::File(raw, meta.dev(), meta.ino()));
        }
        Ok(())
    }
    fn closed(&self) -> bool {
        if !self.entries.iter().all(|entry| match entry {
            AuditedFd::Epoll => true,
            AuditedFd::File(raw, dev, ino) => match fs::metadata(format!("/proc/self/fd/{raw}")) {
                Ok(meta) => (meta.dev(), meta.ino()) != (*dev, *ino),
                Err(error) => error.kind() == std::io::ErrorKind::NotFound,
            },
        }) {
            return false;
        }
        if self.markers.is_empty() {
            return true;
        }
        let Ok(files) = fs::read_dir("/proc/self/fdinfo") else {
            return false;
        };
        // 元のraw番号に限定せず、dupされた同じepollも検出する。
        for file in files {
            let Ok(file) = file else {
                return false;
            };
            let info = match fs::read_to_string(file.path()) {
                Ok(info) => info,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                Err(_) => return false,
            };
            for line in info.lines().filter(|line| line.starts_with("tfd:")) {
                let fields: Vec<_> = line.split_whitespace().collect();
                let target = fields.get(1).and_then(|value| value.parse::<i32>().ok());
                let tag = fields
                    .windows(2)
                    .find(|pair| pair[0] == "data:")
                    .and_then(|pair| u64::from_str_radix(pair[1], 16).ok());
                if target.is_none() || tag.is_none() {
                    return false;
                }
                if self
                    .markers
                    .iter()
                    .any(|marker| target == Some(marker.fd.as_raw_fd()) && tag == Some(marker.tag))
                {
                    return false;
                }
            }
        }
        true
    }
}
fn cancelled(stop: &AtomicBool) -> Result<(), Error> {
    if stop.load(Ordering::Acquire) {
        Err("CANCELLED")
    } else {
        Ok(())
    }
}
pub fn run(parent: &Path, nonce: &str, stop: &AtomicBool) -> Report {
    let started = Instant::now();
    let mut report = Report {
        state: "running",
        nonce: nonce.into(),
        ..Default::default()
    };
    // SAFETY: getuidは引数・ポインターを持たない。
    report.uid = unsafe { libc::getuid() };
    let mut audit = Audit::default();
    let result = (|| -> Result<(), Error> {
        cancelled(stop)?;
        let directory = Directory::create(parent, nonce)?;
        let operation = (|| {
            wayland(&directory.path.join("w"), stop, &mut audit, &mut report)?;
            cancelled(stop)?;
            memory(nonce, &mut audit, &mut report)?;
            for (layout, text) in [
                (
                    "us",
                    include_str!(concat!(
                        env!("CARGO_MANIFEST_DIR"),
                        "/../../android/app/src/main/assets/keymaps/us.xkb"
                    )),
                ),
                (
                    "jis",
                    include_str!(concat!(
                        env!("CARGO_MANIFEST_DIR"),
                        "/../../android/app/src/main/assets/keymaps/jis.xkb"
                    )),
                ),
            ] {
                cancelled(stop)?;
                report.keymaps.push(keymap(layout, text)?);
            }
            Ok(())
        })();
        report.socket_cleaned = directory.cleanup().is_ok();
        if !report.socket_cleaned {
            return Err("CLEANUP_FAILED");
        }
        operation
    })();
    report.audited_fds = audit.entries.len();
    report.owned_fds_closed = audit.closed();
    report.error = result.err();
    if !report.owned_fds_closed {
        report.error = Some("CLEANUP_FAILED");
    }
    report.state = if report.error.is_none() {
        "pass"
    } else {
        "fail"
    };
    report.elapsed_ms = started.elapsed().as_millis().try_into().unwrap_or(u64::MAX);
    report
}

fn wayland(
    path: &Path,
    stop: &AtomicBool,
    audit: &mut Audit,
    report: &mut Report,
) -> Result<(), Error> {
    let mut display = Display::<State>::new().map_err(|_| "WAYLAND_INIT_FAILED")?;
    report.display_created = true;
    let mut state = State {
        shm: ShmState::new::<State>(&display.handle(), vec![]),
    };
    report.shm_global = true;
    audit.record(&display)?;
    let listener = UnixListener::bind(path).map_err(|_| "IPC_FAILED")?;
    listener.set_nonblocking(true).map_err(|_| "IPC_FAILED")?;
    audit.record(&listener)?;
    let mut client = UnixStream::connect(path).map_err(|_| "IPC_FAILED")?;
    client
        .set_read_timeout(Some(Duration::from_secs(1)))
        .map_err(|_| "IPC_FAILED")?;
    client
        .set_write_timeout(Some(Duration::from_secs(1)))
        .map_err(|_| "IPC_FAILED")?;
    audit.record(&client)?;
    let (server, _) = listener.accept().map_err(|_| "IPC_FAILED")?;
    audit.record(&server)?;
    display
        .handle()
        .insert_client(server, Arc::new(()))
        .map_err(|_| "WAYLAND_INIT_FAILED")?;
    // 自分で生成したwl_display.sync(callback=2)。W02の一般client fixtureとは別。
    let request = [1_u32, 12 << 16, 2]
        .into_iter()
        .flat_map(u32::to_ne_bytes)
        .collect::<Vec<_>>();
    client.write_all(&request).map_err(|_| "IPC_FAILED")?;
    cancelled(stop)?;
    display
        .dispatch_clients(&mut state)
        .map_err(|_| "WAYLAND_INIT_FAILED")?;
    display.flush_clients().map_err(|_| "WAYLAND_INIT_FAILED")?;
    let mut bytes = [0_u8; 24];
    client.read_exact(&mut bytes).map_err(|_| "IPC_FAILED")?;
    for event in bytes.chunks_exact(12) {
        let words = event
            .chunks_exact(4)
            .map(|c| u32::from_ne_bytes(c.try_into().unwrap()))
            .collect::<Vec<_>>();
        if words[1] >> 16 != 12 {
            return Err("PROTOCOL_ERROR");
        }
        match (words[0], words[1] & 0xffff) {
            (2, 0) => report.sync_done = true,
            (1, 1) if words[2] == 2 => report.sync_delete_id = true,
            _ => return Err("PROTOCOL_ERROR"),
        }
    }
    if !report.sync_done || !report.sync_delete_id {
        return Err("PROTOCOL_ERROR");
    }
    drop(client);
    display
        .dispatch_clients(&mut state)
        .map_err(|_| "WAYLAND_INIT_FAILED")?;
    Ok(())
}

fn memory(nonce: &str, audit: &mut Audit, report: &mut Report) -> Result<(), Error> {
    let name = format!("eidolon-{nonce}");
    let descriptor =
        memfd_create(name.as_str(), MFdFlags::MFD_CLOEXEC).map_err(|_| "MEMFD_FAILED")?;
    ftruncate(&descriptor, 4096).map_err(|_| "MEMFD_FAILED")?;
    let inode;
    {
        let file = File::from(descriptor);
        audit.record(&file)?;
        let meta = file.metadata().map_err(|_| "MEMFD_FAILED")?;
        inode = meta.ino();
        // SAFETY: 新規の自分専用4096-byte memfd。全mappingの生存中にtruncateしない。
        let mut writable =
            unsafe { MmapOptions::new().len(4096).map_mut(&file) }.map_err(|_| "MMAP_FAILED")?;
        for (i, byte) in writable.iter_mut().enumerate() {
            *byte = (i % 251) as u8;
        }
        let (left, right) = socketpair(
            AddressFamily::Unix,
            SockType::SeqPacket,
            None,
            SockFlag::SOCK_CLOEXEC | SockFlag::SOCK_NONBLOCK,
        )
        .map_err(|_| "IPC_FAILED")?;
        audit.record(&left)?;
        audit.record(&right)?;
        let ids = [file.as_raw_fd()];
        let sent = sendmsg::<()>(
            left.as_raw_fd(),
            &[IoSlice::new(b"F")],
            &[ControlMessage::ScmRights(&ids)],
            MsgFlags::MSG_NOSIGNAL,
            None,
        )
        .map_err(|_| "IPC_FAILED")?;
        if sent != 1 {
            return Err("IPC_FAILED");
        }
        let mut data = [0_u8; 1];
        let mut slices = [IoSliceMut::new(&mut data)];
        let mut control = nix::cmsg_space!([i32; 8]);
        let message = recvmsg::<()>(
            right.as_raw_fd(),
            &mut slices,
            Some(&mut control),
            MsgFlags::MSG_CMSG_CLOEXEC,
        )
        .map_err(|_| "IPC_FAILED")?;
        let mut received = Vec::new();
        for cmsg in message.cmsgs().map_err(|_| "IPC_FAILED")? {
            if let ControlMessageOwned::ScmRights(fds) = cmsg {
                for fd in fds {
                    // SAFETY: dedicated socketpairへ自分が送ったSCM_RIGHTSの新規fdを一度だけ所有する。
                    received.push(unsafe { OwnedFd::from_raw_fd(fd) });
                }
            }
        }
        if message.bytes != 1
            || message
                .flags
                .intersects(MsgFlags::MSG_CTRUNC | MsgFlags::MSG_TRUNC)
            || received.len() != 1
        {
            return Err("IPC_FAILED");
        }
        report.received_fds = received.len();
        let received = File::from(received.pop().unwrap());
        audit.record(&received)?;
        let actual = received.metadata().map_err(|_| "MEMFD_FAILED")?;
        report.same_memfd =
            (meta.dev(), meta.ino(), meta.len()) == (actual.dev(), actual.ino(), actual.len());
        report.fd_cloexec = FdFlag::from_bits_truncate(
            fcntl(&received, FcntlArg::F_GETFD).map_err(|_| "IPC_FAILED")?,
        )
        .contains(FdFlag::FD_CLOEXEC);
        // SAFETY: 上と同じ所有memfdの複製。mapping終了までサイズは固定。
        let readonly =
            unsafe { MmapOptions::new().len(4096).map(&received) }.map_err(|_| "MMAP_FAILED")?;
        if !report.same_memfd
            || !report.fd_cloexec
            || readonly
                .iter()
                .enumerate()
                .any(|(i, b)| *b != (i % 251) as u8)
        {
            return Err("MMAP_FAILED");
        }
        report.mapped_bytes = readonly.len();
    }
    let mut maps = String::new();
    File::open("/proc/self/maps")
        .map_err(|_| "IPC_FAILED")?
        .take(4 * 1024 * 1024 + 1)
        .read_to_string(&mut maps)
        .map_err(|_| "IPC_FAILED")?;
    if maps.len() > 4 * 1024 * 1024 {
        return Err("RESOURCE_LIMIT");
    }
    report.mapped_regions_after = maps
        .lines()
        .filter(|line| {
            line.contains(&name)
                && line
                    .split_whitespace()
                    .nth(4)
                    .and_then(|s| s.parse::<u64>().ok())
                    == Some(inode)
        })
        .count();
    if report.mapped_regions_after != 0 {
        return Err("CLEANUP_FAILED");
    }
    Ok(())
}

fn keymap(layout: &'static str, text: &str) -> Result<KeymapReport, Error> {
    let context =
        xkb::Context::new(xkb::CONTEXT_NO_DEFAULT_INCLUDES | xkb::CONTEXT_NO_ENVIRONMENT_NAMES);
    let include_paths = context.include_paths().count();
    if include_paths != 0 {
        return Err("KEYMAP_FAILED");
    }
    let map = xkb::Keymap::new_from_string(
        &context,
        text.into(),
        xkb::KEYMAP_FORMAT_TEXT_V1,
        xkb::KEYMAP_COMPILE_NO_FLAGS,
    )
    .ok_or("KEYMAP_FAILED")?;
    let mut state = xkb::State::new(&map);
    let a = map.key_by_name("AC01").ok_or("KEYMAP_FAILED")?;
    let shift = map.key_by_name("LFSH").ok_or("KEYMAP_FAILED")?;
    let two = map.key_by_name("AE02").ok_or("KEYMAP_FAILED")?;
    let base = state.key_get_utf8(a);
    state.update_key(shift, xkb::KeyDirection::Down);
    let shifted = state.key_get_utf8(a);
    let shifted_two = state.key_get_utf8(two);
    if base != "a" || shifted != "A" || shifted_two != if layout == "jis" { "\"" } else { "@" } {
        return Err("KEYMAP_FAILED");
    }
    Ok(KeymapReport {
        layout,
        sha256: Sha256::digest(text.as_bytes())
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect(),
        bytes: text.len(),
        include_paths,
        base,
        shifted,
        shifted_two,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn epoll_reuse_is_distinct_but_a_duplicated_owned_epoll_still_fails_audit() {
        fn epoll() -> OwnedFd {
            // SAFETY: 固定flag、新規fdを一度だけOwnedFdへ移す。
            let raw = unsafe { libc::epoll_create1(libc::EPOLL_CLOEXEC) };
            assert!(raw >= 0);
            // SAFETY: epoll_create1の新規fdである。
            unsafe { OwnedFd::from_raw_fd(raw) }
        }
        let first = epoll();
        let identity = fs::metadata(format!("/proc/self/fd/{}", first.as_raw_fd())).unwrap();
        let mut audit = Audit::default();
        audit.record(&first).unwrap();
        assert!(!audit.closed());
        let duplicate = first.as_fd().try_clone_to_owned().unwrap();
        drop(first);
        let replacement = epoll();
        let other = fs::metadata(format!("/proc/self/fd/{}", replacement.as_raw_fd())).unwrap();
        assert_eq!((identity.dev(), identity.ino()), (other.dev(), other.ino()));
        assert!(!audit.closed(), "元のepollのdupを見逃してはいけない");
        drop(duplicate);
        assert!(
            audit.closed(),
            "他ownerのepollを元のfdの漏れと誤認してはいけない"
        );
        drop(replacement);
    }
    #[test]
    fn product_dependencies_sync_fd_mapping_and_both_standalone_keymaps() {
        let nonce = format!("{:016x}", std::process::id());
        let parent = std::env::temp_dir().join(format!("eidolon-deps-{}", std::process::id()));
        fs::DirBuilder::new().mode(0o700).create(&parent).unwrap();
        let report = run(&parent, &nonce, &AtomicBool::new(false));
        assert_eq!(report.error, None, "{report:?}");
        assert_eq!(report.state, "pass");
        assert!(report.sync_done && report.sync_delete_id && report.shm_global);
        assert_eq!(report.audited_fds, 8);
        assert!(report.owned_fds_closed && report.socket_cleaned);
        assert_eq!(report.mapped_regions_after, 0);
        assert_eq!(report.received_fds, 1);
        assert_eq!(report.keymaps.len(), 2);
        assert_eq!(fs::read_dir(&parent).unwrap().count(), 0);
        fs::remove_dir(parent).unwrap();
    }
    #[test]
    fn cancelled_probe_has_no_filesystem_side_effect() {
        let report = run(
            Path::new("/nonexistent-fixture"),
            "aaaaaaaaaaaaaaaa",
            &AtomicBool::new(true),
        );
        assert_eq!(report.error, Some("CANCELLED"));
        assert_eq!(report.audited_fds, 0);
        assert!(report.owned_fds_closed);
    }
}
