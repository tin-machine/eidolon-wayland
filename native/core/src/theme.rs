//! テーマの選択、CPU compile、GPU適用receipt、永続化を別段階として管理する。
use crate::theme_store::{Store, Value};
use eidolon_model::{commands::ThemeSelection, state::Status};
use eidolon_theme::{
    loader::{Generation, Loader},
    raster::CompiledTheme,
};
use serde::Serialize;
use std::{
    path::{Path, PathBuf},
    sync::Arc,
};

/// size省略時の基準解像度。window別の実slotにはGLで拡縮し、毎frame SVGを解析しない。
fn raster_slots() -> std::collections::BTreeMap<eidolon_theme::manifest::Slot, [u32; 2]> {
    use eidolon_theme::manifest::Slot;
    std::collections::BTreeMap::from([
        (Slot::WindowBackdrop, [1280, 1280]),
        (Slot::WindowFrame, [1024, 768]),
        (Slot::TitlebarEnd, [24, 24]),
        (Slot::TitlebarBottom, [1024, 3]),
    ])
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub selection: ThemeSelection,
    pub requested: ThemeSelection,
    pub reduced_motion: bool,
    pub imported_available: bool,
    pub loading: bool,
    pub import_token: Option<String>,
    pub applied_revision: u64,
    pub error: Option<String>,
    pub storage: crate::theme_store::Report,
    pub workers: usize,
}
struct Pending {
    selection: ThemeSelection,
    reduced_motion: bool,
    generation: Option<Generation>,
    revision: Option<u64>,
    theme: Option<Arc<CompiledTheme>>,
    source: Option<Arc<[u8]>>,
}
pub struct Controller {
    loader: Loader,
    store: Store,
    value: Value,
    pending: Option<Pending>,
    files: Vec<PathBuf>,
    report: Report,
    stopping: bool,
}
impl Controller {
    pub fn start(
        parent: Option<&Path>,
        render: &eidolon_render::Controller,
    ) -> std::io::Result<Self> {
        let loader = Loader::start(render.pixel_budget(), Default::default())?;
        let (store, value) = Store::start(parent)?;
        let selection = value.selection;
        let reduced = value.reduced_motion;
        let mut controller = Self {
            loader,
            store,
            value,
            pending: None,
            files: vec![],
            report: Report::default(),
            stopping: false,
        };
        controller.value.selection = ThemeSelection::Off;
        controller.report.imported_available = controller.value.imported.is_some();
        if selection != ThemeSelection::Off || reduced {
            controller.select(selection, reduced, render);
        }
        Ok(controller)
    }
    fn fail(&mut self, error: impl ToString) -> Status {
        self.report.error = Some(error.to_string().chars().take(256).collect());
        self.report.loading = false;
        self.pending = None;
        self.report.import_token = None;
        Status::Failed
    }
    fn acknowledge(&mut self, snapshot: &eidolon_render::Snapshot) {
        let Some(pending) = self.pending.as_ref() else {
            return;
        };
        let Some(revision) = pending.revision else {
            return;
        };
        if snapshot.theme_revision == revision {
            let pending = self.pending.take().unwrap();
            self.value.selection = pending.selection;
            self.value.reduced_motion = pending.reduced_motion;
            if pending.selection == ThemeSelection::Imported {
                self.value.imported = pending.source;
            }
            self.report.selection = self.value.selection;
            self.report.reduced_motion = self.value.reduced_motion;
            self.report.applied_revision = revision;
            self.report.imported_available = self.value.imported.is_some();
            self.report.import_token = None;
            self.report.loading = false;
            self.report.error = None;
            self.store.save(self.value.clone());
        } else if snapshot.theme_attempted_revision == revision {
            self.fail(snapshot.theme_error.unwrap_or("THEME_GPU_FAILED"));
        }
    }
    fn cancel(&mut self, render: &eidolon_render::Controller) -> Result<(), Status> {
        // GPU側の採用と同じlockで取るreceipt。新しい要求の直前に適用された候補を失わない。
        let receipt = render.cancel_pending_theme()?;
        self.acknowledge(&receipt);
        self.loader.cancel().map_err(|_| Status::Failed)?;
        self.pending = None;
        self.report.import_token = None;
        self.report.loading = false;
        self.report.error = None;
        Ok(())
    }
    pub fn select(
        &mut self,
        selection: ThemeSelection,
        reduced: bool,
        render: &eidolon_render::Controller,
    ) -> Status {
        if self.stopping {
            return Status::NotReady;
        }
        if let Err(status) = self.cancel(render) {
            return status;
        }
        if selection == ThemeSelection::Imported && self.value.imported.is_none() {
            return self.fail("THEME_IMPORT_MISSING");
        }
        self.report.requested = selection;
        self.report.loading = true;
        let mut pending = Pending {
            selection,
            reduced_motion: reduced,
            generation: None,
            revision: None,
            theme: None,
            source: None,
        };
        if selection == ThemeSelection::Off {
            match render.request_theme(None, reduced) {
                Ok(revision) => pending.revision = Some(revision),
                Err(status) => {
                    self.report.loading = false;
                    return status;
                }
            }
        } else {
            let result = match selection {
                ThemeSelection::Builtin => self
                    .loader
                    .request(eidolon_theme::cyan_orbit(), raster_slots()),
                ThemeSelection::Imported => self.loader.request_package(
                    self.value.imported.as_ref().unwrap().clone(),
                    raster_slots(),
                ),
                ThemeSelection::Off => unreachable!(),
            };
            match result {
                Ok(generation) => pending.generation = Some(generation),
                Err(error) => return self.fail(error.0),
            }
        }
        self.pending = Some(pending);
        Status::Accepted
    }
    pub fn begin_import(&mut self, token: &str, render: &eidolon_render::Controller) -> Status {
        if self.stopping || self.store.root.is_none() {
            return Status::NotReady;
        }
        if let Err(status) = self.cancel(render) {
            return status;
        }
        self.report.import_token = Some(token.into());
        self.report.requested = ThemeSelection::Imported;
        self.report.loading = true;
        Status::Accepted
    }
    pub fn apply_import(&mut self, token: &str) -> Status {
        if self.stopping {
            return Status::NotReady;
        }
        if self.report.import_token.as_deref() != Some(token) || self.pending.is_some() {
            return Status::Stale;
        }
        let Some(root) = &self.store.root else {
            return Status::NotReady;
        };
        let path = root.join(format!("{token}.bundle"));
        let result =
            self.loader
                .request_file(path.clone(), unsafe { libc::getuid() }, raster_slots());
        self.files.push(path);
        match result {
            Ok(generation) => {
                self.pending = Some(Pending {
                    selection: ThemeSelection::Imported,
                    reduced_motion: self.value.reduced_motion,
                    generation: Some(generation),
                    revision: None,
                    theme: None,
                    source: None,
                });
                Status::Accepted
            }
            Err(error) => self.fail(error.0),
        }
    }
    pub fn cancel_import(&mut self, token: &str, render: &eidolon_render::Controller) -> Status {
        if self.report.import_token.as_deref() != Some(token) {
            return Status::Stale;
        }
        self.cancel(render).map_or_else(|s| s, |_| Status::Accepted)
    }
    /// core共有lockの外から呼ぶ。入力fileの回収もここで行う。
    pub fn poll(&mut self, render: &eidolon_render::Controller) {
        self.acknowledge(&render.snapshot());
        if let Some(completion) = self.loader.take_ready()
            && self
                .pending
                .as_ref()
                .is_some_and(|p| p.generation == Some(completion.generation))
        {
            match completion.result {
                Ok(theme) => {
                    let pending = self.pending.as_mut().unwrap();
                    match render.request_theme(Some(theme.clone()), pending.reduced_motion) {
                        Ok(revision) => {
                            pending.theme = Some(theme);
                            pending.source = completion.source;
                            pending.revision = Some(revision);
                            pending.generation = None;
                        }
                        Err(_) => {
                            self.fail("THEME_RENDER_UNAVAILABLE");
                        }
                    }
                }
                Err(error) => {
                    self.fail(error.0);
                }
            }
        }
        // 最新のcompile対象以外は不要。inflight FDが開いていればunlink後も読める。
        let retained = self
            .pending
            .as_ref()
            .filter(|p| p.generation.is_some())
            .and(self.report.import_token.as_ref())
            .and_then(|t| {
                self.store
                    .root
                    .as_ref()
                    .map(|r| r.join(format!("{t}.bundle")))
            });
        self.files.retain(|path| {
            if Some(path) == retained.as_ref() {
                return true;
            }
            let _ = std::fs::remove_file(path);
            false
        });
        self.report.storage = self.store.report();
        self.report.workers =
            usize::from(!self.loader.is_finished()) + usize::from(!self.store.finished());
    }
    pub fn report(&self) -> Report {
        self.report.clone()
    }
    pub fn stop(&mut self) {
        if !self.stopping {
            self.stopping = true;
            self.loader.request_stop();
            self.store.stop();
            self.pending = None;
            self.report.loading = false;
            self.report.import_token = None;
        }
    }
    pub fn finished(&self) -> bool {
        self.loader.is_finished() && self.store.finished()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::Write,
        os::unix::fs::{DirBuilderExt, OpenOptionsExt},
        time::{Duration, Instant},
    };
    #[test]
    fn omitted_asset_size_uses_bounded_host_slot_resolution() {
        let source=eidolon_theme::raster::Bundle::new([
            ("theme.toml".into(),b"format_version=1\nid='sized'\nname='Sized'\nstyle='style.css'\n[parts.a]\nkind='svg'\nslot='window-backdrop'\nsrc='a.svg'\n[parts.b]\nkind='svg'\nslot='titlebar-end'\nsrc='a.svg'".to_vec()),
            ("style.css".into(),b"#a {opacity:1;} #b {opacity:1;}".to_vec()),
            ("a.svg".into(),br#"<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="cyan"/></svg>"#.to_vec()),
        ]).unwrap();
        let pixels = eidolon_model::pixels::PixelBudget::default();
        let theme =
            eidolon_theme::raster::compile(&source, &raster_slots(), &pixels, &Default::default())
                .unwrap();
        assert_eq!(theme.parts.len(), 2);
        assert!(pixels.used_bytes() > 1280 * 1280 * 4 && pixels.used_bytes() < 7 * 1024 * 1024);
        drop(theme);
        assert_eq!(pixels.used_bytes(), 0);
    }
    struct Directory(PathBuf);
    impl Directory {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "eidolon-theme-runtime-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            std::fs::DirBuilder::new()
                .mode(0o700)
                .create(&root)
                .unwrap();
            Self(root)
        }
        fn bundle(&self, token: &str, bytes: &[u8]) {
            std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(self.0.join("themes").join(format!("{token}.bundle")))
                .unwrap()
                .write_all(bytes)
                .unwrap();
        }
    }
    impl Drop for Directory {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    fn settle(controller: &mut Controller, render: &eidolon_render::Controller) {
        let deadline = Instant::now() + Duration::from_secs(8);
        loop {
            controller.poll(render);
            if !controller.report.loading && !controller.report.storage.pending {
                break;
            }
            assert!(Instant::now() < deadline, "{:?}", controller.report);
            std::thread::sleep(Duration::from_millis(2));
        }
    }
    #[test]
    fn import_failure_preserves_applied_theme_and_off_retains_import_across_restart() {
        let directory = Directory::new();
        let render = eidolon_render::Controller::start().unwrap();
        let mut controller = Controller::start(Some(&directory.0), &render).unwrap();
        assert_eq!(
            controller.select(ThemeSelection::Builtin, false, &render),
            Status::Accepted
        );
        settle(&mut controller, &render);
        assert_eq!(controller.report.selection, ThemeSelection::Builtin);
        let valid = "11111111111111111111111111111111";
        assert_eq!(controller.begin_import(valid, &render), Status::Accepted);
        directory.bundle(
            valid,
            &eidolon_theme::package::encode(&eidolon_theme::cyan_orbit()),
        );
        assert_eq!(controller.apply_import(valid), Status::Accepted);
        settle(&mut controller, &render);
        assert_eq!(controller.report.selection, ThemeSelection::Imported);
        assert!(controller.report.imported_available);
        assert!(
            !directory
                .0
                .join("themes")
                .join(format!("{valid}.bundle"))
                .exists()
        );
        let applied = controller.report.applied_revision;
        let bad = "22222222222222222222222222222222";
        controller.begin_import(bad, &render);
        directory.bundle(bad, b"broken");
        controller.apply_import(bad);
        settle(&mut controller, &render);
        assert!(controller.report.error.is_some());
        assert_eq!(controller.report.selection, ThemeSelection::Imported);
        assert_eq!(controller.report.applied_revision, applied);
        controller.select(ThemeSelection::Off, true, &render);
        settle(&mut controller, &render);
        assert!(render.snapshot().theme_id.is_none());
        assert!(controller.report.imported_available);
        controller.stop();
        drop(controller);
        let mut restored = Controller::start(Some(&directory.0), &render).unwrap();
        settle(&mut restored, &render);
        assert_eq!(restored.report.selection, ThemeSelection::Off);
        assert!(
            restored.report.reduced_motion
                && restored.report.imported_available
                && restored.report.storage.loaded
        );
        restored.select(ThemeSelection::Imported, false, &render);
        settle(&mut restored, &render);
        assert_eq!(restored.report.selection, ThemeSelection::Imported);
        assert_eq!(render.snapshot().theme_id.as_deref(), Some("cyan-orbit"));
        restored.stop();
        drop(restored);
        render.request_stop();
    }
    #[test]
    fn new_selection_cancels_file_import_and_late_token_cannot_restore_it() {
        let directory = Directory::new();
        let render = eidolon_render::Controller::start().unwrap();
        let mut controller = Controller::start(Some(&directory.0), &render).unwrap();
        let old = "11111111111111111111111111111111";
        let new = "22222222222222222222222222222222";
        controller.begin_import(old, &render);
        controller.begin_import(new, &render);
        assert_eq!(controller.apply_import(old), Status::Stale);
        assert_eq!(controller.cancel_import(old, &render), Status::Stale);
        directory.bundle(
            new,
            &eidolon_theme::package::encode(&eidolon_theme::cyan_orbit()),
        );
        controller.apply_import(new);
        controller.select(ThemeSelection::Off, false, &render);
        settle(&mut controller, &render);
        assert_eq!(controller.apply_import(new), Status::Stale);
        assert_eq!(controller.report.selection, ThemeSelection::Off);
        assert!(render.snapshot().theme_id.is_none());
        controller.stop();
        drop(controller);
        render.request_stop();
    }
}
