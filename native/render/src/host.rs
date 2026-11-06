//! Android APIに依存しないowner/世代/停止test用のbackend。
use super::GpuInfo;
pub struct Device;
// Androidと同じ明示的なdevice破棄の境界を持つhost adapter。
impl Drop for Device {
    fn drop(&mut self) {}
}
impl Device {
    pub fn new() -> Result<Self, &'static str> {
        Ok(Self)
    }
    pub fn forget(
        &mut self,
        _: eidolon_model::ids::SessionId,
        _: eidolon_model::ids::WindowId,
    ) -> Result<(), &'static str> {
        Ok(())
    }
    pub fn prepare_theme(
        &mut self,
        _: Option<&eidolon_theme::raster::CompiledTheme>,
    ) -> Result<(), &'static str> {
        Ok(())
    }
    pub fn stage_theme(
        &mut self,
        theme: Option<&eidolon_theme::raster::CompiledTheme>,
    ) -> Result<(), &'static str> {
        self.prepare_theme(theme)
    }
    pub fn finish_theme(&mut self, _: bool) -> Result<(), &'static str> {
        Ok(())
    }
    pub fn report(&self) -> super::compositor::Report {
        Default::default()
    }
    pub fn clear(&mut self) -> Result<(), &'static str> {
        Ok(())
    }
}
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
    pub fn new(
        _device: &mut Device,
        _window: PlatformWindow,
        width: i32,
        height: i32,
    ) -> Result<Self, &'static str> {
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
    pub fn frame(
        &mut self,
        _: &mut Device,
        _: u64,
        _: Option<&eidolon_model::window::WindowSnapshot>,
        _: &[super::compositor::Probe],
        _: Option<&crate::decoration::Composition<'_>>,
    ) -> Result<(), &'static str> {
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
                3 => return Err("COMPOSITE_MISMATCH"),
                4 => panic!("injected GPU owner panic"),
                _ => (),
            }
        }
        Ok(())
    }
    pub fn invalidate(&mut self) {}
    pub fn rebuild(&mut self, _: &mut Device) -> Result<(), &'static str> {
        Ok(())
    }
}
