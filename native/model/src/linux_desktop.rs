//! 通常 Linux の固定 GUI 選択と、起動 receipt / Wayland 所有 marker の照合。
//! app_id 単独では実行許可にしない。呼出元は同じ認証済み session の Started を渡す。
use crate::{
    ids::{CommandSeq, SessionId, TerminalId, WindowId},
    window::WindowLife,
    window_launch::Outcome,
};
use serde::{Deserialize, Serialize};

pub const APP_ID: &str = "gtk3-desktop";
const WINDOW_APP_ID: &str = "io.github.tinmachine.eidolon.linuxdesktop";

fn hex(value: &str, size: usize) -> bool {
    value.len() == size
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// 同じ helper が実際に検証した catalog からだけ受理する選択。argv / 任意 path は持たない。
#[derive(Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    pub scope: String,
    pub revision: String,
    pub app_id: String,
}
impl Selection {
    pub fn valid(&self) -> bool {
        hex(&self.scope, 64) && hex(&self.revision, 64) && self.app_id == APP_ID
    }
}
impl std::fmt::Debug for Selection {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("LinuxDesktopSelection(<private>)")
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Role {
    Main,
    Dialog,
}
pub fn main_app_id(nonce: &str) -> Option<String> {
    hex(nonce, 16).then(|| format!("{WINDOW_APP_ID}.r{nonce}{nonce}"))
}
pub fn dialog_app_id(nonce: &str) -> Option<String> {
    main_app_id(nonce).map(|main| format!("{main}.dialog"))
}
pub fn claim(app_id: &str, nonce: &str) -> Option<Role> {
    let main = main_app_id(nonce)?;
    if app_id == main {
        Some(Role::Main)
    } else if app_id.strip_suffix(".dialog") == Some(main.as_str()) {
        Some(Role::Dialog)
    } else {
        None
    }
}

#[derive(Clone, Copy, Debug)]
pub struct Candidate<'a> {
    pub window: WindowId,
    pub app_id: Option<&'a str>,
    pub parent: Option<WindowId>,
    pub life: WindowLife,
}
#[derive(Clone, Copy, Debug)]
pub struct Receipt<'a> {
    pub session: SessionId,
    pub request: CommandSeq,
    pub nonce: &'a str,
    pub started: bool,
    pub terminal: bool,
}

/// 一つの normal lease が持つ唯一の GUI 起動 ordinal。Termux receipt と共有しない。
fn launch_id() -> TerminalId {
    TerminalId::new(1).expect("fixed positive GUI launch ordinal")
}

pub fn correlate(
    session: SessionId,
    request: CommandSeq,
    nonce: &str,
    receipt: Option<Receipt<'_>>,
    windows: &[Candidate<'_>],
    before: &[WindowId],
) -> Outcome {
    let Some(receipt) = receipt.filter(|r| {
        r.session == session && r.request == request && r.nonce == nonce && hex(nonce, 16)
    }) else {
        return Outcome::AwaitingReceipt;
    };
    let terminal_id = launch_id();
    if receipt.terminal {
        return Outcome::Cancelled;
    }
    if !receipt.started {
        return Outcome::AwaitingAcceptance { terminal_id };
    }
    let mut roots = windows.iter().filter(|w| {
        w.life != WindowLife::Destroyed
            && w.parent.is_none()
            && w.app_id.and_then(|id| claim(id, nonce)) == Some(Role::Main)
    });
    let Some(root) = roots.next() else {
        return Outcome::AwaitingWindow { terminal_id };
    };
    if roots.next().is_some() {
        return Outcome::AmbiguousWindow { terminal_id };
    }
    if before.contains(&root.window) {
        return Outcome::PreexistingClaim { terminal_id };
    }
    if root.life != WindowLife::Mapped {
        return Outcome::AwaitingWindow { terminal_id };
    }
    Outcome::Matched {
        terminal_id,
        window: root.window,
    }
}

/// Started / request の照合後だけ使用する。Dialog は現在の一意な main の実際の xdg parent が必須。
pub fn owned_windows(nonce: &str, windows: &[Candidate<'_>]) -> Vec<WindowId> {
    let mut roots = windows.iter().filter(|w| {
        w.life != WindowLife::Destroyed
            && w.parent.is_none()
            && w.app_id.and_then(|id| claim(id, nonce)) == Some(Role::Main)
    });
    let Some(root) = roots.next() else {
        return vec![];
    };
    if roots.next().is_some() || root.life != WindowLife::Mapped {
        return vec![];
    }
    let mut owned = vec![root.window];
    owned.extend(
        windows
            .iter()
            .filter(|w| {
                w.life == WindowLife::Mapped
                    && w.parent == Some(root.window)
                    && w.app_id.and_then(|id| claim(id, nonce)) == Some(Role::Dialog)
            })
            .map(|w| w.window),
    );
    owned
}

#[cfg(test)]
mod tests {
    use super::*;
    const NONCE: &str = "0123456789abcdef";
    fn session(n: i64) -> SessionId {
        SessionId::new(n).unwrap()
    }
    fn request(n: i64) -> CommandSeq {
        CommandSeq::new(n).unwrap()
    }
    fn window(n: i64) -> WindowId {
        WindowId::new(n).unwrap()
    }
    fn receipt(started: bool) -> Receipt<'static> {
        Receipt {
            session: session(4),
            request: request(9),
            nonce: NONCE,
            started,
            terminal: false,
        }
    }
    #[test]
    fn fixed_catalog_selection_rejects_cross_backend_and_extra_fields() {
        let mut value = Selection {
            scope: "a".repeat(64),
            revision: "b".repeat(64),
            app_id: APP_ID.into(),
        };
        assert!(value.valid());
        value.app_id = "foot".into();
        assert!(!value.valid());
        value.app_id = APP_ID.into();
        value.scope = "A".repeat(64);
        assert!(!value.valid());
        assert!(
            serde_json::from_str::<Selection>(&format!(
                r#"{{"scope":"{}","revision":"{}","app_id":"gtk3-desktop","argv":["x"]}}"#,
                "a".repeat(64),
                "b".repeat(64)
            ))
            .is_err()
        );
    }
    #[test]
    fn map_before_started_is_held_and_scoped_receipt_is_required() {
        let main = main_app_id(NONCE).unwrap();
        let windows = [Candidate {
            window: window(7),
            app_id: Some(&main),
            parent: None,
            life: WindowLife::Mapped,
        }];
        let correlate = |r| super::correlate(session(4), request(9), NONCE, r, &windows, &[]);
        assert_eq!(correlate(None), Outcome::AwaitingReceipt);
        assert_eq!(
            correlate(Some(receipt(false))),
            Outcome::AwaitingAcceptance {
                terminal_id: launch_id()
            }
        );
        for stale in [
            Receipt {
                session: session(5),
                ..receipt(true)
            },
            Receipt {
                request: request(10),
                ..receipt(true)
            },
            Receipt {
                nonce: "ffffffffffffffff",
                ..receipt(true)
            },
        ] {
            assert_eq!(correlate(Some(stale)), Outcome::AwaitingReceipt);
        }
        assert_eq!(
            correlate(Some(receipt(true))),
            Outcome::Matched {
                terminal_id: launch_id(),
                window: window(7)
            }
        );
        // A terminal event in the same packet wins over irreversible Started evidence.
        assert_eq!(
            correlate(Some(Receipt {
                terminal: true,
                ..receipt(true)
            })),
            Outcome::Cancelled
        );
        assert_eq!(
            super::correlate(
                session(4),
                request(9),
                NONCE,
                Some(receipt(true)),
                &windows,
                &[window(7)]
            ),
            Outcome::PreexistingClaim {
                terminal_id: launch_id()
            }
        );
    }
    #[test]
    fn dialog_cannot_claim_main_and_requires_live_parent_in_same_nonce() {
        let main = main_app_id(NONCE).unwrap();
        let dialog = dialog_app_id(NONCE).unwrap();
        let stale = dialog_app_id("ffffffffffffffff").unwrap();
        let mut windows = vec![
            Candidate {
                window: window(1),
                app_id: Some(&main),
                parent: None,
                life: WindowLife::Mapped,
            },
            Candidate {
                window: window(2),
                app_id: Some(&dialog),
                parent: Some(window(1)),
                life: WindowLife::Mapped,
            },
            Candidate {
                window: window(3),
                app_id: Some(&dialog),
                parent: None,
                life: WindowLife::Mapped,
            },
            Candidate {
                window: window(4),
                app_id: Some(&stale),
                parent: Some(window(1)),
                life: WindowLife::Mapped,
            },
            // GTK may set the process app id before applying the dialog-specific id.
            Candidate {
                window: window(5),
                app_id: Some(&main),
                parent: Some(window(1)),
                life: WindowLife::Mapped,
            },
        ];
        assert_eq!(owned_windows(NONCE, &windows), vec![window(1), window(2)]);
        assert_eq!(
            correlate(
                session(4),
                request(9),
                NONCE,
                Some(receipt(true)),
                &windows,
                &[]
            ),
            Outcome::Matched {
                terminal_id: launch_id(),
                window: window(1)
            }
        );
        windows[0].life = WindowLife::Unmapped;
        assert!(owned_windows(NONCE, &windows).is_empty());
        windows[0].life = WindowLife::Mapped;
        windows.push(Candidate {
            window: window(6),
            app_id: Some(&main),
            parent: None,
            life: WindowLife::Mapped,
        });
        assert!(owned_windows(NONCE, &windows).is_empty());
        assert_eq!(
            correlate(
                session(4),
                request(9),
                NONCE,
                Some(receipt(true)),
                &windows,
                &[]
            ),
            Outcome::AmbiguousWindow {
                terminal_id: launch_id()
            }
        );
    }
    #[test]
    fn desktop_markers_do_not_accept_termux_or_suffix_aliases() {
        assert_eq!(claim(&main_app_id(NONCE).unwrap(), NONCE), Some(Role::Main));
        assert_eq!(
            claim(&dialog_app_id(NONCE).unwrap(), NONCE),
            Some(Role::Dialog)
        );
        for app in [
            format!("{}.dialog.extra", main_app_id(NONCE).unwrap()),
            format!("io.github.tinmachine.eidolon.gui.g-{NONCE}-1"),
            main_app_id("ffffffffffffffff").unwrap(),
        ] {
            assert_eq!(claim(&app, NONCE), None);
        }
    }
    #[test]
    fn parent_or_cookie_change_withdraws_admission_before_target_retirement() {
        let main = main_app_id(NONCE).unwrap();
        let dialog = dialog_app_id(NONCE).unwrap();
        let stale = main_app_id("ffffffffffffffff").unwrap();
        let root = Candidate {
            window: window(1),
            app_id: Some(&main),
            parent: None,
            life: WindowLife::Mapped,
        };
        let child = Candidate {
            window: window(2),
            app_id: Some(&dialog),
            parent: Some(window(1)),
            life: WindowLife::Mapped,
        };
        assert_eq!(
            owned_windows(NONCE, &[root, child]),
            vec![window(1), window(2)]
        );
        assert_eq!(
            owned_windows(
                NONCE,
                &[
                    root,
                    Candidate {
                        parent: Some(window(9)),
                        ..child
                    }
                ]
            ),
            vec![window(1)]
        );
        assert!(
            owned_windows(
                NONCE,
                &[
                    Candidate {
                        app_id: Some(&stale),
                        ..root
                    },
                    child
                ]
            )
            .is_empty()
        );
        assert!(
            owned_windows(
                NONCE,
                &[
                    Candidate {
                        parent: Some(window(9)),
                        ..root
                    },
                    child
                ]
            )
            .is_empty()
        );
        assert_eq!(
            owned_windows(NONCE, &[root, child]),
            vec![window(1), window(2)]
        );
    }
}
