//! 明示取得したテキストをプロセス内だけで保持する。本文は診断・永続化の型に含めない。

use crate::{
    ids::{ClipboardCardId, RuntimeHandle},
    surface::TargetKey,
};
use std::time::{Duration, Instant};

pub const MAX_CLIP_BYTES: usize = 1024 * 1024;
const MAX_SHELF_BYTES: usize = 4 * MAX_CLIP_BYTES;
const MAX_CLIPS: usize = 8;
const CLIP_TTL: Duration = Duration::from_secs(5 * 60);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ClipOrigin {
    pub runtime: RuntimeHandle,
    pub target: TargetKey,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ClipMetadata {
    pub id: ClipboardCardId,
    pub origin: ClipOrigin,
    pub captured: Instant,
    pub bytes: usize,
}

impl ClipMetadata {
    pub fn expires_at(self) -> Instant {
        self.captured + CLIP_TTL
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ClipError {
    Empty,
    TooLarge,
    InvalidUtf8,
    ShelfFull,
    Missing,
    Exhausted,
}

struct Clip {
    metadata: ClipMetadata,
    text: String,
}

#[derive(Default)]
pub struct ClipboardShelf {
    last_id: Option<ClipboardCardId>,
    clips: Vec<Clip>,
}

impl ClipboardShelf {
    pub fn capture(
        &mut self,
        origin: ClipOrigin,
        bytes: Vec<u8>,
        now: Instant,
    ) -> Result<ClipMetadata, ClipError> {
        self.expire(now);
        if bytes.is_empty() {
            return Err(ClipError::Empty);
        }
        if bytes.len() > MAX_CLIP_BYTES {
            return Err(ClipError::TooLarge);
        }
        let text = String::from_utf8(bytes).map_err(|_| ClipError::InvalidUtf8)?;
        if self.clips.len() >= MAX_CLIPS
            || self
                .clips
                .iter()
                .map(|card| card.metadata.bytes)
                .sum::<usize>()
                + text.len()
                > MAX_SHELF_BYTES
        {
            return Err(ClipError::ShelfFull);
        }
        let id = match self.last_id {
            Some(previous) => previous.checked_next().ok_or(ClipError::Exhausted)?,
            None => ClipboardCardId::new(1).expect("positive initial card ID"),
        };
        let metadata = ClipMetadata {
            id,
            origin,
            captured: now,
            bytes: text.len(),
        };
        self.last_id = Some(id);
        self.clips.push(Clip { metadata, text });
        Ok(metadata)
    }

    pub fn metadata(&mut self, now: Instant) -> Vec<ClipMetadata> {
        self.expire(now);
        self.clips.iter().map(|card| card.metadata).collect()
    }

    /// 利用者が選択したカードのプレビューや配布にだけ用いる。
    pub fn text(&mut self, id: ClipboardCardId, now: Instant) -> Result<&str, ClipError> {
        self.expire(now);
        self.clips
            .iter()
            .find(|card| card.metadata.id == id)
            .map(|card| card.text.as_str())
            .ok_or(ClipError::Missing)
    }

    pub fn remove(&mut self, id: ClipboardCardId, now: Instant) -> Result<(), ClipError> {
        self.expire(now);
        let index = self
            .clips
            .iter()
            .position(|card| card.metadata.id == id)
            .ok_or(ClipError::Missing)?;
        self.clips.remove(index);
        Ok(())
    }

    pub fn clear(&mut self) {
        self.clips.clear();
    }

    fn expire(&mut self, now: Instant) {
        self.clips
            .retain(|card| now.saturating_duration_since(card.metadata.captured) < CLIP_TTL);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn origin() -> ClipOrigin {
        ClipOrigin {
            runtime: RuntimeHandle::new(1).unwrap(),
            target: TargetKey::from_raw(2, 3, 4, 5).unwrap(),
        }
    }

    #[test]
    fn preview_is_explicit_and_expired_card_ids_are_not_reused() {
        let time = Instant::now();
        let mut shelf = ClipboardShelf::default();
        let text = "日本語\nsecond line";
        let card = shelf
            .capture(origin(), text.as_bytes().to_vec(), time)
            .unwrap();
        assert_eq!(shelf.metadata(time), vec![card]);
        assert_eq!(shelf.text(card.id, time), Ok(text));
        assert_eq!(
            shelf.text(card.id, time + CLIP_TTL),
            Err(ClipError::Missing)
        );
        let new = shelf
            .capture(origin(), vec![b'x'], time + CLIP_TTL)
            .unwrap();
        assert_ne!(card.id, new.id);
        shelf.remove(new.id, time + CLIP_TTL).unwrap();
        assert!(shelf.metadata(time + CLIP_TTL).is_empty());
    }

    #[test]
    fn limits_reject_without_replacing_an_existing_card() {
        let time = Instant::now();
        let mut shelf = ClipboardShelf::default();
        assert_eq!(
            shelf.capture(origin(), vec![255], time),
            Err(ClipError::InvalidUtf8)
        );
        assert_eq!(shelf.capture(origin(), vec![], time), Err(ClipError::Empty));
        assert_eq!(
            shelf.capture(origin(), vec![b'x'; MAX_CLIP_BYTES + 1], time),
            Err(ClipError::TooLarge)
        );
        for _ in 0..4 {
            shelf
                .capture(origin(), vec![b'x'; MAX_CLIP_BYTES], time)
                .unwrap();
        }
        let full = shelf.metadata(time);
        assert_eq!(
            shelf.capture(origin(), vec![b'y'], time),
            Err(ClipError::ShelfFull)
        );
        assert_eq!(shelf.metadata(time), full);
        shelf.clear();
        for _ in 0..MAX_CLIPS {
            shelf.capture(origin(), vec![b'x'], time).unwrap();
        }
        assert_eq!(
            shelf.capture(origin(), vec![b'y'], time),
            Err(ClipError::ShelfFull)
        );
    }
}
