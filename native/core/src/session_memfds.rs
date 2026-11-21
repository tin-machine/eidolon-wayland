//! Attribute anonymous files still held by other live compositors during session cleanup.
use std::{
    collections::HashMap,
    fs,
    os::fd::BorrowedFd,
    os::unix::fs::MetadataExt,
    sync::{Arc, Mutex, MutexGuard},
};

#[derive(Clone, Default)]
pub(crate) struct Owner(Arc<()>);

#[derive(Clone, Copy, Eq, PartialEq, Hash)]
struct FileId(u64, u64, u64);

impl FileId {
    fn new(device: u64, inode: u64) -> Self {
        Self(
            libc::major(device as _) as u64,
            libc::minor(device as _) as u64,
            inode,
        )
    }
}

#[derive(Clone, Copy, Default)]
struct Counts {
    fds: usize,
    maps: usize,
}

#[derive(Clone, Copy, PartialEq)]
struct Claim {
    owner: usize,
    file: FileId,
    mapped: bool,
}
static CLAIMS: Mutex<Vec<Claim>> = Mutex::new(Vec::new());
fn claims() -> MutexGuard<'static, Vec<Claim>> {
    CLAIMS.lock().unwrap_or_else(|err| err.into_inner())
}

pub(crate) struct FileLease {
    claim: Claim,
    _owner: Owner,
}

impl Drop for FileLease {
    fn drop(&mut self) {
        let mut active = claims();
        if let Some(index) = active.iter().position(|claim| claim == &self.claim) {
            active.swap_remove(index);
        }
    }
}

impl Owner {
    fn id(&self) -> usize {
        Arc::as_ptr(&self.0) as usize
    }

    pub(crate) fn track(&self, fd: BorrowedFd<'_>, mapped: bool) -> Option<FileLease> {
        let stat = nix::sys::stat::fstat(fd).ok()?;
        let claim = Claim {
            owner: self.id(),
            file: FileId::new(stat.st_dev as _, stat.st_ino as _),
            mapped,
        };
        claims().push(claim);
        Some(FileLease {
            claim,
            _owner: self.clone(),
        })
    }

    pub(crate) fn remaining(&self) -> Result<(usize, usize), &'static str> {
        let mut seen: HashMap<FileId, Counts> = HashMap::new();
        let live = claims();
        for fd in fs::read_dir("/proc/self/fd").map_err(|_| "PROC_FD")? {
            let path = fd.map_err(|_| "PROC_FD")?.path();
            let name = match fs::read_link(&path) {
                Ok(name) => name,
                Err(err) if err.kind() == std::io::ErrorKind::NotFound => continue,
                Err(_) => return Err("PROC_FD"),
            };
            if named(&name.to_string_lossy()) {
                match fs::metadata(path) {
                    Ok(info) => {
                        seen.entry(FileId::new(info.dev(), info.ino()))
                            .or_default()
                            .fds += 1
                    }
                    Err(err) if err.kind() == std::io::ErrorKind::NotFound => continue,
                    Err(_) => return Err("PROC_FD"),
                }
            }
        }
        for line in fs::read_to_string("/proc/self/maps")
            .map_err(|_| "PROC_MAPS")?
            .lines()
            .filter(|s| named(s))
        {
            let mut fields = line.split_whitespace().skip(3);
            let (major, minor) = fields
                .next()
                .and_then(|s| s.split_once(':'))
                .ok_or("PROC_MAPS")?;
            let id = FileId(
                u64::from_str_radix(major, 16).map_err(|_| "PROC_MAPS")?,
                u64::from_str_radix(minor, 16).map_err(|_| "PROC_MAPS")?,
                fields
                    .next()
                    .and_then(|s| s.parse().ok())
                    .ok_or("PROC_MAPS")?,
            );
            seen.entry(id).or_default().maps += 1;
        }
        Ok(unclaimed(seen, self.id(), &live))
    }
}

fn named(s: &str) -> bool {
    [
        "memfd:waypipe",
        "memfd:eidolon-shm-fixture",
        "memfd:smithay-keymap",
    ]
    .iter()
    .any(|name| s.contains(name))
}

fn unclaimed(mut seen: HashMap<FileId, Counts>, owner: usize, active: &[Claim]) -> (usize, usize) {
    for claim in active.iter().filter(|claim| claim.owner != owner) {
        if let Some(count) = seen.get_mut(&claim.file) {
            count.fds = count.fds.saturating_sub(1);
            if claim.mapped {
                count.maps = count.maps.saturating_sub(1);
            }
        }
    }
    seen.values()
        .fold((0, 0), |(fds, maps), v| (fds + v.fds, maps + v.maps))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn other_window_only_accounts_for_its_own_fd_and_mapping() {
        let file = FileId(0, 1, 17);
        let unknown = FileId(0, 1, 18);
        let seen = HashMap::from([
            (file, Counts { fds: 2, maps: 2 }),
            (unknown, Counts { fds: 1, maps: 0 }),
        ]);
        let foreign = Claim {
            owner: 2,
            file,
            mapped: true,
        };
        assert_eq!(unclaimed(seen.clone(), 1, &[foreign]), (2, 1));
        assert_eq!(unclaimed(seen, 2, &[foreign]), (3, 2));
    }
}
