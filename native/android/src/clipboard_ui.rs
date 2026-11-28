//! Private JNI commands for the clipboard shelf. No payload is returned to diagnostic readers.

use eidolon_core::{
    clipboard_coordinator::ClipboardCoordinator, clipboard_shelf::ClipboardAction, runtime::Runtime,
};
use eidolon_model::ids::{RuntimeHandle, SceneEpoch};
use serde_json::{Value, json};
use std::{sync::Arc, time::Instant};

#[derive(Default)]
pub(crate) struct ClipboardUi {
    scope: Option<(RuntimeHandle, SceneEpoch)>,
    shelf: ClipboardCoordinator,
}

impl ClipboardUi {
    pub(crate) fn clear(&mut self, handle: RuntimeHandle, scene: Option<SceneEpoch>) {
        if self
            .scope
            .is_some_and(|scope| scope.0 == handle && scene.is_none_or(|scene| scope.1 == scene))
        {
            self.scope = None;
            self.shelf.clear();
        }
    }

    pub(crate) fn perform(
        &mut self,
        handle: RuntimeHandle,
        action: ClipboardAction,
        runtime: Option<Arc<Runtime>>,
        now: Instant,
    ) -> Value {
        let scene = action.scene();
        if matches!(action, ClipboardAction::Clear { .. }) {
            self.clear(handle, Some(scene));
            return json!({"status":"ok"});
        }
        if matches!(action, ClipboardAction::Tick { .. }) {
            if self.scope == Some((handle, scene)) {
                self.shelf.metadata(now);
            }
            return json!({"status":"ok"});
        }
        if self.scope != Some((handle, scene)) {
            self.shelf.clear();
            self.scope = Some((handle, scene));
        }
        match action {
            ClipboardAction::List { .. } => {
                let cards = self.shelf.metadata(now).into_iter().map(|card| {
                    json!({
                        "id": card.id, "runtime":card.origin.runtime, "target":card.origin.target,
                        "bytes":card.bytes, "age_ms":now.saturating_duration_since(card.captured).as_millis()
                    })
                }).collect::<Vec<_>>();
                json!({"status":"ok","pending":self.shelf.pending(),"outcome":self.shelf.outcome(),"cards":cards})
            }
            ClipboardAction::Capture {
                runtime: source,
                focus,
                ..
            } => {
                if let Some(runtime) = runtime
                    && focus.target.scene == scene
                {
                    response(
                        self.shelf.request_capture(source, runtime, focus, now),
                        "pending",
                    )
                } else {
                    error("STALE")
                }
            }
            ClipboardAction::Paste { focus, card, .. } => {
                if let Some(runtime) = runtime
                    && focus.target.scene == scene
                {
                    response(
                        self.shelf.request_paste(card, runtime, focus, now),
                        "pending",
                    )
                } else {
                    error("STALE")
                }
            }
            ClipboardAction::Preview { card, .. } => match self.shelf.preview(card, now) {
                Ok(text) => {
                    let limit = text.char_indices().nth(4096).map(|(index, _)| index);
                    json!({"status":"ok","text":&text[..limit.unwrap_or(text.len())],
                           "truncated":limit.is_some()})
                }
                Err(reason) => error(reason),
            },
            ClipboardAction::Remove { card, .. } => response(self.shelf.remove(card, now), "ok"),
            ClipboardAction::Clear { .. } | ClipboardAction::Tick { .. } => {
                unreachable!("lifecycle handled before scope admission")
            }
        }
    }
}

fn response(outcome: Result<(), &'static str>, status: &'static str) -> Value {
    match outcome {
        Ok(()) => json!({"status":status}),
        Err(reason) => error(reason),
    }
}

pub(crate) fn error(reason: &'static str) -> Value {
    json!({"status":"error","reason":reason})
}
