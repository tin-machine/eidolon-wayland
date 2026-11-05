//! CPU compile専用worker。実行中一件、待機最新一件、完成最新一件のみを保持する。
//! adoption/GL uploadは呼出側が行い、失敗時には現在のthemeを維持する。
use crate::{
    manifest::{Error, Slot},
    raster::{self, Bundle, CompiledTheme, RasterBudget},
};
use eidolon_model::pixels::PixelBudget;
use std::{
    collections::BTreeMap,
    sync::{Arc, Condvar, Mutex},
    thread::{self, JoinHandle},
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Generation(u64);
impl Generation {
    pub fn get(self) -> u64 {
        self.0
    }
}
struct Request {
    generation: Generation,
    source: Source,
    slots: BTreeMap<Slot, [u32; 2]>,
}
enum Source {
    Bundle(Bundle),
    Package(Arc<[u8]>),
    File { path: std::path::PathBuf, uid: u32 },
}
impl Source {
    fn load(self) -> Result<(Bundle, Vec<u8>), Error> {
        match self {
            Self::Bundle(bundle) => {
                let bytes = crate::package::encode(&bundle);
                Ok((bundle, bytes))
            }
            Self::Package(bytes) => Ok((crate::package::decode(&bytes)?, bytes.to_vec())),
            Self::File { path, uid } => {
                let bytes = crate::package::read_file(&path, uid)?;
                let bundle = crate::package::decode(&bytes)?;
                Ok((bundle, bytes))
            }
        }
    }
}
pub struct Completion {
    pub generation: Generation,
    pub result: Result<Arc<CompiledTheme>, Error>,
    /// compileしたものと同一の入力。GPU適用成功後に永続化するためのboundedな所有copy。
    pub source: Option<Arc<[u8]>>,
}
#[derive(Default)]
struct State {
    generation: u64,
    pending: Option<Request>,
    ready: Option<Completion>,
    compiling: Option<Generation>,
    stop: bool,
}
type Shared = Arc<(Mutex<State>, Condvar)>;
pub struct Loader {
    state: Shared,
    worker: Option<JoinHandle<()>>,
}
impl Loader {
    /// pixel/quotaは旧theme・現在のcompile・次のreloadを通じて共有する。
    pub fn start(pixels: PixelBudget, quota: RasterBudget) -> std::io::Result<Self> {
        Self::start_with(move |_, bundle, slots| raster::compile(bundle, slots, &pixels, &quota))
    }
    fn start_with(
        compile: impl Fn(Generation, &Bundle, &BTreeMap<Slot, [u32; 2]>) -> Result<CompiledTheme, Error>
        + Send
        + 'static,
    ) -> std::io::Result<Self> {
        let state = Arc::new((Mutex::new(State::default()), Condvar::new()));
        let owned = state.clone();
        let worker = thread::Builder::new()
            .name("eidolon-theme".into())
            .spawn(move || {
                loop {
                    let request = {
                        let mut s = owned.0.lock().unwrap_or_else(|e| e.into_inner());
                        while s.pending.is_none() && !s.stop {
                            s = owned.1.wait(s).unwrap_or_else(|e| e.into_inner());
                        }
                        if s.stop {
                            break;
                        }
                        let request = s.pending.take().unwrap();
                        s.compiling = Some(request.generation);
                        request
                    };
                    // compilerは共有lock/GL/SDKを保持せず、panicも次のrequestを妨げない。
                    let Request {
                        generation,
                        source,
                        slots,
                    } = request;
                    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                        let (bundle, bytes) = source.load()?;
                        compile(generation, &bundle, &slots)
                            .map(|theme| (Arc::new(theme), Arc::<[u8]>::from(bytes)))
                    }))
                    .unwrap_or_else(|_| Err(Error("theme compiler panicked".into())));
                    let mut s = owned.0.lock().unwrap_or_else(|e| e.into_inner());
                    s.compiling = None;
                    if !s.stop && s.generation == generation.0 {
                        let (result, source) = match result {
                            Ok((theme, source)) => (Ok(theme), Some(source)),
                            Err(error) => (Err(error), None),
                        };
                        s.ready = Some(Completion {
                            generation,
                            result,
                            source,
                        });
                    }
                }
            })?;
        Ok(Self {
            state,
            worker: Some(worker),
        })
    }
    pub fn request(
        &self,
        bundle: Bundle,
        slots: BTreeMap<Slot, [u32; 2]>,
    ) -> Result<Generation, Error> {
        self.request_source(Source::Bundle(bundle), slots)
    }
    pub fn request_file(
        &self,
        path: std::path::PathBuf,
        uid: u32,
        slots: BTreeMap<Slot, [u32; 2]>,
    ) -> Result<Generation, Error> {
        self.request_source(Source::File { path, uid }, slots)
    }
    pub fn request_package(
        &self,
        bytes: Arc<[u8]>,
        slots: BTreeMap<Slot, [u32; 2]>,
    ) -> Result<Generation, Error> {
        self.request_source(Source::Package(bytes), slots)
    }
    fn request_source(
        &self,
        source: Source,
        slots: BTreeMap<Slot, [u32; 2]>,
    ) -> Result<Generation, Error> {
        let mut s = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        if s.stop {
            return Err(Error("theme loader stopped".into()));
        }
        let next = s
            .generation
            .checked_add(1)
            .ok_or_else(|| Error("theme generation exhausted".into()))?;
        s.generation = next;
        s.ready = None;
        let generation = Generation(next);
        s.pending = Some(Request {
            generation,
            source,
            slots,
        });
        self.state.1.notify_one();
        Ok(generation)
    }
    /// 無効化も世代を進め、無効化前のcompile結果で再有効化されることを防ぐ。
    pub fn cancel(&self) -> Result<Generation, Error> {
        let mut s = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        if s.stop {
            return Err(Error("theme loader stopped".into()));
        }
        s.generation = s
            .generation
            .checked_add(1)
            .ok_or_else(|| Error("theme generation exhausted".into()))?;
        s.pending = None;
        s.ready = None;
        Ok(Generation(s.generation))
    }
    pub fn take_ready(&self) -> Option<Completion> {
        self.state
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .ready
            .take()
    }
    /// GPU準備後/adoption直前に再確認する。古い候補を表示中themeと交換しない。
    pub fn is_current(&self, generation: Generation) -> bool {
        let s = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        !s.stop && s.generation == generation.0
    }
    pub fn request_stop(&self) {
        let mut s = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        s.stop = true;
        s.pending = None;
        s.ready = None;
        self.state.1.notify_one();
    }
    pub fn is_finished(&self) -> bool {
        self.worker.as_ref().is_none_or(|w| w.is_finished())
    }
}
impl Drop for Loader {
    fn drop(&mut self) {
        self.request_stop();
        // 所有者の回収地点でjoinする。UI callbackからLoaderを破棄しない。
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{sync::mpsc, time::Duration};
    #[test]
    fn coalesces_pending_discards_old_completion_and_cancels_inflight() {
        let (entered_tx, entered_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let loader = Loader::start_with(move |generation, _, _| {
            entered_tx.send(generation).unwrap();
            release_rx.recv_timeout(Duration::from_secs(5)).unwrap();
            Err(Error("fixture rejected".into()))
        })
        .unwrap();
        let first = loader
            .request(crate::cyan_orbit(), BTreeMap::new())
            .unwrap();
        assert_eq!(
            entered_rx.recv_timeout(Duration::from_secs(5)).unwrap(),
            first
        );
        let skipped = loader
            .request(crate::cyan_orbit(), BTreeMap::new())
            .unwrap();
        let latest = loader
            .request(crate::cyan_orbit(), BTreeMap::new())
            .unwrap();
        release_tx.send(()).unwrap();
        assert_eq!(
            entered_rx.recv_timeout(Duration::from_secs(5)).unwrap(),
            latest
        );
        assert!(!loader.is_current(first) && !loader.is_current(skipped));
        assert!(loader.take_ready().is_none());
        loader.cancel().unwrap();
        release_tx.send(()).unwrap();
        loader.request_stop();
        assert!(!loader.is_current(latest));
        drop(loader); // join: cancelled resultも確実に回収する。
    }
    #[test]
    fn valid_then_invalid_reload_preserves_caller_owned_theme_and_releases_budgets() {
        let pixels = PixelBudget::default();
        let quota = RasterBudget::default();
        let loader = Loader::start(pixels.clone(), quota.clone()).unwrap();
        let first = loader
            .request(crate::cyan_orbit(), BTreeMap::new())
            .unwrap();
        fn completion(loader: &Loader) -> Completion {
            let deadline = std::time::Instant::now() + Duration::from_secs(5);
            loop {
                if let Some(c) = loader.take_ready() {
                    return c;
                }
                assert!(std::time::Instant::now() < deadline);
                thread::sleep(Duration::from_millis(1));
            }
        }
        let c = completion(&loader);
        assert_eq!(c.generation, first);
        let current = c.result.unwrap();
        let bytes = pixels.used_bytes();
        loader
            .request(Bundle::new([]).unwrap(), BTreeMap::new())
            .unwrap();
        assert!(completion(&loader).result.is_err());
        assert_eq!(current.manifest.id, "cyan-orbit");
        assert_eq!(pixels.used_bytes(), bytes);
        drop(loader);
        drop(current);
        assert_eq!(pixels.used_bytes(), 0);
        assert_eq!(quota.used_bytes(), 0);
    }
}
