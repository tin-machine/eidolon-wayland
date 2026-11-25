use serde::{Deserialize, Serialize};

/// How long an already running GUI session can remain without a fresh owner signal.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(try_from = "u32", into = "u32")]
pub struct GuiUnresponsiveGrace(u32);

impl GuiUnresponsiveGrace {
    pub const DEFAULT: Self = Self(3_600);

    pub fn seconds(self) -> u32 {
        self.0
    }
}

impl Default for GuiUnresponsiveGrace {
    fn default() -> Self {
        Self::DEFAULT
    }
}

impl TryFrom<u32> for GuiUnresponsiveGrace {
    type Error = &'static str;

    fn try_from(seconds: u32) -> Result<Self, Self::Error> {
        if (60..=86_400).contains(&seconds) && seconds.is_multiple_of(60) {
            Ok(Self(seconds))
        } else {
            Err("GUI grace must be a whole number of minutes in 1..=1440")
        }
    }
}

impl From<GuiUnresponsiveGrace> for u32 {
    fn from(value: GuiUnresponsiveGrace) -> Self {
        value.seconds()
    }
}
