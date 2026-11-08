//! launch receiptとtoplevel markerの照合。forward/session/nonceの所有境界は呼出元で先に検査する。
use crate::{
    catalog::Mode,
    ids::{CommandSeq, SessionId, TerminalId, WindowId},
    window::WindowLife,
    workload::claimed_terminal,
};
use serde::Serialize;

#[derive(Clone, Copy, Debug, Serialize)]
pub struct TerminalLaunch {
    pub terminal_id: TerminalId,
    pub request_id: Option<CommandSeq>,
    pub accepted: Option<bool>,
    pub mode: Mode,
}

const GUI_APP_ID: &str = "io.github.tinmachine.eidolon.gui";
fn valid_nonce(nonce: &str) -> bool {
    nonce.len() == 16
        && nonce
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub fn gui_app_id(nonce: &str, launch: TerminalId) -> Option<String> {
    valid_nonce(nonce).then(|| format!("{GUI_APP_ID}.g-{nonce}-{}", launch.get()))
}
fn claimed_gui(app_id: &str, nonce: &str) -> Option<TerminalId> {
    if !valid_nonce(nonce) {
        return None;
    }
    let text = app_id.strip_prefix(&format!("{GUI_APP_ID}.g-{nonce}-"))?;
    let id = TerminalId::new(text.parse().ok()?).ok()?;
    (id.get().to_string() == text).then_some(id)
}
#[derive(Clone, Copy, Debug)]
pub struct Candidate<'a> {
    pub window: WindowId,
    pub app_id: Option<&'a str>,
    pub life: WindowLife,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "phase", rename_all = "snake_case")]
pub enum Outcome {
    Queued,
    AwaitingReceipt,
    AwaitingAcceptance {
        terminal_id: TerminalId,
    },
    AwaitingWindow {
        terminal_id: TerminalId,
    },
    Rejected {
        terminal_id: TerminalId,
    },
    AmbiguousReceipt,
    AmbiguousWindow {
        terminal_id: TerminalId,
    },
    PreexistingClaim {
        terminal_id: TerminalId,
    },
    Matched {
        terminal_id: TerminalId,
        window: WindowId,
    },
    Cancelled,
}
impl Outcome {
    pub fn completed(self) -> bool {
        matches!(
            self,
            Self::Rejected { .. } | Self::Matched { .. } | Self::Cancelled
        )
    }
}
#[derive(Clone, Copy, Debug, Serialize)]
pub struct Report {
    pub session: SessionId,
    pub request_id: CommandSeq,
    #[serde(flatten)]
    pub outcome: Outcome,
}

/// app_idだけでは完了にしない。曖昧なreceipt/claim、旧window、未mappedを別状態として保持する。
/// popupはWindowIdを持つこの候補列へ入れず、compositorの通常toplevel列だけを渡す。
pub fn correlate(
    nonce: &str,
    request: Option<CommandSeq>,
    launches: &[TerminalLaunch],
    windows: &[Candidate<'_>],
    before: &[WindowId],
) -> Outcome {
    let mut receipts = launches.iter().filter(|r| r.request_id == request);
    let Some(receipt) = receipts.next() else {
        return Outcome::AwaitingReceipt;
    };
    if receipts.next().is_some() {
        return Outcome::AmbiguousReceipt;
    }
    let terminal_id = receipt.terminal_id;
    match receipt.accepted {
        None => return Outcome::AwaitingAcceptance { terminal_id },
        Some(false) => return Outcome::Rejected { terminal_id },
        Some(true) => {}
    }
    let mut claims = windows.iter().filter(|w| {
        w.life != WindowLife::Destroyed
            && w.app_id.and_then(|id| match receipt.mode {
                Mode::Terminal => claimed_terminal(id, nonce),
                Mode::Gui => claimed_gui(id, nonce),
            }) == Some(terminal_id)
    });
    let Some(claim) = claims.next() else {
        return Outcome::AwaitingWindow { terminal_id };
    };
    if claims.next().is_some() {
        return Outcome::AmbiguousWindow { terminal_id };
    }
    if before.contains(&claim.window) {
        return Outcome::PreexistingClaim { terminal_id };
    }
    if claim.life != WindowLife::Mapped {
        return Outcome::AwaitingWindow { terminal_id };
    }
    Outcome::Matched {
        terminal_id,
        window: claim.window,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workload::terminal_app_id;
    const NONCE: &str = "0123456789abcdef";
    fn request(n: i64) -> CommandSeq {
        CommandSeq::new(n).unwrap()
    }
    fn terminal(n: i64) -> TerminalId {
        TerminalId::new(n).unwrap()
    }
    fn window(n: i64) -> WindowId {
        WindowId::new(n).unwrap()
    }
    fn receipt(req: i64, id: i64, accepted: Option<bool>) -> TerminalLaunch {
        TerminalLaunch {
            terminal_id: terminal(id),
            request_id: Some(request(req)),
            accepted,
            mode: Mode::Terminal,
        }
    }
    #[test]
    fn reversed_mapping_matches_each_original_request_not_first_new_window() {
        let a = terminal_app_id(NONCE, terminal(1)).unwrap();
        let b = terminal_app_id(NONCE, terminal(2)).unwrap();
        let launches = [receipt(10, 1, Some(true)), receipt(20, 2, Some(true))];
        let candidates = [
            Candidate {
                window: window(6),
                app_id: Some(&b),
                life: WindowLife::Mapped,
            },
            Candidate {
                window: window(9),
                app_id: Some(&a),
                life: WindowLife::Mapped,
            },
        ];
        assert_eq!(
            correlate(NONCE, Some(request(10)), &launches, &candidates, &[]),
            Outcome::Matched {
                terminal_id: terminal(1),
                window: window(9)
            }
        );
        assert_eq!(
            correlate(NONCE, Some(request(20)), &launches, &candidates, &[]),
            Outcome::Matched {
                terminal_id: terminal(2),
                window: window(6)
            }
        );
    }
    #[test]
    fn unrelated_dialog_other_nonce_and_preexisting_window_never_complete_launch() {
        let app = terminal_app_id(NONCE, terminal(1)).unwrap();
        let foreign = terminal_app_id("fedcba9876543210", terminal(1)).unwrap();
        let launches = [receipt(10, 1, Some(true))];
        for id in ["org.example.dialog", &foreign] {
            let windows = [Candidate {
                window: window(1),
                app_id: Some(id),
                life: WindowLife::Mapped,
            }];
            assert_eq!(
                correlate(NONCE, Some(request(10)), &launches, &windows, &[]),
                Outcome::AwaitingWindow {
                    terminal_id: terminal(1)
                }
            );
        }
        let windows = [Candidate {
            window: window(1),
            app_id: Some(&app),
            life: WindowLife::Mapped,
        }];
        assert_eq!(
            correlate(NONCE, Some(request(10)), &launches, &windows, &[window(1)]),
            Outcome::PreexistingClaim {
                terminal_id: terminal(1)
            }
        );
    }
    #[test]
    fn receipt_missing_unaccepted_failed_and_other_request_are_distinct() {
        let app = terminal_app_id(NONCE, terminal(1)).unwrap();
        let windows = [Candidate {
            window: window(1),
            app_id: Some(&app),
            life: WindowLife::Mapped,
        }];
        assert_eq!(
            correlate(NONCE, Some(request(10)), &[], &windows, &[]),
            Outcome::AwaitingReceipt
        );
        assert_eq!(
            correlate(
                NONCE,
                Some(request(10)),
                &[receipt(11, 1, Some(true))],
                &windows,
                &[]
            ),
            Outcome::AwaitingReceipt
        );
        assert_eq!(
            correlate(
                NONCE,
                Some(request(10)),
                &[receipt(10, 1, None)],
                &windows,
                &[]
            ),
            Outcome::AwaitingAcceptance {
                terminal_id: terminal(1)
            }
        );
        assert_eq!(
            correlate(
                NONCE,
                Some(request(10)),
                &[receipt(10, 1, Some(false))],
                &windows,
                &[]
            ),
            Outcome::Rejected {
                terminal_id: terminal(1)
            }
        );
    }
    #[test]
    fn duplicate_claim_even_unmapped_or_duplicate_receipt_is_fail_closed() {
        let app = terminal_app_id(NONCE, terminal(1)).unwrap();
        let launch = receipt(10, 1, Some(true));
        let windows = [
            Candidate {
                window: window(1),
                app_id: Some(&app),
                life: WindowLife::Mapped,
            },
            Candidate {
                window: window(2),
                app_id: Some(&app),
                life: WindowLife::Configured,
            },
        ];
        assert_eq!(
            correlate(NONCE, Some(request(10)), &[launch], &windows, &[]),
            Outcome::AmbiguousWindow {
                terminal_id: terminal(1)
            }
        );
        assert_eq!(
            correlate(
                NONCE,
                Some(request(10)),
                &[launch, launch],
                &windows[..1],
                &[]
            ),
            Outcome::AmbiguousReceipt
        );
    }
    #[test]
    fn mapping_before_receipt_waits_and_unmapped_or_destroyed_is_not_ready() {
        let app = terminal_app_id(NONCE, terminal(1)).unwrap();
        let launches = [receipt(10, 1, Some(true))];
        for life in [
            WindowLife::Created,
            WindowLife::AwaitingInitialCommit,
            WindowLife::Configured,
            WindowLife::Unmapped,
            WindowLife::Destroyed,
        ] {
            let candidates = [Candidate {
                window: window(1),
                app_id: Some(&app),
                life,
            }];
            assert_eq!(
                correlate(NONCE, Some(request(10)), &launches, &candidates, &[]),
                Outcome::AwaitingWindow {
                    terminal_id: terminal(1)
                }
            );
        }
    }
    #[test]
    fn initial_terminal_has_no_open_request_and_must_not_match_later_request() {
        let app = terminal_app_id(NONCE, terminal(1)).unwrap();
        let launches = [TerminalLaunch {
            terminal_id: terminal(1),
            request_id: None,
            accepted: Some(true),
            mode: Mode::Terminal,
        }];
        let candidates = [Candidate {
            window: window(1),
            app_id: Some(&app),
            life: WindowLife::Mapped,
        }];
        assert_eq!(
            correlate(NONCE, None, &launches, &candidates, &[]),
            Outcome::Matched {
                terminal_id: terminal(1),
                window: window(1)
            }
        );
        assert_eq!(
            correlate(NONCE, Some(request(10)), &launches, &candidates, &[]),
            Outcome::AwaitingReceipt
        );
    }
    #[test]
    fn report_is_request_scoped_and_preserves_the_existing_wire_fields() {
        let value = serde_json::to_value(Report {
            session: SessionId::new(7).unwrap(),
            request_id: request(i64::MAX),
            outcome: Outcome::Matched {
                terminal_id: terminal(2),
                window: window(3),
            },
        })
        .unwrap();
        assert_eq!(
            value,
            serde_json::json!({"session":"7","request_id":"9223372036854775807","phase":"matched","terminal_id":"2","window":"3"})
        );
        assert!(!Outcome::AmbiguousReceipt.completed());
        assert!(!Outcome::AwaitingReceipt.completed());
        assert!(Outcome::Cancelled.completed());
    }
    #[test]
    fn gui_cookie_requires_gui_receipt_and_does_not_claim_a_terminal_workload() {
        let gui = gui_app_id(NONCE, terminal(2)).unwrap();
        let foot = terminal_app_id(NONCE, terminal(2)).unwrap();
        assert_eq!(claimed_terminal(&gui, NONCE), None);
        let mut launch = receipt(10, 2, Some(true));
        launch.mode = Mode::Gui;
        for wrong in [
            &foot,
            "io.github.tinmachine.eidolon.gui.g-0123456789abcdef-02",
            "io.github.tinmachine.eidolon.gui.g-fedcba9876543210-2",
        ] {
            assert_eq!(
                correlate(
                    NONCE,
                    Some(request(10)),
                    &[launch],
                    &[Candidate {
                        window: window(7),
                        app_id: Some(wrong),
                        life: WindowLife::Mapped
                    }],
                    &[]
                ),
                Outcome::AwaitingWindow {
                    terminal_id: terminal(2)
                }
            );
        }
        let candidate = Candidate {
            window: window(7),
            app_id: Some(&gui),
            life: WindowLife::Mapped,
        };
        assert_eq!(
            correlate(NONCE, Some(request(10)), &[launch], &[candidate], &[]),
            Outcome::Matched {
                terminal_id: terminal(2),
                window: window(7)
            }
        );
        assert_eq!(
            correlate(
                NONCE,
                Some(request(10)),
                &[launch],
                &[candidate],
                &[window(7)]
            ),
            Outcome::PreexistingClaim {
                terminal_id: terminal(2)
            }
        );
        launch.mode = Mode::Terminal;
        assert_eq!(
            correlate(NONCE, Some(request(10)), &[launch], &[candidate], &[]),
            Outcome::AwaitingWindow {
                terminal_id: terminal(2)
            }
        );
    }
}
