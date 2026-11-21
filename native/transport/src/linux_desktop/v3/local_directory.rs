//! Attest the private Waypipe directory before any local socket is allocated.
use super::{Context, journal};
use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    os::unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt},
    path::Path,
};

const MARKER: &str = ".desktop-v3-owner";
const ERROR: &str = "DESKTOP_LOCAL_DIRECTORY_UNPROVEN";

pub fn mark(directory: &Path, context: &Context) -> Result<(), &'static str> {
    journal::check_directory(directory, true)?;
    if directory.file_name().and_then(|name| name.to_str()) != Some(context.nonce().as_str()) {
        return Err(ERROR);
    }
    let identity = fs::symlink_metadata(directory).map_err(|_| ERROR)?;
    let mut marker = OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(directory.join(MARKER))
        .map_err(|_| ERROR)?;
    marker
        .write_all(stamp(context, &identity).as_bytes())
        .map_err(|_| ERROR)?;
    marker.sync_all().map_err(|_| ERROR)?;
    journal::sync(directory)?;
    journal::sync(directory.parent().ok_or(ERROR)?)
}

fn stamp(context: &Context, identity: &fs::Metadata) -> String {
    format!(
        "{}:{}:{}\n",
        context.scope(),
        identity.dev(),
        identity.ino()
    )
}

fn matches_after_reboot(context: &Context, identity: &fs::Metadata, raw: &[u8]) -> bool {
    let Some(text) = std::str::from_utf8(raw)
        .ok()
        .and_then(|text| text.strip_suffix('\n'))
    else {
        return false;
    };
    let mut parts = text.split(':');
    // Android's device-mapper minor number can change across reboot. The
    // same private path, context and filesystem inode identify this directory.
    parts.next() == Some(context.scope().as_str())
        && parts
            .next()
            .is_some_and(|value| value.parse::<u64>().is_ok())
        && parts.next().and_then(|value| value.parse::<u64>().ok()) == Some(identity.ino())
        && parts.next().is_none()
}

fn absent(path: &Path) -> Result<bool, &'static str> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(false),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(true),
        Err(_) => Err(ERROR),
    }
}

/// A journal which never exposed launch admission must not leave any app
/// sockets or private directory. Refuse unexpected files without deleting.
pub(super) fn require_absent(files: &Path, context: &Context) -> Result<(), &'static str> {
    journal::check_directory(files, false)?;
    let parent = files.join("rt");
    if absent(&parent)? {
        return Ok(());
    }
    journal::check_directory(&parent, true)?;
    if !absent(&parent.join(context.nonce().as_str()))? {
        return Err(ERROR);
    }
    Ok(())
}

fn owned_sockets(
    entries: impl Iterator<Item = std::io::Result<fs::DirEntry>>,
    uid: u32,
) -> Result<Vec<std::path::PathBuf>, &'static str> {
    let mut sockets = Vec::new();
    for entry in entries {
        let entry = entry.map_err(|_| ERROR)?;
        if entry.file_name() == MARKER {
            continue;
        }
        let path = entry.path();
        let m = fs::symlink_metadata(&path).map_err(|_| ERROR)?;
        if !matches!(entry.file_name().to_str(), Some("w" | "p"))
            || !m.file_type().is_socket()
            || m.uid() != uid
            || m.nlink() != 1
        {
            return Err(ERROR);
        }
        sockets.push(path);
    }
    Ok(sockets)
}

/// Earlier v3 releases used the same private nonce layout without a marker.
/// Establish its current identity now and require a subsequent device reboot.
pub(super) fn remember_legacy_owner(files: &Path, context: &Context) -> Result<(), &'static str> {
    let parent = files.join("rt");
    if absent(&parent)? {
        return Ok(());
    }
    journal::check_directory(&parent, true)?;
    let directory = parent.join(context.nonce().as_str());
    if absent(&directory)? {
        return Ok(());
    }
    journal::check_directory(&directory, true)?;
    let marker_path = directory.join(MARKER);
    owned_sockets(fs::read_dir(&directory).map_err(|_| ERROR)?, unsafe {
        libc::getuid()
    })?;
    if absent(&marker_path)? {
        mark(&directory, context)?;
    }
    Ok(())
}

/// Called only after proof that the previous Android boot has ended. Leave
/// unknown entries intact and never traverse a symlink or a subdirectory.
pub(super) fn retire_after_reboot(files: &Path, context: &Context) -> Result<(), &'static str> {
    journal::check_directory(files, false)?;
    let parent = files.join("rt");
    if absent(&parent)? {
        return Ok(());
    }
    journal::check_directory(&parent, true)?;
    let directory = parent.join(context.nonce().as_str());
    if absent(&directory)? {
        return Ok(());
    }
    journal::check_directory(&directory, true)?;
    let identity = fs::symlink_metadata(&directory).map_err(|_| ERROR)?;
    let mut entries = fs::read_dir(&directory).map_err(|_| ERROR)?.peekable();
    if entries.peek().is_some() {
        let marker_path = directory.join(MARKER);
        let marker = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NONBLOCK | libc::O_NOFOLLOW)
            .open(&marker_path)
            .map_err(|_| ERROR)?;
        let metadata = marker.metadata().map_err(|_| ERROR)?;
        let mut actual = Vec::new();
        if !metadata.is_file()
            || metadata.mode() & 0o777 != 0o600
            || metadata.uid() != unsafe { libc::getuid() }
            || metadata.nlink() != 1
            || marker.take(256).read_to_end(&mut actual).is_err()
            || !matches_after_reboot(context, &identity, &actual)
        {
            return Err(ERROR);
        }
        let sockets = owned_sockets(entries, metadata.uid())?;
        for path in sockets {
            fs::remove_file(path).map_err(|_| ERROR)?;
        }
        fs::remove_file(marker_path).map_err(|_| ERROR)?;
    }
    fs::remove_dir(directory).map_err(|_| ERROR)?;
    journal::sync(&parent)
}
