//! Androidで選択したZIPは展開先pathを作らず、この限定形式に変換して保存する。
//! little endian: magic(9), file count(u16), {path bytes(u16), content bytes(u32), path UTF-8, content}。
use crate::{
    manifest::{Error, relative_file},
    raster::{Bundle, MAX_SOURCE},
};

pub const MAGIC: &[u8; 9] = b"EIDTHEME\x01";
pub const MAX_PACKAGE: usize = MAX_SOURCE + 16 * 1024;

/// callerが決めたprivate directory内のregular fileのみ。取得したFDのidentityも再照合する。
pub fn read_file(path: &std::path::Path, uid: u32) -> Result<Vec<u8>, Error> {
    use std::{
        io::Read,
        os::unix::fs::{MetadataExt, OpenOptionsExt},
    };
    let bad = || Error("theme package file invalid".into());
    let before = std::fs::symlink_metadata(path).map_err(|_| bad())?;
    if !before.is_file()
        || before.uid() != uid
        || before.mode() & 0o777 != 0o600
        || before.len() > MAX_PACKAGE as u64
    {
        return Err(bad());
    }
    let mut file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)
        .map_err(|_| bad())?;
    let opened = file.metadata().map_err(|_| bad())?;
    if (before.dev(), before.ino(), before.len()) != (opened.dev(), opened.ino(), opened.len()) {
        return Err(bad());
    }
    let mut bytes = Vec::new();
    (&mut file)
        .take(MAX_PACKAGE as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| bad())?;
    let after = file.metadata().map_err(|_| bad())?;
    if bytes.len() > MAX_PACKAGE
        || after.len() != bytes.len() as u64
        || (
            after.mtime(),
            after.mtime_nsec(),
            after.ctime(),
            after.ctime_nsec(),
        ) != (
            opened.mtime(),
            opened.mtime_nsec(),
            opened.ctime(),
            opened.ctime_nsec(),
        )
    {
        return Err(bad());
    }
    Ok(bytes)
}

pub fn encode(bundle: &Bundle) -> Vec<u8> {
    let mut output = Vec::new();
    output.extend_from_slice(MAGIC);
    output.extend_from_slice(&(bundle.files.len() as u16).to_le_bytes());
    for (name, bytes) in &bundle.files {
        output.extend_from_slice(&(name.len() as u16).to_le_bytes());
        output.extend_from_slice(&(bytes.len() as u32).to_le_bytes());
        output.extend_from_slice(name.as_bytes());
        output.extend_from_slice(bytes);
    }
    output
}
pub fn decode(bytes: &[u8]) -> Result<Bundle, Error> {
    fn take<'a>(remaining: &mut &'a [u8], count: usize) -> Result<&'a [u8], Error> {
        let (part, rest) = remaining
            .split_at_checked(count)
            .ok_or_else(|| Error("truncated theme package".into()))?;
        *remaining = rest;
        Ok(part)
    }
    if bytes.len() > MAX_PACKAGE {
        return Err(Error("theme package limit".into()));
    }
    let mut rest = bytes;
    if take(&mut rest, MAGIC.len())? != MAGIC {
        return Err(Error("theme package header".into()));
    }
    let count = u16::from_le_bytes(take(&mut rest, 2)?.try_into().unwrap());
    if !(1..=34).contains(&count) {
        return Err(Error("theme package file count".into()));
    }
    let mut total = 0usize;
    let mut entries = Vec::new();
    let mut names = std::collections::BTreeSet::new();
    for _ in 0..count {
        let length = u16::from_le_bytes(take(&mut rest, 2)?.try_into().unwrap()) as usize;
        let size = u32::from_le_bytes(take(&mut rest, 4)?.try_into().unwrap()) as usize;
        if !(1..=256).contains(&length) {
            return Err(Error("theme package path length".into()));
        }
        total = total
            .checked_add(size)
            .filter(|v| *v <= MAX_SOURCE)
            .ok_or_else(|| Error("theme package source limit".into()))?;
        let name = std::str::from_utf8(take(&mut rest, length)?)
            .map_err(|_| Error("theme package UTF-8 path".into()))?;
        if !relative_file(name) || !names.insert(name.to_owned()) {
            return Err(Error("theme package path".into()));
        }
        entries.push((name.to_owned(), take(&mut rest, size)?.to_vec()));
    }
    if !rest.is_empty() {
        return Err(Error("theme package trailing bytes".into()));
    }
    Bundle::new(entries)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn private_file_read_rejects_symlink_permissions_uid_and_large_files() {
        use std::os::unix::fs::{MetadataExt, PermissionsExt, symlink};
        let root = std::env::temp_dir().join(format!(
            "eidolon-theme-file-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&root).unwrap();
        let file = root.join("source.bundle");
        let bytes = encode(&crate::cyan_orbit());
        std::fs::write(&file, &bytes).unwrap();
        std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o600)).unwrap();
        let uid = std::fs::metadata(&file).unwrap().uid();
        assert_eq!(read_file(&file, uid).unwrap(), bytes);
        assert!(read_file(&file, uid.wrapping_add(1)).is_err());
        let link = root.join("link");
        symlink(&file, &link).unwrap();
        assert!(read_file(&link, uid).is_err());
        std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(read_file(&file, uid).is_err());
        std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o600)).unwrap();
        std::fs::OpenOptions::new()
            .write(true)
            .open(&file)
            .unwrap()
            .set_len(MAX_PACKAGE as u64 + 1)
            .unwrap();
        assert!(read_file(&file, uid).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn round_trip_real_theme_and_reject_every_truncation_and_trailing_data() {
        let bundle = crate::cyan_orbit();
        let encoded = encode(&bundle);
        let restored = decode(&encoded).unwrap();
        assert_eq!(restored.files, bundle.files);
        for end in 0..encoded.len() {
            assert!(decode(&encoded[..end]).is_err());
        }
        let mut extra = encoded.clone();
        extra.push(0);
        assert!(decode(&extra).is_err());
        let compiled = crate::raster::compile(
            &restored,
            &Default::default(),
            &Default::default(),
            &Default::default(),
        )
        .unwrap();
        assert_eq!(compiled.manifest.id, "cyan-orbit");
    }
    #[test]
    fn reject_traversal_duplicate_huge_lengths_and_counts_without_extracting_files() {
        fn packet(name: &str, size: u32, count: u16) -> Vec<u8> {
            let mut bytes = MAGIC.to_vec();
            bytes.extend_from_slice(&count.to_le_bytes());
            bytes.extend_from_slice(&(name.len() as u16).to_le_bytes());
            bytes.extend_from_slice(&size.to_le_bytes());
            bytes.extend_from_slice(name.as_bytes());
            bytes
        }
        for name in ["../escape", "/absolute", "x/../x", "a\\b", "file:a", "a\0b"] {
            assert!(decode(&packet(name, 0, 1)).is_err());
        }
        assert!(decode(&packet("theme.toml", u32::MAX, 1)).is_err());
        assert!(decode(&packet("theme.toml", 0, 35)).is_err());
        let mut duplicate = packet("style.css", 0, 2);
        duplicate.extend_from_slice(&packet("style.css", 0, 1)[11..]);
        assert!(decode(&duplicate).is_err());
        assert!(decode(&vec![0; MAX_PACKAGE + 1]).is_err());
    }
}
