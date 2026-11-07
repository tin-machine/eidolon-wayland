//! IMEの所有済み契約。通常診断のDebugに入力本文を含めない。
use crate::ids::{CommandSeq, SessionId, SurfaceId, WindowId};
use serde::{Deserialize, Serialize};
use std::fmt;

pub const IME_TEXT_BYTES: usize = 4000;
pub const IME_TRANSACTION_BYTES: usize = 8192;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ImeTarget {
    pub session: SessionId,
    pub window: WindowId,
    pub surface: SurfaceId,
    pub focus_token: CommandSeq,
    pub input_epoch: u64,
    pub activation: u64,
    /// このtext-input objectから受けたcommit回数。keyboard serialではない。
    pub serial: u32,
}

#[derive(Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ImeSurrounding {
    pub text: String,
    /// UTF-8 byte offset。AndroidのUTF-16 offsetではない。
    pub cursor: u32,
    pub anchor: u32,
}
impl fmt::Debug for ImeSurrounding {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ImeSurrounding")
            .field("bytes", &self.text.len())
            .field("cursor", &self.cursor)
            .field("anchor", &self.anchor)
            .finish()
    }
}
impl ImeSurrounding {
    pub fn valid(&self) -> bool {
        valid_text(&self.text)
            && self.text.is_char_boundary(self.cursor as usize)
            && self.text.is_char_boundary(self.anchor as usize)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ImeRectangle {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
}
impl ImeRectangle {
    pub fn valid(&self) -> bool {
        self.width >= 0
            && self.height >= 0
            && self.x.checked_add(self.width).is_some()
            && self.y.checked_add(self.height).is_some()
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ImeSnapshot {
    pub target: ImeTarget,
    /// 編集内容・選択・属性・外部編集の世代。geometry-only commitでは変えない。
    pub edit_revision: u64,
    /// activation内で最後に適用したsequence。adapterの採番開始値に使う。
    pub last_sequence: u64,
    /// Noneは未提供。空文書とは異なる。
    pub surrounding: Option<ImeSurrounding>,
    pub content_hint: u32,
    pub content_purpose: u32,
    pub cursor_rectangle: Option<ImeRectangle>,
    pub change_cause: u32,
}

#[derive(Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ImePreedit {
    pub text: String,
    /// UTF-8 byte offset。両方-1の場合だけcursor非表示。
    pub cursor_begin: i32,
    pub cursor_end: i32,
}
impl fmt::Debug for ImePreedit {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ImePreedit")
            .field("bytes", &self.text.len())
            .field("cursor_begin", &self.cursor_begin)
            .field("cursor_end", &self.cursor_end)
            .finish()
    }
}
impl ImePreedit {
    pub fn valid(&self) -> bool {
        valid_text(&self.text)
            && ((self.cursor_begin == -1 && self.cursor_end == -1)
                || (self.cursor_begin >= 0
                    && self.cursor_end >= 0
                    && self.text.is_char_boundary(self.cursor_begin as usize)
                    && self.text.is_char_boundary(self.cursor_end as usize)))
    }
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ImeTransaction {
    pub target: ImeTarget,
    /// snapshotの値をcaptureする。queue済み本文を最新snapshotへ付け替えない。
    pub edit_revision: u64,
    /// activation内で単調増加。再送による二重確定を拒否する。
    pub sequence: u64,
    pub commit: Option<String>,
    /// Noneは今回のdoneでpreeditを空へ戻す。「前回の表示を保持」ではない。
    pub preedit: Option<ImePreedit>,
    #[serde(default)]
    pub delete_before: u32,
    #[serde(default)]
    pub delete_after: u32,
}
impl fmt::Debug for ImeTransaction {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ImeTransaction")
            .field("target", &self.target)
            .field("edit_revision", &self.edit_revision)
            .field("sequence", &self.sequence)
            .field("commit_bytes", &self.commit.as_ref().map(String::len))
            .field("preedit", &self.preedit)
            .field("delete_before", &self.delete_before)
            .field("delete_after", &self.delete_after)
            .finish()
    }
}
impl ImeTransaction {
    pub fn valid(&self) -> bool {
        self.sequence > 0
            && self.target.activation > 0
            && self.edit_revision > 0
            && self.commit.as_ref().is_none_or(|s| valid_text(s))
            && self.preedit.as_ref().is_none_or(ImePreedit::valid)
            && self.commit.as_ref().map_or(0, String::len)
                + self.preedit.as_ref().map_or(0, |p| p.text.len())
                <= IME_TRANSACTION_BYTES
    }

    pub fn valid_deletion(&self, surrounding: Option<&ImeSurrounding>) -> bool {
        if self.delete_before == 0 && self.delete_after == 0 {
            return true;
        }
        let Some(surrounding) = surrounding.filter(|s| s.valid()) else {
            return false;
        };
        // selectionは削除byte数へ含めない。前後とも既知snapshotの境界で検査する。
        let start = surrounding.cursor.min(surrounding.anchor);
        let end = surrounding.cursor.max(surrounding.anchor);
        let Some(before) = start.checked_sub(self.delete_before) else {
            return false;
        };
        let Some(after) = end.checked_add(self.delete_after) else {
            return false;
        };
        surrounding.text.is_char_boundary(before as usize)
            && surrounding.text.is_char_boundary(after as usize)
    }
}

pub fn valid_text(text: &str) -> bool {
    text.len() <= IME_TEXT_BYTES && !text.contains('\0')
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unicode_boundaries_and_hidden_cursor_are_explicit() {
        let mut preedit = ImePreedit {
            text: "日𠮷🙂e\u{301}".into(),
            cursor_begin: 3,
            cursor_end: 7,
        };
        assert!(preedit.valid());
        preedit.cursor_begin = 4;
        assert!(!preedit.valid());
        preedit.cursor_begin = -1;
        preedit.cursor_end = -1;
        assert!(preedit.valid());
        preedit.cursor_end = 0;
        assert!(!preedit.valid());
        assert!(!valid_text("a\0b"));
        assert!(!valid_text(&"a".repeat(IME_TEXT_BYTES + 1)));
    }

    #[test]
    fn snapshots_do_not_leak_text_through_debug() {
        let surrounding = ImeSurrounding {
            text: "private-fixture".into(),
            cursor: 0,
            anchor: 0,
        };
        assert!(surrounding.valid());
        assert!(!format!("{surrounding:?}").contains("private-fixture"));
        assert!(
            !ImeRectangle {
                x: i32::MAX,
                y: 0,
                width: 1,
                height: 1
            }
            .valid()
        );
    }
}
