//! Explicit transfers between runtime owners. Card contents never enter runtime diagnostics.

use crate::{
    clipboard_shelf::{
        ClipboardFocus, ClipboardGrant, ClipboardOperation, ClipboardPermit, ClipboardReply,
        ClipboardResponse,
    },
    compositor::protocol::SelectionCapture,
    runtime::Runtime,
};
use eidolon_model::{
    clipboard_shelf::{ClipError, ClipMetadata, ClipOrigin, ClipboardShelf},
    ids::{ClipboardCardId, RuntimeHandle},
    state::Status,
};
use std::{
    sync::{Arc, mpsc::TryRecvError},
    time::{Duration, Instant},
};

const REQUEST_TIMEOUT: Duration = Duration::from_secs(6);

struct Owner {
    runtime: Arc<Runtime>,
    focus: ClipboardFocus,
}

struct Offer {
    card: ClipboardCardId,
    permit: ClipboardPermit,
}

enum Progress {
    Starting(ClipOrigin, ClipboardResponse),
    Reading(ClipOrigin, SelectionCapture),
    Offering(Offer, ClipboardResponse),
}

struct Pending {
    owner: Owner,
    until: Instant,
    progress: Progress,
}

#[derive(Default)]
pub struct ClipboardCoordinator {
    shelf: ClipboardShelf,
    pending: Option<Pending>,
    offer: Option<Offer>,
    outcome: Option<&'static str>,
}

impl ClipboardCoordinator {
    pub fn request_capture(
        &mut self,
        handle: RuntimeHandle,
        runtime: Arc<Runtime>,
        focus: ClipboardFocus,
        now: Instant,
    ) -> Result<(), &'static str> {
        self.advance(now);
        if self.pending.is_some() {
            return Err("BUSY");
        }
        let response = runtime
            .request_clipboard(ClipboardOperation::Capture(focus))
            .map_err(runtime_error)?;
        self.outcome = None;
        let origin = ClipOrigin {
            runtime: handle,
            target: focus.target,
        };
        self.pending = Some(Pending {
            owner: Owner { runtime, focus },
            until: now + REQUEST_TIMEOUT,
            progress: Progress::Starting(origin, response),
        });
        Ok(())
    }

    pub fn request_paste(
        &mut self,
        card: ClipboardCardId,
        runtime: Arc<Runtime>,
        focus: ClipboardFocus,
        now: Instant,
    ) -> Result<(), &'static str> {
        self.advance(now);
        if self.pending.is_some() {
            return Err("BUSY");
        }
        let metadata = self
            .shelf
            .metadata(now)
            .into_iter()
            .find(|item| item.id == card)
            .ok_or("MISSING")?;
        let text = Arc::from(self.shelf.text(card, now).map_err(shelf_error)?);
        let permit = ClipboardPermit::new(metadata.expires_at());
        let response = runtime
            .request_clipboard(ClipboardOperation::Grant {
                focus,
                grant: ClipboardGrant {
                    card,
                    text,
                    permit: permit.clone(),
                },
            })
            .map_err(runtime_error)?;
        self.revoke_offer();
        self.outcome = None;
        self.pending = Some(Pending {
            owner: Owner { runtime, focus },
            until: now + REQUEST_TIMEOUT,
            progress: Progress::Offering(Offer { card, permit }, response),
        });
        Ok(())
    }

    pub fn metadata(&mut self, now: Instant) -> Vec<ClipMetadata> {
        self.advance(now);
        self.shelf.metadata(now)
    }

    pub fn preview(&mut self, card: ClipboardCardId, now: Instant) -> Result<&str, &'static str> {
        self.advance(now);
        self.shelf.text(card, now).map_err(shelf_error)
    }

    pub fn remove(&mut self, card: ClipboardCardId, now: Instant) -> Result<(), &'static str> {
        self.advance(now);
        self.shelf.remove(card, now).map_err(shelf_error)?;
        if self.offer.as_ref().is_some_and(|offer| offer.card == card) {
            self.revoke_offer();
        }
        if self.pending.as_ref().is_some_and(|pending| {
            matches!(&pending.progress, Progress::Offering(offer, _) if offer.card == card)
        }) && let Some(pending) = self.pending.take() {
            self.cancel(pending, "CANCELLED");
        }
        Ok(())
    }

    pub fn clear(&mut self) {
        if let Some(pending) = self.pending.take() {
            self.cancel(pending, "CANCELLED");
        }
        self.revoke_offer();
        self.shelf.clear();
        self.outcome = None;
    }

    pub fn pending(&self) -> bool {
        self.pending.is_some()
    }

    pub fn outcome(&self) -> Option<&'static str> {
        self.outcome
    }

    fn revoke_offer(&mut self) {
        if let Some(offer) = self.offer.take() {
            offer.permit.revoke();
        }
    }

    fn cancel(&mut self, pending: Pending, error: &'static str) {
        if let Progress::Offering(offer, _) = pending.progress {
            offer.permit.revoke();
        }
        self.outcome = Some(error);
    }

    fn advance(&mut self, now: Instant) {
        if self
            .offer
            .as_ref()
            .is_some_and(|offer| !offer.permit.is_live(now))
        {
            self.revoke_offer();
        }
        let Some(Pending {
            owner,
            until,
            progress,
        }) = self.pending.take()
        else {
            return;
        };
        if now >= until || !owner.runtime.clipboard_focus_current(owner.focus) {
            self.cancel(
                Pending {
                    owner,
                    until,
                    progress,
                },
                if now >= until { "TIMEOUT" } else { "STALE" },
            );
            return;
        }
        let progress = match progress {
            Progress::Starting(origin, response) => match response.try_recv() {
                Ok(Ok(ClipboardReply::Capturing(capture))) => Progress::Reading(origin, capture),
                Err(TryRecvError::Empty) => Progress::Starting(origin, response),
                result => {
                    self.outcome = Some(result_error(result));
                    return;
                }
            },
            Progress::Reading(origin, capture) => match capture.try_recv() {
                Ok(Ok(text)) => {
                    self.outcome = Some(if owner.runtime.clipboard_focus_current(owner.focus) {
                        self.shelf
                            .capture(origin, text, now)
                            .map(|_| "CAPTURED")
                            .unwrap_or_else(shelf_error)
                    } else {
                        "STALE"
                    });
                    return;
                }
                Err(TryRecvError::Empty) => Progress::Reading(origin, capture),
                result => {
                    self.outcome = Some(result_error(result));
                    return;
                }
            },
            Progress::Offering(offer, response) => match response.try_recv() {
                Ok(Ok(ClipboardReply::Granted)) => {
                    if offer.permit.is_live(now)
                        && owner.runtime.clipboard_focus_current(owner.focus)
                    {
                        self.offer = Some(offer);
                        self.outcome = Some("OFFERED");
                    } else {
                        offer.permit.revoke();
                        self.outcome = Some("STALE");
                    }
                    return;
                }
                Err(TryRecvError::Empty) => Progress::Offering(offer, response),
                result => {
                    offer.permit.revoke();
                    self.outcome = Some(result_error(result));
                    return;
                }
            },
        };
        self.pending = Some(Pending {
            owner,
            until,
            progress,
        });
    }
}

impl Drop for ClipboardCoordinator {
    fn drop(&mut self) {
        self.clear();
    }
}

fn result_error<T>(result: Result<Result<T, &'static str>, TryRecvError>) -> &'static str {
    match result {
        Ok(Err(error)) => error,
        Err(TryRecvError::Disconnected) => "STALE",
        _ => "INVALID_RESPONSE",
    }
}

fn runtime_error(error: Status) -> &'static str {
    match error {
        Status::Busy => "BUSY",
        Status::Stale => "STALE",
        _ => "NOT_READY",
    }
}

fn shelf_error(error: ClipError) -> &'static str {
    match error {
        ClipError::Empty => "EMPTY",
        ClipError::TooLarge => "TOO_LARGE",
        ClipError::InvalidUtf8 => "INVALID_UTF8",
        ClipError::ShelfFull => "SHELF_FULL",
        ClipError::Missing => "MISSING",
        ClipError::Exhausted => "ID_EXHAUSTED",
    }
}
