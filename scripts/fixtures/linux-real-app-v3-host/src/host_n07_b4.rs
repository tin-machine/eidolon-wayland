//! Two real N07 sessions on one Registry and one unchanged Runtime config.
//! The supervisor owns filesystem observations; this driver can only pause
//! before Launch / Close / the next Runtime and use ordinary product commands.
use super::{
    Descriptor, Driver, Result, digest, host_n04::clock_ns, host_n07::popup_grab_matches_click,
    host_n07_b2::popup, host_n07_review::review_rois, write_receipt, write_receipt_bytes,
};
use eidolon_model::linux_desktop_v3 as b1;
use eidolon_native::registry::Registry;
use eidolon_render::HostReadbackRoi;
use eidolon_transport::credentials::Secret;
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    fs,
    io::{ErrorKind, Read, Write},
    os::{
        fd::FromRawFd,
        unix::{fs::MetadataExt, net::UnixStream},
    },
    path::Path,
    thread,
    time::{Duration, Instant},
};

const BARRIER_LIMIT: usize = 16 * 1024;
const BARRIER_BUDGET: Duration = Duration::from_secs(30);
const SECOND_CONTEXT_PHASES: [&str; 2] = ["second_on_context", "second_on_menu_context"];

pub(super) struct BarrierChannel {
    stream: UnixStream,
    received: Option<(Value, u64)>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Response {
    schema: u32,
    phase: String,
    request_sha256: String,
    allow: bool,
    observation_sha256: String,
}

fn hash_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn parse_bound_response(raw: &[u8], request: &Value, expected: &str) -> Result<(Value, bool)> {
    let value = b1::parse_json(raw, BARRIER_LIMIT).map_err(|_| "I6_N07_B4_BARRIER_INVALID")?;
    let reply: Response =
        serde_json::from_value(value.clone()).map_err(|_| "I6_N07_B4_BARRIER_INVALID")?;
    if reply.schema != 1
        || reply.phase != request["phase"]
        || reply.request_sha256 != expected
        || !hash_valid(&reply.observation_sha256)
    {
        return Err("I6_N07_B4_BARRIER_INVALID");
    }
    Ok((value, reply.allow))
}

impl BarrierChannel {
    fn from_fd(fd: i32) -> Result<Self> {
        if fd < 3 {
            return Err("I6_N07_B4_BARRIER_INVALID");
        }
        let mut metadata: libc::stat = unsafe { std::mem::zeroed() };
        if unsafe { libc::fstat(fd, &mut metadata) } != 0
            || metadata.st_mode & libc::S_IFMT != libc::S_IFSOCK
        {
            return Err("I6_N07_B4_BARRIER_INVALID");
        }
        // The root supervisor creates the pair before dropping the driver
        // uid. A GUI app/driver-created socket cannot become this authority.
        let mut peer: libc::ucred = unsafe { std::mem::zeroed() };
        let mut length = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
        if unsafe {
            libc::getsockopt(
                fd,
                libc::SOL_SOCKET,
                libc::SO_PEERCRED,
                (&mut peer as *mut libc::ucred).cast(),
                &mut length,
            )
        } != 0
            || length as usize != std::mem::size_of::<libc::ucred>()
            || peer.uid != 0
        {
            return Err("I6_N07_B4_BARRIER_INVALID");
        }
        // pass_fds makes this inherited endpoint executable across the one
        // supervisor -> Host transition. Seal it again before any ordinary
        // product child exists; no local Waypipe/SSH child owns this channel.
        let flags = unsafe { libc::fcntl(fd, libc::F_GETFD) };
        if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFD, flags | libc::FD_CLOEXEC) } != 0 {
            return Err("I6_N07_B4_BARRIER_INVALID");
        }
        let stream = unsafe { UnixStream::from_raw_fd(fd) };
        stream
            .set_nonblocking(true)
            .map_err(|_| "I6_N07_B4_BARRIER_INVALID")?;
        Ok(Self {
            stream,
            received: None,
        })
    }

    fn exchange(&mut self, request: &Value, mut pump: impl FnMut() -> Result<()>) -> Result<Value> {
        self.received = None;
        let raw = serde_json::to_vec(request).map_err(|_| "I6_N07_B4_BARRIER_INVALID")?;
        if raw.len() > BARRIER_LIMIT {
            return Err("I6_N07_B4_BARRIER_INVALID");
        }
        let expected = digest(&raw);
        let mut wire = raw;
        wire.push(b'\n');
        let deadline = Instant::now() + BARRIER_BUDGET;
        let mut written = 0;
        let mut response = Vec::new();
        loop {
            if Instant::now() >= deadline {
                return Err("I6_N07_B4_BARRIER_TIMEOUT");
            }
            if written < wire.len() {
                match self.stream.write(&wire[written..]) {
                    Ok(0) => return Err("I6_N07_B4_BARRIER_EOF"),
                    Ok(count) => written += count,
                    Err(error) if error.kind() == ErrorKind::WouldBlock => (),
                    Err(_) => return Err("I6_N07_B4_BARRIER_IO"),
                }
            } else {
                let mut bytes = [0u8; 1024];
                match self.stream.read(&mut bytes) {
                    Ok(0) => return Err("I6_N07_B4_BARRIER_EOF"),
                    Ok(count) => {
                        response.extend_from_slice(&bytes[..count]);
                        if response.len() > BARRIER_LIMIT {
                            return Err("I6_N07_B4_BARRIER_INVALID");
                        }
                        if let Some(newline) = response.iter().position(|b| *b == b'\n') {
                            if newline + 1 != response.len() {
                                return Err("I6_N07_B4_BARRIER_INVALID");
                            }
                            let (value, allow) =
                                parse_bound_response(&response[..newline], request, &expected)?;
                            // Retain an authentic refusal as observed evidence,
                            // while keeping the same fail-closed launch gate.
                            self.received = Some((value.clone(), clock_ns()?));
                            return if allow {
                                Ok(value)
                            } else {
                                Err("I6_N07_B4_SUPERVISOR_REFUSED")
                            };
                        }
                    }
                    Err(error) if error.kind() == ErrorKind::WouldBlock => (),
                    Err(_) => return Err("I6_N07_B4_BARRIER_IO"),
                }
            }
            pump()?;
            thread::sleep(Duration::from_millis(20));
        }
    }
}

fn numbers_roi() -> HostReadbackRoi {
    HostReadbackRoi {
        id: 3,
        x: 0,
        y: 64,
        width: 64,
        height: 192,
    }
}
fn checkmark_roi() -> HostReadbackRoi {
    HostReadbackRoi {
        id: 2,
        x: 112,
        y: 96,
        width: 112,
        height: 128,
    }
}

impl Driver {
    pub(super) fn n07_b4_before_launch(&mut self) -> Result<()> {
        let Some((_, session)) = self.n07_b4_session.as_ref() else {
            return Ok(());
        };
        let phase = format!("{session}_before_launch");
        self.n07_b4_barrier(&phase)
    }

    fn n07_b4_barrier(&mut self, phase: &str) -> Result<()> {
        if self.destroy_status.is_none() {
            self.refresh_trace()?;
        }
        let (run_id, session) = self
            .n07_b4_session
            .as_ref()
            .ok_or("I6_N07_B4_BARRIER_INVALID")?;
        let captured = self
            .trace
            .events
            .iter()
            .find(|event| event.kind == "capture_created")
            .ok_or("I6_N07_B4_CAPTURE_MISSING")?;
        let capture = captured
            .capture
            .as_ref()
            .ok_or("I6_N07_B4_CAPTURE_MISSING")?;
        let watermark: u64 = self
            .trace
            .events
            .last()
            .ok_or("I6_N07_B4_CAPTURE_MISSING")?
            .sequence
            .parse()
            .map_err(|_| "I6_N07_B4_CAPTURE_MISSING")?;
        let request = json!({
            "schema":1,"run_id":run_id,"session":session,"phase":phase,
            "runtime":self.handle.to_string(),"capture_id":capture.capture_id,
            "capture":capture,"context":captured.data["context"],
            "transport_watermark":watermark,"monotonic_ns":clock_ns()?,
        });
        let request_sha = digest(serde_json::to_vec(&request).unwrap());
        let index = self.n07_b4_barriers.len();
        self.n07_b4_barriers.push(json!({
            "request":request,"request_sha256":request_sha,
            "response":null,"response_monotonic_ns":null,"error":null,
        }));
        let mut channel = self
            .n07_b4_channel
            .take()
            .ok_or("I6_N07_B4_BARRIER_INVALID")?;
        let result = channel.exchange(&request, || {
            if self.destroy_status.is_none() {
                self.pump()?;
            }
            Ok(())
        });
        if let Some((response, received_at)) = channel.received.take() {
            self.n07_b4_barriers[index]["response"] = response;
            self.n07_b4_barriers[index]["response_monotonic_ns"] = json!(received_at);
        }
        self.n07_b4_channel = Some(channel);
        if let Err(error) = result {
            self.n07_b4_barriers[index]["error"] = json!(error);
            return Err(error);
        }
        if self.destroy_status.is_none() {
            self.observe("n07_b4_barrier", self.n07_b4_barriers[index].clone())?;
        }
        Ok(())
    }

    fn n07_b4_close(&mut self) -> Result<()> {
        let main = self.n02_readback.as_ref().ok_or("I6_N07_B4_MAIN_MISSING")?["target"].clone();
        self.pump()?;
        // The persisted census intentionally omits presentation metadata.
        // Read the live typed Renderer snapshot through the same Registry
        // guard used by CloseWindowTarget; never infer a revision from it.
        let revision = self
            .registry
            .guarded(self.handle, |runtime| {
                runtime.snapshot().render.theme_revision
            })
            .map_err(|_| "I6_N07_B4_CLOSE_INVALID")?;
        self.observe(
            "n07_b4_close_intent",
            json!({
                "main_target":main,"action":"close_window_target","request_id":"7400",
                "controls_revision":revision,
            }),
        )?;
        self.n02_command(
            json!({"schema":1,"type":"close_window_target",
            "request_id":"7400","target":main,"controls_revision":revision}),
            "close_window_target",
        )?;
        self.n02_wait_command("7400", 10)?;
        let gone = self.wait(35, |s| {
            let app = &s["runtime"]["app"];
            app["windows"].as_array().is_some_and(|windows| {
                !windows
                    .iter()
                    .any(|w| w["window"] == main["window"] && w["life"] == "Mapped")
            }) && app["gui_windows"]
                .as_array()
                .is_some_and(|windows| !windows.contains(&main["window"]))
        })?;
        self.observe(
            "n07_b4_main_destroyed",
            json!({
                "main_target":main,"main_mapped":false,
                "app_state":gone["runtime"]["app"]["state"],
                "local_workers":gone["runtime"]["app"]["local"]["workers"],
            }),
        )?;
        let exited = self.wait(45, |s| {
            s["runtime"]["app"]["local"]["waited"] == true
                && s["runtime"]["app"]["local"]["workers"] == 0
        })?;
        self.observe(
            "n07_b4_app_exit",
            json!({
                "main_target":main,"local_waited":exited["runtime"]["app"]["local"]["waited"],
                "local_workers":exited["runtime"]["app"]["local"]["workers"],
                "app_state":exited["runtime"]["app"]["state"],
                "app_error":exited["runtime"]["app"]["error"],
                "transport_state":exited["runtime"]["transport"]["state"],
                "transport_reason":exited["runtime"]["transport"]["desktop_v3_transport"]["error"],
            }),
        )?;
        Ok(())
    }

    fn n07_b4_context(&mut self, main: &Value, phase: &str) -> Result<()> {
        if !SECOND_CONTEXT_PHASES.contains(&phase)
            || self
                .n07_review_context
                .as_ref()
                .is_none_or(|states| states.get(phase).is_some())
        {
            return Err("I6_N07_REVIEW_CONTEXT_INVALID");
        }
        let stage = self.n07_capture_with_rois(main, phase, Some(review_rois()))?;
        self.n07_review_context.as_mut().unwrap()[phase] = stage;
        Ok(())
    }

    fn n07_b4_second(&mut self, run_id: &str, document: &str) -> Result<()> {
        self.exercise_n02("mousepad_editor")?;
        self.n05_active = true;
        let main = self.n02_readback.as_ref().ok_or("I6_N07_B4_MAIN_MISSING")?["target"].clone();
        self.refresh_trace()?;
        let capture = self
            .trace
            .events
            .iter()
            .find_map(|event| event.capture.as_ref())
            .ok_or("I6_N07_B4_CAPTURE_MISSING")?
            .capture_id
            .clone();
        self.n07_second = Some(json!({
            "schema":1,"run_id":run_id,"capture_id":capture,"main_target":main,
            "menu_coordinate_reviewed":false,"menu_identity_verified":false,
            "document":document,"input":document,"before_open":null,"before_open_confirm":null,
            "steps":[],"dialogs":[],
            "second_on_numbers_1_8":null,"pointer":null,"popup":null,
            "second_on_checkmark":null,
        }));
        let before = self.n07_capture_with_rois(&main, "before_open", Some(vec![numbers_roi()]))?;
        let layers = before["readback"]["receipt"]["layers"]
            .as_array()
            .ok_or("I6_N07_B4_MAIN_MISSING")?;
        if layers.len() != 1 {
            return Err("I6_N07_B4_MAIN_MISSING");
        }
        let parent = layers[0]["surface"].clone();
        self.n07_second.as_mut().unwrap()["before_open"] = before.clone();
        let mut req = 8000u32;
        let baseline = self.n05_dialog_baseline(&main, "open")?;
        let open_step = self.n05_step("open_shortcut", &main, &mut req, |s, t, e, k, r| {
            s.n05_shortcut(t, e, k, r, 24, 43, false)
        })?;
        self.n07_second.as_mut().unwrap()["steps"]
            .as_array_mut()
            .unwrap()
            .push(open_step);
        let open = self.n05_dialog(&main, "open", &baseline)?;
        self.n07_second.as_mut().unwrap()["dialogs"]
            .as_array_mut()
            .unwrap()
            .push(open.clone());
        let child = &open["target"];
        let location = self.n05_step("open_location", child, &mut req, |s, t, e, k, r| {
            s.n05_shortcut(t, e, k, r, 38, 40, false)
        })?;
        self.n07_second.as_mut().unwrap()["steps"]
            .as_array_mut()
            .unwrap()
            .push(location);
        thread::sleep(Duration::from_millis(500));
        let path = self.n05_step("open_path", child, &mut req, |s, t, e, k, r| {
            s.n05_ascii(t, e, k, r, document.as_bytes())
        })?;
        self.n07_second.as_mut().unwrap()["steps"]
            .as_array_mut()
            .unwrap()
            .push(path);
        thread::sleep(Duration::from_millis(900));
        // Dialog creation/focus can redraw the parent before any file is
        // opened. Bind post-Open progress to the actual image immediately
        // before Confirm, not only to the older startup buffer.
        let before_confirm =
            self.n07_capture_with_rois(&main, "before_open_confirm", Some(vec![numbers_roi()]))?;
        self.n07_second.as_mut().unwrap()["before_open_confirm"] = before_confirm.clone();
        self.n05_confirm(&open, &main, &mut req)?;
        self.observe(
            "n07_b4_open_confirmed",
            json!({"target":child,"document":document}),
        )?;
        let (epoch, _) = self.n05_focus(&main, &mut req)?;
        let numbers = self.n07_capture_settled_post_open(
            &main,
            epoch,
            &before_confirm,
            &parent,
            vec![numbers_roi()],
        )?;
        self.n07_second.as_mut().unwrap()["second_on_numbers_1_8"] = numbers;
        self.n07_b4_context(&main, "second_on_context")?;
        // Opening View observes the persisted state; no Line Numbers item
        // click exists anywhere in this second-session driver.
        let pointer = self.n07_click_at(&main, epoch, &mut req, [0.16, 0.063], [160, 63], true)?;
        self.n07_second.as_mut().unwrap()["pointer"] = pointer.clone();
        let s = self.wait(12, |s| popup(s, &main).is_some())?;
        let menu = popup(&s, &main).ok_or("I6_N07_B4_POPUP_MISSING")?;
        if !popup_grab_matches_click(&menu, &pointer) {
            return Err("I6_N07_B4_POPUP_SERIAL");
        }
        self.n07_second.as_mut().unwrap()["popup"] = menu;
        let checked =
            self.n07_capture_with_rois(&main, "second_on_checkmark", Some(vec![checkmark_roi()]))?;
        self.n07_second.as_mut().unwrap()["second_on_checkmark"] = checked;
        self.n07_b4_context(&main, "second_on_menu_context")?;
        self.n07_second.as_mut().unwrap()["final_key_request"] = json!(req.to_string());
        self.n07_b4_barrier("second_after_observation")
    }
}

fn second_receipt(
    driver: &Driver,
    run_id: &str,
    error: Option<&str>,
    cleanup: Option<&str>,
) -> Value {
    let mut native = driver.receipt_n02(run_id, "mousepad_editor", error, cleanup);
    native["schema"] = json!("eidolon-i6-n07-b4-second-native-v1");
    native["variant"] = json!("same_storage_restart_collection");
    native["n07_second"] = driver.n07_second.clone().unwrap_or(Value::Null);
    native["n07_assertions_verified"] = json!([]);
    native["host_v3_acceptance_passed"] = json!(false);
    native
}

fn write_second_context(driver: &Driver, path: &Path, native: &Value) -> Result<Value> {
    let states = driver
        .n07_review_context
        .as_ref()
        .ok_or("I6_N07_REVIEW_CONTEXT_INVALID")?;
    let acquired: Vec<_> = SECOND_CONTEXT_PHASES
        .iter()
        .filter(|phase| states.get(**phase).is_some())
        .collect();
    let complete = native["error"].is_null()
        && native["cleanup_error"].is_null()
        && acquired.len() == SECOND_CONTEXT_PHASES.len();
    let value = json!({"schema":1,"revision":"eidolon-i6-n07-b4-second-review-context-v1",
        "source":"host_renderer_review_context","run_id":native["run_id"],
        "runtime":native["runtime"],"owner_id":native["owner_id"],
        "source_sha256":native["source_sha256"],
        "capture_id":native["n07_second"]["capture_id"],"target":native["n07_second"]["main_target"],
        "complete":complete,"states":states,"error":native["error"],"cleanup_error":native["cleanup_error"]});
    let bytes = serde_json::to_vec(&value).map_err(|_| "I6_N07_REVIEW_CONTEXT_INVALID")?;
    if bytes.len() > 4 * 1024 * 1024 {
        return Err("I6_N07_REVIEW_CONTEXT_OVERFLOW");
    }
    write_receipt_bytes(path, &bytes)?;
    Ok(
        json!({"schema":1,"sha256":digest(&bytes),"bytes":bytes.len(),
        "states":acquired,"complete":complete}),
    )
}

fn finish(
    driver: &mut Driver,
    run_id: &str,
    label: &str,
    output: &Path,
    error: Option<&str>,
) -> Result<(Value, bool)> {
    let retirement = if driver.n02_sdk.as_ref().is_some_and(|sdk| sdk.owns_lease()) {
        driver.retire_n02().err()
    } else {
        driver.retire_after_failure().err()
    };
    let cleanup = retirement.or_else(|| driver.shutdown().err());
    let mut native = if label == "first" {
        driver.receipt_n07_b2_probe(run_id, error, cleanup)
    } else {
        second_receipt(driver, run_id, error, cleanup)
    };
    let context_path = output.join(format!("{label}-context.json"));
    let context = if label == "first" {
        driver.write_n07_review_context(&context_path, &native)
    } else {
        write_second_context(driver, &context_path, &native)
    };
    let context_error = match context {
        Ok(binding) => {
            native["review_context"] = binding;
            None
        }
        Err(reason) => {
            native["review_context"] = json!({"schema":1,"complete":false,"error":reason});
            if native["error"].is_null() {
                native["error"] = json!(reason);
            }
            Some(reason)
        }
    };
    let name = format!("{label}-native.json");
    write_receipt(&output.join(&name), &native)?;
    let bytes = serde_json::to_vec(&native).map_err(|_| "I6_RECEIPT_INVALID")?;
    let session = json!({"session":label,"runtime":native["runtime"],"owner_id":native["owner_id"],
        "native_file":name,"native_sha256":digest(&bytes),"native_bytes":bytes.len(),
        "context_file":format!("{label}-context.json"),"review_context":native["review_context"],
        "error":native["error"],"cleanup_error":native["cleanup_error"],
        "product_cleanup_complete":cleanup.is_none() && driver.destroy_status == Some(0)});
    Ok((
        session,
        error.is_none() && cleanup.is_none() && context_error.is_none(),
    ))
}

fn summary(
    descriptor: &Descriptor,
    sessions: Vec<Value>,
    barriers: Vec<Value>,
    error: Option<&str>,
) -> Value {
    let build: Value = serde_json::from_str(env!("EIDOLON_I6_BUILD_SOURCE")).unwrap();
    json!({"schema":1,"revision":"eidolon-i6-n07-b4-host-v1",
        "run_id":descriptor.run_id,"source_sha256":build["tree_sha256"],
        "build_source":build,"sessions":sessions,"barriers":barriers,"error":error,
        "reviewed_golden":false,"menu_identity_verified":false,
        "verified_n07_assertions":[],"host_v3_acceptance_passed":false})
}

fn document_valid(descriptor: &Descriptor, document: &str) -> bool {
    document
        == format!(
            "/home/eidolon-app/.local/share/eidolon/apps/{}/{}/mousepad-editor-v1/Documents/n07-eight-lines.txt",
            descriptor.installation_id, descriptor.profile_storage_id
        )
}

/// Collection failure must not hide a completed product retirement from the
/// independent supervisor. Observation cannot repair the collection error,
/// and an incomplete cleanup must never be announced as retired.
fn observe_second_retirement(
    collection: Result<()>,
    product_cleanup_complete: bool,
    observe: impl FnOnce() -> Result<()>,
) -> Result<()> {
    let retirement = if product_cleanup_complete {
        observe()
    } else {
        Err("I6_N07_B4_SECOND_INCOMPLETE")
    };
    collection.and(retirement)
}

/// New Runtime creation is below the successful complete first-session
/// receipt and independent supervisor census ACK. No failure can jump it.
pub(super) fn run(
    descriptor: &Descriptor,
    config: &str,
    key: Secret,
    fd: i32,
    document: &str,
    output: &Path,
    summary_path: &Path,
) -> Result<bool> {
    if !document_valid(descriptor, document) {
        return Err("I6_N07_B4_DOCUMENT_INVALID");
    }
    let metadata = fs::symlink_metadata(output).map_err(|_| "I6_INPUT_INVALID")?;
    if !output.is_absolute()
        || !metadata.is_dir()
        || metadata.mode() & 0o077 != 0
        || metadata.uid() != unsafe { libc::geteuid() }
    {
        return Err("I6_INPUT_INVALID");
    }
    let channel = BarrierChannel::from_fd(fd)?;
    let started_at_ns = clock_ns()?;
    let mut first = Driver::new(config, Secret::new(key.to_vec()))?;
    let completed_at_ns = clock_ns()?;
    first.observe(
        "n07_b4_runtime_created",
        json!({
            "session":"first","runtime":first.handle.to_string(),
            "started_at_ns":started_at_ns,"completed_at_ns":completed_at_ns,
        }),
    )?;
    first.n07_review_context = Some(json!({}));
    first.n07_b4_channel = Some(channel);
    first.n07_b4_session = Some((descriptor.run_id.clone(), "first".to_owned()));
    let first_error = (|| {
        first.exercise_n07_b2_probe(&descriptor.run_id)?;
        first.n07_b4_barrier("first_after_toggle")?;
        first.n07_b4_close()
    })()
    .err();
    let (first_session, first_ok) =
        finish(&mut first, &descriptor.run_id, "first", output, first_error)?;
    let mut sessions = vec![first_session];
    if !first_ok {
        write_receipt(
            summary_path,
            &summary(
                descriptor,
                sessions,
                first.n07_b4_barriers.clone(),
                Some(first_error.unwrap_or("I6_N07_B4_FIRST_INCOMPLETE")),
            ),
        )?;
        if first.destroy_status != Some(0) {
            std::process::exit(2);
        }
        return Ok(false);
    }
    if let Err(error) = first.n07_b4_barrier("first_retired") {
        write_receipt(
            summary_path,
            &summary(
                descriptor,
                sessions,
                first.n07_b4_barriers.clone(),
                Some(error),
            ),
        )?;
        return Ok(false);
    }
    // Registry::create owns its next handle. Constructing another Registry
    // would reset it to 1 even inside this process; move the actual owner.
    let registry: Registry = std::mem::take(&mut first.registry);
    let started_at_ns = clock_ns()?;
    let mut second = match Driver::with_registry(config, key, registry) {
        Ok(driver) => driver,
        Err(error) => {
            write_receipt(
                summary_path,
                &summary(
                    descriptor,
                    sessions,
                    first.n07_b4_barriers.clone(),
                    Some(error),
                ),
            )?;
            return Ok(false);
        }
    };
    let completed_at_ns = clock_ns()?;
    second.observe(
        "n07_b4_runtime_created",
        json!({
            "session":"second","runtime":second.handle.to_string(),
            "started_at_ns":started_at_ns,"completed_at_ns":completed_at_ns,
        }),
    )?;
    if second.handle == first.handle {
        return Err("I6_N07_B4_RUNTIME_REUSED");
    }
    second.n07_review_context = Some(json!({}));
    second.n07_b4_channel = first.n07_b4_channel.take();
    second.n07_b4_session = Some((descriptor.run_id.clone(), "second".to_owned()));
    let second_error = second.n07_b4_second(&descriptor.run_id, document).err();
    let (second_session, second_ok) = finish(
        &mut second,
        &descriptor.run_id,
        "second",
        output,
        second_error,
    )?;
    let product_cleanup_complete = second_session["product_cleanup_complete"] == true;
    sessions.push(second_session);
    let collection = if second_ok {
        Ok(())
    } else {
        Err(second_error.unwrap_or("I6_N07_B4_SECOND_INCOMPLETE"))
    };
    let final_error = observe_second_retirement(collection, product_cleanup_complete, || {
        second.n07_b4_barrier("second_retired")
    })
    .err();
    let mut barriers = first.n07_b4_barriers.clone();
    barriers.extend(second.n07_b4_barriers.clone());
    write_receipt(
        summary_path,
        &summary(descriptor, sessions, barriers, final_error),
    )?;
    if second.destroy_status != Some(0) {
        std::process::exit(2);
    }
    Ok(second_ok && final_error.is_none())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> Value {
        json!({"schema":1,"phase":"first_retired"})
    }
    fn reply() -> Value {
        json!({"schema":1,"phase":"first_retired",
        "request_sha256":"a".repeat(64),"allow":true,"observation_sha256":"b".repeat(64)})
    }

    #[test]
    fn barrier_requires_exact_typed_reply_bound_to_current_request() {
        assert!(
            parse_bound_response(
                &serde_json::to_vec(&reply()).unwrap(),
                &request(),
                &"a".repeat(64)
            )
            .is_ok()
        );
        for (field, value) in [
            ("schema", json!(true)),
            ("phase", json!("second_before_launch")),
            ("request_sha256", json!("c".repeat(64))),
            ("allow", json!("false")),
            ("observation_sha256", json!(null)),
            ("unexpected", json!(true)),
        ] {
            let mut changed = reply();
            changed[field] = value;
            assert!(
                parse_bound_response(
                    &serde_json::to_vec(&changed).unwrap(),
                    &request(),
                    &"a".repeat(64)
                )
                .is_err(),
                "{field}"
            );
        }
        assert!(
            parse_bound_response(br#"{"schema":1,"schema":1}"#, &request(), &"a".repeat(64))
                .is_err()
        );
    }

    #[test]
    fn barrier_missing_reply_and_pipelined_ack_are_rejected() {
        let (left, mut right) = UnixStream::pair().unwrap();
        left.set_nonblocking(true).unwrap();
        let handle = thread::spawn(move || {
            let mut bytes = [0u8; 1024];
            let count = right.read(&mut bytes).unwrap();
            assert!(count > 0);
            right.write_all(b"{}\n{}\n").unwrap();
        });
        let mut channel = BarrierChannel {
            stream: left,
            received: None,
        };
        assert_eq!(
            channel.exchange(&request(), || Ok(())),
            Err("I6_N07_B4_BARRIER_INVALID")
        );
        handle.join().unwrap();
        let (left, right) = UnixStream::pair().unwrap();
        left.set_nonblocking(true).unwrap();
        drop(right);
        assert!(
            BarrierChannel {
                stream: left,
                received: None,
            }
            .exchange(&request(), || Ok(()))
            .is_err()
        );
    }

    #[test]
    fn live_barrier_requires_supervisor_ack_for_exact_wire_bytes() {
        let (left, mut right) = UnixStream::pair().unwrap();
        left.set_nonblocking(true).unwrap();
        let handle = thread::spawn(move || {
            let mut bytes = Vec::new();
            loop {
                let mut byte = [0u8; 1];
                right.read_exact(&mut byte).unwrap();
                if byte[0] == b'\n' {
                    break;
                }
                bytes.push(byte[0]);
            }
            let request: Value = serde_json::from_slice(&bytes).unwrap();
            let response = json!({"schema":1,"phase":request["phase"],
                "request_sha256":digest(&bytes),"allow":true,
                "observation_sha256":"b".repeat(64)});
            let mut encoded = serde_json::to_vec(&response).unwrap();
            encoded.push(b'\n');
            right.write_all(&encoded).unwrap();
        });
        let mut pumped = 0;
        let response = BarrierChannel {
            stream: left,
            received: None,
        }
        .exchange(&request(), || {
            pumped += 1;
            Ok(())
        })
        .unwrap();
        assert_eq!(
            response["request_sha256"],
            digest(serde_json::to_vec(&request()).unwrap())
        );
        assert!(pumped > 0);
        handle.join().unwrap();
    }

    #[test]
    fn refused_barrier_retains_real_negative_ack_without_admitting_next_phase() {
        let (left, mut right) = UnixStream::pair().unwrap();
        left.set_nonblocking(true).unwrap();
        let handle = thread::spawn(move || {
            let mut bytes = Vec::new();
            loop {
                let mut byte = [0u8; 1];
                right.read_exact(&mut byte).unwrap();
                if byte[0] == b'\n' {
                    break;
                }
                bytes.push(byte[0]);
            }
            let request: Value = serde_json::from_slice(&bytes).unwrap();
            let response = json!({"schema":1,"phase":request["phase"],
                "request_sha256":digest(&bytes),"allow":false,
                "observation_sha256":"b".repeat(64)});
            let mut encoded = serde_json::to_vec(&response).unwrap();
            encoded.push(b'\n');
            right.write_all(&encoded).unwrap();
            response
        });
        let mut channel = BarrierChannel {
            stream: left,
            received: None,
        };
        let started = clock_ns().unwrap();
        assert_eq!(
            channel.exchange(&request(), || Ok(())),
            Err("I6_N07_B4_SUPERVISOR_REFUSED")
        );
        let actual = handle.join().unwrap();
        let (retained, received_at) = channel.received.as_ref().unwrap();
        assert_eq!(retained, &actual);
        assert!(*received_at >= started && *received_at <= clock_ns().unwrap());
        // A later incomplete exchange must never inherit the earlier reply.
        assert!(channel.exchange(&request(), || Ok(())).is_err());
        assert!(channel.received.is_none());
    }

    #[test]
    fn failed_collection_still_observes_completed_retirement_without_replacing_error() {
        let mut observations = 0;
        let original = "I6_N07_B4_SUPERVISOR_REFUSED";
        let result = observe_second_retirement(Err(original), true, || {
            observations += 1;
            Err("I6_N07_B4_BARRIER_EOF")
        });
        assert_eq!(observations, 1);
        assert_eq!(result, Err(original));
        for collection in [Ok(()), Err(original)] {
            let result = observe_second_retirement(collection, false, || {
                panic!("incomplete product retirement must not send retired IPC")
            });
            assert_eq!(result, collection.and(Err("I6_N07_B4_SECOND_INCOMPLETE")));
        }
        assert_eq!(observe_second_retirement(Ok(()), true, || Ok(())), Ok(()));
        assert_eq!(
            observe_second_retirement(Ok(()), true, || Err("I6_N07_B4_BARRIER_EOF")),
            Err("I6_N07_B4_BARRIER_EOF")
        );
    }

    #[test]
    fn second_numbers_geometry_is_distinct_and_bounded_for_eight_lines() {
        let roi = numbers_roi();
        assert_eq!(
            (roi.id, roi.x, roi.y, roi.width, roi.height),
            (3, 0, 64, 64, 192)
        );
        assert!(roi.width * roi.height <= 32768);
        assert!(roi.y + roi.height <= 256);
        assert_eq!(
            SECOND_CONTEXT_PHASES,
            ["second_on_context", "second_on_menu_context"]
        );
    }
}
