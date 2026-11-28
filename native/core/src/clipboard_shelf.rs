//! 取得と配布の本文は通常のcommand/event/snapshot経路を通さない。

use crate::compositor::protocol::SelectionCapture;
use eidolon_model::{
    ids::{ClipboardCardId, CommandSeq, RuntimeHandle, SceneEpoch},
    surface::TargetKey,
};
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc::{Receiver, SyncSender},
    },
    time::Instant,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ClipboardFocus {
    pub target: TargetKey,
    pub input_epoch: u64,
    pub token: CommandSeq,
}

#[derive(Clone)]
pub struct ClipboardPermit {
    valid: Arc<AtomicBool>,
    expires: Instant,
}

impl ClipboardPermit {
    pub fn new(expires: Instant) -> Self {
        Self {
            valid: Arc::new(AtomicBool::new(true)),
            expires,
        }
    }

    pub fn is_live(&self, now: Instant) -> bool {
        now < self.expires && self.valid.load(Ordering::Acquire)
    }

    pub fn revoke(&self) {
        self.valid.store(false, Ordering::Release);
    }
}

pub struct ClipboardGrant {
    pub card: ClipboardCardId,
    pub text: Arc<str>,
    pub permit: ClipboardPermit,
}

pub enum ClipboardOperation {
    Capture {
        focus: ClipboardFocus,
        permit: ClipboardPermit,
    },
    Grant {
        focus: ClipboardFocus,
        grant: ClipboardGrant,
    },
}

impl ClipboardOperation {
    pub(crate) fn focus(&self) -> ClipboardFocus {
        match self {
            Self::Capture { focus, .. } | Self::Grant { focus, .. } => *focus,
        }
    }
}

pub enum ClipboardReply {
    Capturing(SelectionCapture),
    Granted,
}

pub type ClipboardResponse = Receiver<Result<ClipboardReply, &'static str>>;

pub(crate) struct ClipboardRequest {
    pub operation: ClipboardOperation,
    pub reply: SyncSender<Result<ClipboardReply, &'static str>>,
}

#[derive(serde::Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ClipboardAction {
    Tick {
        scene: SceneEpoch,
    },
    List {
        scene: SceneEpoch,
    },
    Capture {
        scene: SceneEpoch,
        runtime: RuntimeHandle,
        focus: ClipboardFocus,
    },
    Paste {
        scene: SceneEpoch,
        runtime: RuntimeHandle,
        focus: ClipboardFocus,
        card: ClipboardCardId,
    },
    Preview {
        scene: SceneEpoch,
        card: ClipboardCardId,
    },
    Remove {
        scene: SceneEpoch,
        card: ClipboardCardId,
    },
    Clear {
        scene: SceneEpoch,
    },
}

impl ClipboardAction {
    pub fn scene(&self) -> SceneEpoch {
        match self {
            Self::Tick { scene }
            | Self::List { scene }
            | Self::Capture { scene, .. }
            | Self::Paste { scene, .. }
            | Self::Preview { scene, .. }
            | Self::Remove { scene, .. }
            | Self::Clear { scene } => *scene,
        }
    }

    pub fn runtime(&self) -> Option<RuntimeHandle> {
        match self {
            Self::Capture { runtime, .. } | Self::Paste { runtime, .. } => Some(*runtime),
            _ => None,
        }
    }
}
