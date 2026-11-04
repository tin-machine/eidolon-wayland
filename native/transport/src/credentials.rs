use crate::Error;
use eidolon_model::ids::{CommandSeq, SessionId};
use russh::keys::{Algorithm, PrivateKey, ssh_key::LineEnding};
use std::time::{Duration, Instant};
use zeroize::Zeroizing;

pub const KEY_BYTES: usize = 16 * 1024;
pub type Secret = Zeroizing<Vec<u8>>;
pub fn generate() -> Result<Zeroizing<Vec<u8>>, Error> {
    let key = PrivateKey::random(&mut rand::rng(), Algorithm::Ed25519)
        .map_err(|_| Error::CredentialInvalid)?;
    let encoded = key
        .to_openssh(LineEnding::LF)
        .map_err(|_| Error::CredentialInvalid)?;
    Ok(Zeroizing::new(encoded.as_bytes().to_vec()))
}
pub fn decode(bytes: &[u8]) -> Result<PrivateKey, Error> {
    if bytes.is_empty() || bytes.len() > KEY_BYTES {
        return Err(Error::CredentialInvalid);
    }
    let key = PrivateKey::from_openssh(bytes).map_err(|_| Error::CredentialInvalid)?;
    if key.algorithm() != Algorithm::Ed25519 || key.is_encrypted() {
        return Err(Error::CredentialInvalid);
    }
    Ok(key)
}
pub fn public_key(bytes: &[u8]) -> Result<String, Error> {
    decode(bytes)?
        .public_key()
        .to_openssh()
        .map_err(|_| Error::CredentialInvalid)
}

struct Pending {
    session: SessionId,
    request: CommandSeq,
    expires: Instant,
    key: Option<PrivateKey>,
}
#[derive(Default)]
pub struct CredentialSlot {
    pending: Option<Pending>,
}
impl CredentialSlot {
    pub fn begin(&mut self, session: SessionId, request: CommandSeq) {
        self.pending = Some(Pending {
            session,
            request,
            expires: Instant::now() + Duration::from_secs(10),
            key: None,
        });
    }
    pub fn provide(
        &mut self,
        session: SessionId,
        request: CommandSeq,
        bytes: Zeroizing<Vec<u8>>,
    ) -> Result<(), Error> {
        let pending = self.pending.as_mut().ok_or(Error::Stale)?;
        if pending.expires <= Instant::now() {
            self.clear();
            return Err(Error::Stale);
        }
        if pending.session != session || pending.request != request || pending.key.is_some() {
            return Err(Error::Stale);
        }
        pending.key = Some(decode(&bytes)?);
        // 元のOpenSSH bufferはここでzeroizeし、署名用key objectだけを一度保持。
        Ok(())
    }
    pub fn take(&mut self, session: SessionId, request: CommandSeq) -> Result<PrivateKey, Error> {
        let pending = self.pending.as_mut().ok_or(Error::Stale)?;
        if pending.expires <= Instant::now() {
            self.clear();
            return Err(Error::Stale);
        }
        if pending.session != session || pending.request != request {
            return Err(Error::Stale);
        }
        let key = pending.key.take().ok_or(Error::NotReady)?;
        self.clear();
        Ok(key)
    }
    pub fn clear(&mut self) {
        self.pending = None;
    }
    pub fn expire(&mut self) {
        if self
            .pending
            .as_ref()
            .is_some_and(|pending| pending.expires <= Instant::now())
        {
            self.clear();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn session(n: i64) -> SessionId {
        SessionId::new(n).unwrap()
    }
    fn request(n: i64) -> CommandSeq {
        CommandSeq::new(n).unwrap()
    }
    #[test]
    fn identity_is_ed25519_and_secret_is_not_the_public_export() {
        let private = generate().unwrap();
        let public = public_key(&private).unwrap();
        assert!(public.starts_with("ssh-ed25519 "));
        assert!(!public.contains("PRIVATE"));
        assert!(public_key(b"invalid").is_err());
        assert!(decode(&vec![0; KEY_BYTES + 1]).is_err());
        assert!(decode(&private).unwrap().algorithm() == Algorithm::Ed25519);
    }
    #[test]
    fn pending_session_and_request_are_required_and_single_use() {
        let private = generate().unwrap();
        let mut slot = CredentialSlot::default();
        assert_eq!(
            slot.provide(session(1), request(1), private.clone()),
            Err(Error::Stale)
        );
        slot.begin(session(2), request(3));
        assert_eq!(
            slot.provide(session(1), request(3), private.clone()),
            Err(Error::Stale)
        );
        assert_eq!(
            slot.provide(session(2), request(1), private.clone()),
            Err(Error::Stale)
        );
        assert!(
            slot.provide(session(2), request(3), private.clone())
                .is_ok()
        );
        assert_eq!(
            slot.provide(session(2), request(3), private),
            Err(Error::Stale)
        );
        assert!(slot.take(session(2), request(3)).is_ok());
        assert!(matches!(
            slot.take(session(2), request(3)),
            Err(Error::Stale)
        ));
    }
    #[test]
    fn expired_or_cancelled_request_cannot_accept_or_retain_identity() {
        let private = generate().unwrap();
        let mut slot = CredentialSlot::default();
        slot.begin(session(1), request(1));
        slot.pending.as_mut().unwrap().expires = Instant::now() - Duration::from_secs(1);
        assert_eq!(
            slot.provide(session(1), request(1), private.clone()),
            Err(Error::Stale)
        );
        slot.begin(session(1), request(2));
        slot.provide(session(1), request(2), private).unwrap();
        slot.pending.as_mut().unwrap().expires = Instant::now() - Duration::from_secs(1);
        slot.expire();
        assert!(slot.pending.is_none());
        slot.clear();
        assert!(matches!(
            slot.take(session(1), request(2)),
            Err(Error::Stale)
        ));
    }
}
