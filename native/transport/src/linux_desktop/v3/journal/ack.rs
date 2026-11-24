//! A finalizer may queue one receipt per authenticated profile and workspace.
//! An ACK is durable before its first send. Retries never use absence as
//! completion evidence for a pending owner.
use super::*;
use crate::linux_desktop::v3::{Receipt, owner, recovery::receipt_exchange};
use russh::client;
use tokio_util::sync::CancellationToken;

const INVALID: &str = "DESKTOP_RECEIPT_ACK_INVALID";
const WAITING: &str = "DESKTOP_RECEIPT_ACK_PENDING";
const MAX_PROFILES: usize = 128;
const OUTBOX: &str = "linux-desktop-v3-receipts";

fn profile_key(context: &Context) -> Result<String, &'static str> {
    let mut value = context.record();
    let identity = value.as_object_mut().ok_or(INVALID)?;
    identity.remove("session");
    identity.remove("nonce");
    eidolon_model::linux_desktop_v3::digest(&value).map_err(|_| INVALID)
}

fn path(directory: &Path, context: &Context) -> Result<PathBuf, &'static str> {
    Ok(directory.join(format!("ack-{}.json", profile_key(context)?)))
}

fn exists(path: &Path) -> Result<bool, &'static str> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(_) => Err(INVALID),
    }
}

pub(super) fn check_capacity(files: &Path, binding: &Binding) -> Result<(), &'static str> {
    let directory = files.join(OUTBOX);
    if !exists(&directory)? {
        return Ok(());
    }
    check_directory(&directory, true)?;
    if exists(&path(&directory, binding.context())?)? {
        return Err(WAITING);
    }
    let mut profiles = 0;
    for entry in std::fs::read_dir(directory).map_err(|_| INVALID)? {
        if entry
            .map_err(|_| INVALID)?
            .file_name()
            .as_bytes()
            .starts_with(b"ack-")
        {
            profiles += 1;
            if profiles >= MAX_PROFILES {
                return Err("DESKTOP_RECEIPT_OUTBOX_FULL");
            }
        }
    }
    Ok(())
}

fn encode(record: &Record, digest: Option<&str>) -> Result<Vec<u8>, &'static str> {
    let value = serde_json::json!({"completed": record, "digest": digest});
    canonical_json(&value).map_err(|_| INVALID)
}

// The journal lock serializes writers for the entire workspace. A process
// crash may leave just this one scratch file; the previous authoritative
// journal or outbox entry still exists until publication has completed.
fn stage(directory: &Path, bytes: &[u8]) -> Result<PathBuf, &'static str> {
    let name = directory.join(".receipt-write");
    let mut file = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
        .open(&name)
        .map_err(|_| INVALID)?;
    let m = file.metadata().map_err(|_| INVALID)?;
    if !m.is_file()
        || m.nlink() != 1
        || m.uid() != unsafe { libc::getuid() }
        || m.mode() & 0o777 != 0o600
        || m.len() > LIMIT as u64
    {
        return Err(INVALID);
    }
    file.set_len(0)
        .and_then(|()| file.write_all(bytes))
        .and_then(|()| file.sync_all())
        .map_err(|_| "DESKTOP_V3_JOURNAL_SYNC_FAILED")?;
    Ok(name)
}

pub(super) fn enqueue(files: &Path, record: &Record) -> Result<(), &'static str> {
    let directory = files.join(OUTBOX);
    match std::fs::DirBuilder::new().mode(0o700).create(&directory) {
        Ok(()) => (),
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
        Err(_) => return Err(INVALID),
    }
    check_directory(&directory, true)?;
    // Persist the outbox directory itself before removing the pending journal.
    sync(files)?;
    let target = path(&directory, &record.context)?;
    let bytes = encode(record, None)?;
    if exists(&target)? {
        if read(&target)?.1 != bytes {
            return Err(WAITING);
        }
    } else {
        let candidate = stage(&directory, &bytes)?;
        let result = exclusive(&candidate, &target);
        let _ = std::fs::remove_file(candidate);
        result?;
    }
    sync(&directory)
}

struct Outbox {
    directory: PathBuf,
    path: PathBuf,
    _guard: File,
    record: Record,
    binding: Binding,
    digest: Option<String>,
}

impl Outbox {
    fn load(files: &Path, active: &Binding) -> Result<Option<Self>, &'static str> {
        check_directory(files, false)?;
        let directory = files.join(DIRECTORY);
        if !exists(&directory)? {
            return Ok(None);
        }
        check_directory(&directory, true)?;
        let guard = lock(&directory)?;
        // Finalization may have crashed between publishing the outbox entry
        // and clearing the journal. Its ordinary recovery must run first.
        if exists(&directory.join("pending.json"))? {
            return Err("DESKTOP_CLEANUP_PENDING");
        }
        let directory = files.join(OUTBOX);
        if !exists(&directory)? {
            return Ok(None);
        }
        check_directory(&directory, true)?;
        let path = path(&directory, active.context())?;
        if !exists(&path)? {
            check_capacity(files, active)?;
            return Ok(None);
        }
        let (_, raw) = read(&path)?;
        let value = parse_json(&raw, LIMIT).map_err(|_| INVALID)?;
        exact_object(&value, &["completed", "digest"])?;
        let record = Record::parse(&canonical_json(&value["completed"]).map_err(|_| INVALID)?)?;
        if !record.receipt_retirement
            || record.stage != Stage::Complete
            || !record.cleanup.complete()
            || profile_key(&record.context)? != profile_key(active.context())?
        {
            return Err(INVALID);
        }
        let digest = match &value["digest"] {
            serde_json::Value::Null => None,
            serde_json::Value::String(hash) if crate::linux_desktop::hex(hash, 64) => {
                Some(hash.clone())
            }
            _ => return Err(INVALID),
        };
        let binding = Binding::from_captured(record.context.clone(), record.pins.clone())?;
        Ok(Some(Self {
            directory,
            path,
            _guard: guard,
            record,
            binding,
            digest,
        }))
    }

    fn unchanged(&self) -> Result<(), &'static str> {
        if read(&self.path)?.1 != encode(&self.record, self.digest.as_deref())? {
            return Err(INVALID);
        }
        Ok(())
    }

    fn accept_receipt(&mut self, raw: &[u8]) -> Result<(), &'static str> {
        let value = parse_json(raw, LIMIT).map_err(|_| INVALID)?;
        let expected = value["expected_boot_id"].as_str().ok_or(INVALID)?;
        if expected != "-" && Some(expected) != self.record.boot_id.as_deref() {
            return Err(INVALID);
        }
        let receipt = Receipt::parse(raw, &self.binding, &self.record.sources, expected, 0)?;
        if !receipt.complete()
            || receipt.origin_boot_id() != self.record.boot_id.as_deref()
            || receipt.pipes_eof() != self.record.cleanup.pipes_eof
            || (receipt.process_evidence() == "boot_changed") != self.record.cleanup.boot_changed
        {
            return Err(INVALID);
        }
        let hash = crate::linux_desktop::hash(raw);
        let bytes = encode(&self.record, Some(&hash))?;
        self.unchanged()?;
        let tmp = stage(&self.directory, &bytes)?;
        let result = std::fs::rename(&tmp, &self.path).map_err(|_| INVALID);
        let _ = std::fs::remove_file(tmp);
        result?;
        self.digest = Some(hash);
        sync(&self.directory)
    }

    fn confirm(&self, raw: &[u8]) -> Result<(), &'static str> {
        let digest = self.digest.as_deref().ok_or(INVALID)?;
        if raw != format!("retired {} {digest}\n", self.binding.scope()).as_bytes() {
            return Err(INVALID);
        }
        self.unchanged()?;
        std::fs::remove_file(&self.path).map_err(|_| INVALID)?;
        sync(&self.directory)
    }

    async fn exchange<H: client::Handler>(
        &self,
        client: &client::Handle<H>,
        workspace: u8,
        action: &str,
        cancel: &CancellationToken,
    ) -> Result<Vec<u8>, &'static str> {
        let command = owner::receipt_command(
            &self.binding,
            &self.record.sources,
            self.record.boot_id.as_deref().ok_or(INVALID)?,
            workspace,
            action,
        )?;
        let (raw, status) = receipt_exchange(client, command, cancel, || ()).await?;
        if status != 0 {
            return Err(WAITING);
        }
        Ok(raw)
    }
}

pub(in crate::linux_desktop::v3) async fn flush<H: client::Handler>(
    client: &client::Handle<H>,
    files: &Path,
    active: &Binding,
    cancel: &CancellationToken,
) -> Result<(), &'static str> {
    let Some(mut entry) = Outbox::load(files, active)? else {
        return Ok(());
    };
    let workspace = eidolon_model::limits::workspace_number(files);
    if entry.digest.is_none() {
        let raw = entry.exchange(client, workspace, "read", cancel).await?;
        entry.accept_receipt(&raw)?;
    }
    // Also covers a previous failed fsync after the atomic digest update.
    entry.unchanged()?;
    sync(&entry.directory)?;
    let reply = entry
        .exchange(
            client,
            workspace,
            entry.digest.as_deref().ok_or(INVALID)?,
            cancel,
        )
        .await?;
    entry.confirm(&reply)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::linux_desktop::v3::owner::test_support::*;

    #[test]
    fn durable_local_finalization_then_exact_receipt_then_idempotent_ack() {
        let dir = Temp::new();
        let b = binding();
        let mut j = Journal::reserve_inspection(&dir.0, &b, Some(7)).unwrap();
        j.prepared(BOOT).unwrap();
        j.finish(complete()).unwrap();
        assert!(!exists(&path(&dir.0.join(OUTBOX), b.context()).unwrap()).unwrap());
        // Crash after publishing the local ACK intent, before clearing pending.
        enqueue(&dir.0, &j.record).unwrap();
        drop(j);
        assert!(matches!(
            Outbox::load(&dir.0, &b),
            Err("DESKTOP_CLEANUP_PENDING")
        ));
        let (mut j, _) = Journal::matching(&request(&dir.0, &profile(), b.pins(), &storage()))
            .unwrap()
            .unwrap();
        j.clear().unwrap();
        drop(j);
        let journal_files: Vec<_> = std::fs::read_dir(dir.0.join(DIRECTORY))
            .unwrap()
            .map(|entry| entry.unwrap().file_name())
            .collect();
        assert_eq!(journal_files, vec!["owner.lock"]);
        assert!(matches!(
            Journal::reserve_inspection(&dir.0, &b, Some(7)),
            Err(WAITING)
        ));
        // Simulate an interrupted digest write before its atomic publication.
        let scratch = dir.0.join(OUTBOX).join(".receipt-write");
        OpenOptions::new()
            .create_new(true)
            .write(true)
            .mode(0o600)
            .open(&scratch)
            .unwrap()
            .write_all(b"{")
            .unwrap();
        let mut box1 = Outbox::load(&dir.0, &b).unwrap().unwrap();
        let mut fresh = b.context().record();
        fresh["nonce"] = serde_json::json!("000000000000002a");
        let mut wrong = receipt();
        wrong["context"] = fresh;
        let mut foreign = canonical_json(&wrong).unwrap();
        foreign.push(b'\n');
        assert!(box1.accept_receipt(&foreign).is_err());
        assert!(box1.digest.is_none());
        let mut raw = canonical_json(&receipt()).unwrap();
        raw.push(b'\n');
        box1.accept_receipt(&raw).unwrap();
        assert!(!scratch.exists());
        drop(box1); // lost response; reopen with exactly the same digest
        let saved = Outbox::load(&dir.0, &b).unwrap().unwrap();
        let digest = crate::linux_desktop::hash(&raw);
        assert_eq!(saved.digest.as_deref(), Some(digest.as_str()));
        assert!(saved.confirm(b"retired\n").is_err());
        saved
            .confirm(format!("retired {} {digest}\n", b.scope()).as_bytes())
            .unwrap();
        drop(saved);
        assert!(Outbox::load(&dir.0, &b).unwrap().is_none());
    }

    #[test]
    fn old_complete_journal_without_receipt_retirement_does_not_queue_an_ack() {
        let dir = Temp::new();
        let b = binding();
        let mut j = Journal::reserve(&dir.0, &b).unwrap();
        j.prepared(BOOT).unwrap();
        j.finish(complete()).unwrap();
        j.clear().unwrap();
        drop(j);
        assert!(Outbox::load(&dir.0, &b).unwrap().is_none());
    }
}
