//! AndroidのkeyCode/scanCodeをevdevへ正規化し、physical sourceごとの押下を追跡する。
use serde::{Deserialize, Serialize};
use std::collections::{BTreeSet, HashSet};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum KeyboardLayout {
    #[default]
    Us,
    Jis,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum KeyAction {
    Pressed,
    Released,
}

/// 文字列/Unicodeを受け付けない、小さなAndroid物理key DTO。
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct KeyPacket {
    pub action: KeyAction,
    pub device: i32,
    pub scan_code: i32,
    pub key_code: i32,
    pub repeat: u32,
    pub meta: u32,
    pub time_ms: u32,
}
impl KeyPacket {
    pub fn code(self) -> Option<EvdevCode> {
        (self.device >= 0 && self.repeat <= i32::MAX as u32)
            .then(|| android_key(self.scan_code, self.key_code))
            .flatten()
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct EvdevCode(u32);
impl EvdevCode {
    pub fn new(code: u32) -> Option<Self> {
        // Linux input-event-codes.hのkeyboard領域。system/consumer/mouseを曖昧に受け取らない。
        matches!(code, 1..=83 | 85..=100 | 102..=111 | 117 | 119 | 121..=127 | 179..=194)
            .then_some(Self(code))
    }
    pub fn raw(self) -> u32 {
        self.0
    }
    /// Smithay 0.7のKeyboardHandleはXKB codeを受け、Wayland wireでは8を引く。
    pub fn xkb(self) -> u32 {
        self.0 + 8
    }
}

/// scanCode優先。fallbackはAndroid API36 KeyEvent定数からevdevのphysical位置への明示表。
/// Unicode/文字列からの変換や暗黙のmodifier合成をしない。
pub fn android_key(scan_code: i32, key_code: i32) -> Option<EvdevCode> {
    if let Ok(scan) = u32::try_from(scan_code)
        && let Some(code) = EvdevCode::new(scan)
    {
        return Some(code);
    }
    const LETTERS: [u32; 26] = [
        30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50, 49, 24, 25, 16, 19, 31, 20, 22, 47, 17,
        45, 21, 44,
    ];
    let code = match key_code {
        7 => 11, // KEYCODE_0
        8..=16 => (key_code - 6) as u32,
        29..=54 => LETTERS[(key_code - 29) as usize],
        19 => 103,
        20 => 108,
        21 => 105,
        22 => 106,
        23 => 28, // DPAD
        55 => 51,
        56 => 52,
        57 => 56,
        58 => 100,
        59 => 42,
        60 => 54,
        61 => 15,
        62 => 57,
        66 => 28,
        67 => 14,
        68 => 41,
        69 => 12,
        70 => 13,
        71 => 26,
        72 => 27,
        73 => 43,
        74 => 39,
        75 => 40,
        76 => 53,
        92 => 104,
        93 => 109,
        111 => 1,
        112 => 111,
        113 => 29,
        114 => 97,
        115 => 58,
        116 => 70,
        117 => 125,
        118 => 126,
        120 => 99,
        121 => 119,
        122 => 102,
        123 => 107,
        124 => 110,
        131..=140 => (key_code - 72) as u32,
        141 => 87,
        142 => 88,
        143 => 69,
        144 => 82,
        145 => 79,
        146 => 80,
        147 => 81,
        148 => 75,
        149 => 76,
        150 => 77,
        151 => 71,
        152 => 72,
        153 => 73,
        154 => 98,
        155 => 55,
        156 => 74,
        157 => 78,
        158 => 83,
        159 => 121,
        160 => 96,
        161 => 117,
        162 => 179,
        163 => 180,
        211 => 85,
        213 => 94,
        214 => 92,
        215 => 93,
        216 => 124,
        217 => 89,
        218 => 93, // JIS固有key
        _ => return None,
    };
    EvdevCode::new(code)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KeyError {
    InvalidSource,
    ResourceLimit,
}

#[derive(Default)]
pub struct KeyTracker {
    pressed: HashSet<(i32, EvdevCode)>,
}
impl KeyTracker {
    pub const LIMIT: usize = 256;
    /// duplicate/repeat/未知upはNone。同じkeyを二つのdeviceで押した時もwireは一押下にする。
    pub fn update(
        &mut self,
        device: i32,
        code: EvdevCode,
        action: KeyAction,
        repeat: u32,
    ) -> Result<Option<KeyAction>, KeyError> {
        if device < 0 {
            return Err(KeyError::InvalidSource);
        }
        let pair = (device, code);
        match action {
            KeyAction::Pressed => {
                if repeat > 0 || self.pressed.contains(&pair) {
                    return Ok(None);
                }
                if self.pressed.len() == Self::LIMIT {
                    return Err(KeyError::ResourceLimit);
                }
                let already = self.pressed.iter().any(|(_, key)| *key == code);
                self.pressed.insert(pair);
                Ok((!already).then_some(action))
            }
            KeyAction::Released => {
                if !self.pressed.remove(&pair) {
                    return Ok(None);
                }
                Ok((!self.pressed.iter().any(|(_, key)| *key == code)).then_some(action))
            }
        }
    }
    pub fn pressed_count(&self) -> usize {
        self.pressed.len()
    }
    pub fn reset(&mut self) -> Vec<EvdevCode> {
        self.pressed
            .drain()
            .map(|(_, code)| code)
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn android_physical_positions_and_jis_special_keys_use_one_xkb_offset() {
        for (android, evdev) in [
            (29, 30),
            (54, 44),
            (7, 11),
            (8, 2),
            (16, 10),
            (62, 57),
            (66, 28),
            (67, 14),
            (111, 1),
            (61, 15),
            (19, 103),
            (20, 108),
            (21, 105),
            (22, 106),
            (131, 59),
            (140, 68),
            (141, 87),
            (142, 88),
            (113, 29),
            (114, 97),
            (57, 56),
            (58, 100),
            (59, 42),
            (60, 54),
            (117, 125),
            (118, 126),
            (211, 85),
            (213, 94),
            (214, 92),
            (215, 93),
            (216, 124),
            (217, 89),
            (218, 93),
        ] {
            let key = android_key(0, android).unwrap();
            assert_eq!(key.raw(), evdev);
            assert_eq!(key.xkb(), evdev + 8);
        }
        assert_eq!(android_key(30, 54).unwrap().raw(), 30); // 検証済みscanCodeが優先
        assert_eq!(android_key(999, 29).unwrap().raw(), 30); // 未知scanCodeは明示tableへ
        assert!(android_key(0, 77).is_none()); // @を暗黙にShift+2へ変換しない
        assert!(android_key(0, 3).is_none()); // HOMEを奪わない
        for code in [0, 84, 112, 113, 114, 115, 116, 240, 256, 767, u32::MAX] {
            assert!(EvdevCode::new(code).is_none());
        }
    }
    #[test]
    fn repeat_duplicate_unknown_release_reset_and_two_devices_do_not_stick() {
        let mut keys = KeyTracker::default();
        let control = EvdevCode::new(29).unwrap();
        assert_eq!(keys.update(1, control, KeyAction::Pressed, 1), Ok(None));
        assert_eq!(
            keys.update(1, control, KeyAction::Pressed, 0),
            Ok(Some(KeyAction::Pressed))
        );
        assert_eq!(keys.update(1, control, KeyAction::Pressed, 0), Ok(None));
        assert_eq!(keys.update(2, control, KeyAction::Pressed, 0), Ok(None));
        assert_eq!(keys.update(1, control, KeyAction::Released, 0), Ok(None));
        assert_eq!(
            keys.update(2, control, KeyAction::Released, 0),
            Ok(Some(KeyAction::Released))
        );
        assert_eq!(keys.update(2, control, KeyAction::Released, 0), Ok(None));
        keys.update(1, control, KeyAction::Pressed, 0).unwrap();
        assert_eq!(keys.reset(), vec![control]);
        assert!(keys.reset().is_empty());
        assert_eq!(keys.update(1, control, KeyAction::Released, 0), Ok(None));
        assert_eq!(keys.pressed_count(), 0);
    }
    #[test]
    fn finite_pressed_budget_and_device_validation_are_explicit() {
        let mut keys = KeyTracker::default();
        let a = EvdevCode::new(30).unwrap();
        assert_eq!(
            keys.update(-1, a, KeyAction::Pressed, 0),
            Err(KeyError::InvalidSource)
        );
        for device in 0..KeyTracker::LIMIT as i32 {
            keys.update(device, a, KeyAction::Pressed, 0).unwrap();
        }
        assert_eq!(
            keys.update(1000, a, KeyAction::Pressed, 0),
            Err(KeyError::ResourceLimit)
        );
        assert_eq!(keys.reset(), vec![a]);
        assert_eq!(keys.pressed_count(), 0);
    }
}
