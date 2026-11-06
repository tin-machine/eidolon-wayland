//! Android processごとに一つの実行threadを保持する。runtimeのGL所有物はjob終了時に全回収する。
//! SDK 0.15.0のTID別GPU計測cacheを無制限に増やさない。停止・故障時の代替threadは作らない。
use super::State;
use serde::Serialize;
use std::{
    io,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc::{SyncSender, sync_channel},
    },
    thread,
};

#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub threads: usize,
    pub active_jobs: usize,
    pub completed_jobs: u64,
    pub tid: i32,
    pub failed: bool,
}

struct Job {
    state: State,
    finished: Arc<AtomicBool>,
}

pub(crate) struct Executor {
    sender: SyncSender<Job>,
    report: Arc<Mutex<Report>>,
}
impl Executor {
    // 非Androidの製品経路は専用thread。host testでは同じExecutorを明示して検査する。
    #[cfg_attr(not(any(target_os = "android", test)), allow(dead_code))]
    pub(crate) fn new() -> io::Result<Self> {
        let (sender, receiver) = sync_channel::<Job>(1);
        let report = Arc::new(Mutex::new(Report {
            threads: 1,
            ..Default::default()
        }));
        let owned = report.clone();
        thread::Builder::new()
            .name("eidolon-gl".into())
            .spawn(move || {
                owned.lock().unwrap_or_else(|e| e.into_inner()).tid = super::thread_id();
                // recv待機中はGL/runtime参照を持たず、timerによるpollもしない。
                while let Ok(job) = receiver.recv() {
                    let healthy = super::run_owner(&job.state);
                    drop(job.state);
                    {
                        let mut report = owned.lock().unwrap_or_else(|e| e.into_inner());
                        report.active_jobs = 0;
                        report.completed_jobs += 1;
                        report.failed |= !healthy;
                    }
                    // GLのdropとjobの全参照解放、lease返却後にだけ完了を通知する。
                    job.finished.store(true, Ordering::Release);
                    if !healthy {
                        break;
                    }
                }
                owned.lock().unwrap_or_else(|e| e.into_inner()).threads = 0;
            })?;
        Ok(Self { sender, report })
    }

    #[cfg(target_os = "android")]
    pub(crate) fn global() -> io::Result<Arc<Self>> {
        static GLOBAL: std::sync::OnceLock<Mutex<Option<Arc<Executor>>>> =
            std::sync::OnceLock::new();
        let mut global = GLOBAL
            .get_or_init(|| Mutex::new(None))
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if global.is_none() {
            *global = Some(Arc::new(Self::new()?));
        }
        Ok(global.as_ref().unwrap().clone())
    }

    pub(crate) fn submit(&self, state: State) -> io::Result<Arc<AtomicBool>> {
        let mut report = self.report.lock().unwrap_or_else(|e| e.into_inner());
        if report.failed || report.threads == 0 {
            return Err(io::Error::other("RENDER_EXECUTOR_FAILED"));
        }
        if report.active_jobs != 0 {
            return Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "RENDER_EXECUTOR_BUSY",
            ));
        }
        let finished = Arc::new(AtomicBool::new(false));
        report.active_jobs = 1;
        if self
            .sender
            .try_send(Job {
                state,
                finished: finished.clone(),
            })
            .is_err()
        {
            report.active_jobs = 0;
            report.failed = true;
            return Err(io::Error::other("RENDER_EXECUTOR_FAILED"));
        }
        Ok(finished)
    }

    pub(crate) fn snapshot(&self) -> Report {
        self.report
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }
}
