use serde::{Deserialize, Deserializer, Serialize, Serializer, de};
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InvalidId;
impl fmt::Display for InvalidId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("ID must be a positive signed 64-bit decimal string")
    }
}
impl std::error::Error for InvalidId {}

macro_rules! id_type {
    ($($name:ident),+ $(,)?) => {$(
        #[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
        pub struct $name(i64);
        impl $name {
            pub fn new(value: i64) -> Result<Self, InvalidId> {
                if value > 0 { Ok(Self(value)) } else { Err(InvalidId) }
            }
            pub fn get(self) -> i64 { self.0 }
            /// 最大値では枯渇を返す。wrapして既存IDを再利用しない。
            pub fn checked_next(self) -> Option<Self> { self.0.checked_add(1).map(Self) }
        }
        impl Serialize for $name {
            fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
                serializer.serialize_str(&self.0.to_string())
            }
        }
        impl<'de> Deserialize<'de> for $name {
            fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
                let text = String::deserialize(deserializer)?;
                if text.starts_with('0') || !text.bytes().all(|b| b.is_ascii_digit()) {
                    return Err(de::Error::custom(InvalidId));
                }
                let value = text.parse::<i64>().map_err(|_| de::Error::custom(InvalidId))?;
                Self::new(value).map_err(de::Error::custom)
            }
        }
    )+};
}
id_type!(
    RuntimeHandle,
    SessionId,
    WindowId,
    SurfaceId,
    SceneEpoch,
    SurfaceGeneration,
    FrameSeq,
    ContentSeq,
    InputSourceId,
    CommandSeq,
    TerminalId,
    SampleSeq
);

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ids_preserve_all_signed_64_bits_and_reject_invalid_values() {
        let id = WindowId::new(i64::MAX).unwrap();
        assert_eq!(
            serde_json::to_string(&id).unwrap(),
            "\"9223372036854775807\""
        );
        assert_eq!(
            serde_json::from_str::<WindowId>("\"9223372036854775807\"").unwrap(),
            id
        );
        assert_eq!(id.checked_next(), None);
        for invalid in [
            "0",
            "-1",
            "1",
            "\"0\"",
            "\"01\"",
            "\"+1\"",
            "\"-1\"",
            "\"\"",
            "\"9223372036854775808\"",
        ] {
            assert!(
                serde_json::from_str::<WindowId>(invalid).is_err(),
                "{invalid}"
            );
        }
        assert!(SessionId::new(0).is_err());
        assert!(SessionId::new(-1).is_err());
        assert_eq!(SessionId::new(1).unwrap().checked_next().unwrap().get(), 2);
    }
}
