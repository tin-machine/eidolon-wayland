//! Android APIに依存しないowner/世代/停止test用のbackend。
use super::GpuInfo;
#[derive(Default)]
pub struct PlatformWindow {
    #[cfg(test)]
    pub control: Option<std::sync::Arc<TestControl>>,
}
#[cfg(test)]
#[derive(Default)]
pub struct TestControl {
    pub blocked: std::sync::atomic::AtomicBool,
    pub entered: std::sync::atomic::AtomicBool,
    pub fault: std::sync::atomic::AtomicU8,
}
pub struct Target {
    info: GpuInfo,
    #[cfg(test)]
    control: Option<std::sync::Arc<TestControl>>,
}
impl Target {
    pub fn new(_window: PlatformWindow, width: i32, height: i32) -> Result<Self, &'static str> {
        Ok(Self {
            #[cfg(test)]
            control: _window.control,
            info: GpuInfo {
                width,
                height,
                readback: true,
                ..Default::default()
            },
        })
    }
    pub fn info(&self) -> GpuInfo {
        self.info.clone()
    }
    pub fn frame(&mut self, _: u64) -> Result<(), &'static str> {
        #[cfg(test)]
        if let Some(control) = &self.control {
            use std::sync::atomic::Ordering;
            control.entered.store(true, Ordering::Release);
            while control.blocked.load(Ordering::Acquire) {
                std::thread::sleep(std::time::Duration::from_millis(1));
            }
            match control.fault.swap(0, Ordering::AcqRel) {
                1 => return Err("SURFACE_LOST"),
                2 => return Err("EGL_CONTEXT_LOST"),
                _ => (),
            }
        }
        Ok(())
    }
    pub fn rebuild(&mut self) -> Result<(), &'static str> {
        Ok(())
    }
}
