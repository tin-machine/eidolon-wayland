//! Android targetはSurfaceだけを持ち、context/textureはGL ownerのDevice一つで共有する。
use super::{GpuInfo, compositor::Probe};
pub use crate::egl::Device;
use eidolon_model::window::WindowSnapshot;
pub use ndk::native_window::NativeWindow as PlatformWindow;
use smithay::backend::egl::EGLSurface;

pub struct Target {
    // Surfaceを先にdropしてからANativeWindow追加参照を返す。
    surface: Option<EGLSurface>,
    window: PlatformWindow,
    info: GpuInfo,
    rebuilds: u32,
}
impl Target {
    pub fn new(
        device: &mut Device,
        window: PlatformWindow,
        width: i32,
        height: i32,
    ) -> Result<Self, &'static str> {
        let surface = device.surface(window.clone(), width, height)?;
        let info = device.info(&surface)?;
        Ok(Self {
            surface: Some(surface),
            window,
            info,
            rebuilds: 0,
        })
    }
    pub fn info(&self) -> GpuInfo {
        self.info.clone()
    }
    pub fn invalidate(&mut self) {
        self.surface = None;
    }
    pub fn rebuild(&mut self, device: &mut Device) -> Result<(), &'static str> {
        self.rebuilds += 1;
        if self.rebuilds > 3 {
            return Err("SURFACE_LOST");
        }
        let surface = device.surface(self.window.clone(), self.info.width, self.info.height)?;
        self.info = device.info(&surface)?;
        self.surface = Some(surface);
        Ok(())
    }
    pub fn frame(
        &mut self,
        device: &mut Device,
        frame: u64,
        snapshot: Option<&WindowSnapshot>,
        probes: &[Probe],
    ) -> Result<(), &'static str> {
        let surface = self.surface.as_mut().ok_or("SURFACE_LOST")?;
        let elapsed = if let Some(snapshot) = snapshot {
            device.paint(surface, snapshot, probes)?
        } else {
            device.pattern(surface, frame, !self.info.readback)?
        };
        self.info.readback |= snapshot.is_none() || !probes.is_empty();
        self.info.max_swap_us = self.info.max_swap_us.max(elapsed);
        Ok(())
    }
}
