//! A local Waypipe child exclusively owned by one selected application session.
use crate::waypipe::OwnedProcess;
use eidolon_transport::input_method::{Endpoint, Lease};
use std::{
    os::{
        fd::AsRawFd,
        unix::{net::UnixStream, process::CommandExt},
    },
    path::Path,
    process::Command,
};

pub(crate) struct Connection {
    lease: Lease,
    endpoint: Endpoint,
    _child: OwnedProcess,
}
impl Connection {
    pub(crate) fn start(
        peer: UnixStream,
        executable: &Path,
        directory: &Path,
        endpoint: Endpoint,
        attempt: u64,
    ) -> Result<Self, &'static str> {
        let socket = directory.join(format!("im{attempt:x}"));
        let fd = peer.as_raw_fd();
        let mut command = Command::new(executable);
        command
            .args([
                "--no-gpu",
                "--compress",
                "none",
                "--threads",
                "1",
                "--oneshot",
                "--socket",
            ])
            .arg(&socket)
            .arg("client")
            .env_remove("WAYLAND_DISPLAY")
            .env("WAYLAND_SOCKET", fd.to_string())
            .env("XDG_RUNTIME_DIR", directory);
        // SAFETY: the parent keeps peer alive through spawn. Only the forked child's copy loses CLOEXEC.
        unsafe {
            command.pre_exec(move || {
                if libc::fcntl(fd, libc::F_SETFD, 0) < 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let child = OwnedProcess::spawn(&mut command)?;
        let lease = endpoint.attach(socket)?;
        Ok(Self {
            lease,
            endpoint,
            _child: child,
        })
    }
    pub(crate) fn uses(&self, endpoint: &Endpoint) -> bool {
        self.endpoint.same_connection(endpoint) && !self.finished()
    }
    pub(crate) fn finished(&self) -> bool {
        self.lease.finished()
    }
}
