//! 同じWayland clientの余剰toplevelだけがcloseされ、既存窓は継続する固定検査。
use super::clipboard::ClientFixture;
use super::*;
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender};
#[derive(Clone, Copy, Debug)]
pub enum Stage {
    Full,
    RejectedCommit,
    RejectedDestroyed,
    SurvivorUpdated,
    Vacancy,
    Replacement,
}
pub struct Control {
    pub steps: SyncSender<Stage>,
    pub replies: Receiver<()>,
}
/// Quest footで観測した一窓あたり10surfaceとglobal1個を実wireで再現する。
pub const EXTRA_SURFACES_PER_WINDOW: usize = 9;
pub fn expected_surfaces(capacity: usize) -> usize {
    capacity * (EXTRA_SURFACES_PER_WINDOW + 1) + 1
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub accepted: usize,
    pub closed: u32,
    pub rejected_released: bool,
    pub survivor_updated: bool,
    pub replacement_mapped: bool,
}
struct Window {
    surface: wl_surface::WlSurface,
    xdg: xdg_surface::XdgSurface,
    top: xdg_toplevel::XdgToplevel,
}
impl Window {
    fn new(c: &mut ClientFixture, label: &str) -> Self {
        let qh = c.queue.handle();
        let surface = c.state.compositor.as_ref().unwrap().create_surface(&qh, ());
        let xdg = c
            .state
            .shell
            .as_ref()
            .unwrap()
            .get_xdg_surface(&surface, &qh, ());
        let top = xdg.get_toplevel(&qh, ());
        top.set_app_id(label.to_owned());
        Self { surface, xdg, top }
    }
    fn map(
        &self,
        c: &mut ClientFixture,
        stop: &AtomicBool,
        deadline: Instant,
    ) -> Result<(), &'static str> {
        c.state.configured = false;
        self.surface.commit();
        pump(
            &c.connection,
            &mut c.queue,
            &mut c.state,
            stop,
            deadline,
            |s| s.configured,
        )?;
        self.surface.attach(Some(&c.buffer), 0, 0);
        self.surface.damage_buffer(0, 0, WIDTH, HEIGHT);
        self.surface.commit();
        c.sync(stop, deadline)
    }
    fn destroy(self) {
        self.top.destroy();
        self.xdg.destroy();
        self.surface.destroy();
    }
}
fn step(
    c: &mut ClientFixture,
    control: &Control,
    stage: Stage,
    stop: &AtomicBool,
    deadline: Instant,
) -> Result<(), &'static str> {
    control.steps.try_send(stage).map_err(|_| "STEP_SEND")?;
    loop {
        if stop.load(Ordering::Acquire) || Instant::now() >= deadline {
            return Err("STEP_TIMEOUT");
        }
        match control.replies.recv_timeout(Duration::from_millis(20)) {
            Ok(()) => break,
            Err(RecvTimeoutError::Timeout) => (),
            Err(_) => return Err("STEP_CLOSED"),
        }
    }
    c.sync(stop, deadline)
}
pub fn run(
    stream: UnixStream,
    capacity: usize,
    stop: Arc<AtomicBool>,
    control: Control,
) -> Result<Report, &'static str> {
    run_inner(stream, capacity, stop, control, false)
}

/// Real Wayland v3 variant: one verified Mousepad root with child xdg_toplevels.
/// All wire stages remain identical to the legacy capacity test.
pub fn run_v3(
    stream: UnixStream,
    capacity: usize,
    stop: Arc<AtomicBool>,
    control: Control,
) -> Result<Report, &'static str> {
    run_inner(stream, capacity, stop, control, true)
}

fn run_inner(
    stream: UnixStream,
    capacity: usize,
    stop: Arc<AtomicBool>,
    control: Control,
    v3: bool,
) -> Result<Report, &'static str> {
    if !(2..=16).contains(&capacity) {
        return Err("CAPACITY_INVALID");
    }
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut c = ClientFixture::new(stream, &stop, deadline)?;
    c.top.set_app_id(
        if v3 {
            "mousepad"
        } else {
            "fixture.capacity.root"
        }
        .into(),
    );
    c.sync(&stop, deadline)?;
    let mut windows = Vec::new();
    for n in 1..capacity {
        let window = Window::new(&mut c, &format!("fixture.capacity.{n}"));
        if v3 {
            window.top.set_parent(Some(&c.top));
        }
        window.map(&mut c, &stop, deadline)?;
        windows.push(window);
    }
    let qh = c.queue.handle();
    let retained_surfaces = (0..(capacity * EXTRA_SURFACES_PER_WINDOW + 1))
        .map(|_| c.state.compositor.as_ref().unwrap().create_surface(&qh, ()))
        .collect::<Vec<_>>();
    c.sync(&stop, deadline)?;
    step(&mut c, &control, Stage::Full, &stop, deadline)?;
    let rejected = Window::new(&mut c, "fixture.capacity.rejected");
    // closeと交差したattachはconfigure ACK前でも破棄され、共有clientをprotocol errorにしない。
    let released = c.state.report.releases;
    let configured = c.state.report.configures;
    let buffer = c.pool.create_buffer(
        0,
        WIDTH,
        HEIGHT,
        WIDTH * 4,
        wl_shm::Format::Argb8888,
        &c.queue.handle(),
        (),
    );
    rejected.surface.attach(Some(&buffer), 0, 0);
    rejected.surface.commit();
    c.sync(&stop, deadline)?;
    if c.state.toplevel_closes != 1
        || c.state.report.configures != configured
        || c.state.report.releases <= released
    {
        return Err("REJECTED_WIRE");
    }
    step(&mut c, &control, Stage::RejectedCommit, &stop, deadline)?;
    rejected.destroy();
    buffer.destroy();
    c.sync(&stop, deadline)?;
    step(&mut c, &control, Stage::RejectedDestroyed, &stop, deadline)?;
    c.surface.attach(Some(&c.buffer), 0, 0);
    c.surface.damage_buffer(0, 0, WIDTH, HEIGHT);
    c.surface.commit();
    c.sync(&stop, deadline)?;
    step(&mut c, &control, Stage::SurvivorUpdated, &stop, deadline)?;
    windows.pop().unwrap().destroy();
    c.sync(&stop, deadline)?;
    step(&mut c, &control, Stage::Vacancy, &stop, deadline)?;
    let replacement = Window::new(&mut c, "fixture.capacity.replacement");
    if v3 {
        replacement.top.set_parent(Some(&c.top));
    }
    replacement.map(&mut c, &stop, deadline)?;
    step(&mut c, &control, Stage::Replacement, &stop, deadline)?;
    replacement.destroy();
    for window in windows {
        window.destroy();
    }
    for surface in retained_surfaces {
        surface.destroy();
    }
    let report = Report {
        accepted: capacity,
        closed: c.state.toplevel_closes,
        rejected_released: true,
        survivor_updated: true,
        replacement_mapped: true,
    };
    c.finish(&stop, deadline)?;
    Ok(report)
}
