//! window単位の再生状態。呼出側が(SessionId, WindowId)で所有し、Surface世代と分離する。
use crate::{
    manifest::Error,
    raster::CompiledTheme,
    style::{Animation, Clip, Property, Transform, Value},
    timeline::{Easing, Timeline, cycle_fraction},
};
use std::{
    collections::{BTreeMap, BTreeSet},
    time::Duration,
};

#[derive(Clone)]
struct Tween {
    from: Value,
    target: Value,
    start: Duration,
    duration: Duration,
    easing: Easing,
}
impl Tween {
    fn sample(&self, now: Duration) -> Option<Value> {
        let elapsed = now.checked_sub(self.start)?;
        if self.duration.is_zero() || elapsed >= self.duration {
            return Some(self.target.clone());
        }
        self.from.interpolate(
            &self.target,
            self.easing
                .sample(elapsed.as_secs_f64() / self.duration.as_secs_f64())?,
        )
    }
    fn active(&self, now: Duration) -> bool {
        self.from != self.target
            && now
                .checked_sub(self.start)
                .is_some_and(|d| d < self.duration)
    }
}
struct Playing {
    definition: Animation,
    clip: Clip,
    start: Duration,
}
#[derive(Default)]
struct PartPlayer {
    values: BTreeMap<Property, Tween>,
    clip: Option<Playing>,
    last_meter: Option<f64>,
    binding: Option<crate::manifest::Binding>,
    rotation: crate::rotation::Rotation,
    rotation_binding: Option<crate::manifest::RotationBinding>,
}

#[derive(Clone, Copy, Default)]
pub struct Metrics {
    pub cpu_cores: Option<f64>,
    pub rss_bytes: Option<u64>,
    pub io_bytes_per_second: Option<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Frame {
    pub parts: BTreeMap<String, BTreeMap<Property, Value>>,
    pub active: bool,
    /// 終了直後にactive=falseでもbaseの最終frameをpresentする。
    pub changed: bool,
}

pub struct Player {
    clock: Timeline,
    parts: BTreeMap<String, PartPlayer>,
    last: Option<Frame>,
    theme_id: Option<String>,
}
impl Player {
    /// collector/workloadの対応が変わったとき、別作業の最終値を表示し続けない。
    pub fn clear_metrics(&mut self) {
        for part in self.parts.values_mut() {
            part.last_meter = None;
            part.values.remove(&Property::Value);
            part.rotation.clear_metric();
        }
    }
    /// visibility通知はmetrics/stateを更新せずclockだけを変更する。
    pub fn set_visible(
        &mut self,
        now: Duration,
        visible: bool,
    ) -> Result<(), crate::timeline::ClockError> {
        self.clock.set_running(now, visible)
    }
    pub fn new(now: Duration) -> Self {
        Self {
            clock: Timeline::new(now, true),
            parts: BTreeMap::new(),
            last: None,
            theme_id: None,
        }
    }

    /// callerはvisibility変更時にも一度呼び、それ以降は非表示tickを止める。
    /// cpu_coresはfreshな値だけ。欠測はstale classとhold-lastへ変換する。
    pub fn evaluate(
        &mut self,
        theme: &CompiledTheme,
        now: Duration,
        visible: bool,
        reduced_motion: bool,
        states: &BTreeSet<String>,
        cpu_cores: Option<f64>,
    ) -> Result<Frame, Error> {
        self.evaluate_with_metrics(
            theme,
            now,
            visible,
            reduced_motion,
            states,
            Metrics {
                cpu_cores,
                rss_bytes: None,
                io_bytes_per_second: None,
            },
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn evaluate_with_metrics(
        &mut self,
        theme: &CompiledTheme,
        now: Duration,
        visible: bool,
        reduced_motion: bool,
        states: &BTreeSet<String>,
        metrics: Metrics,
    ) -> Result<Frame, Error> {
        self.clock
            .set_running(now, visible)
            .map_err(|_| Error("animation clock reversed/overflowed".into()))?;
        let time = self
            .clock
            .elapsed(now)
            .map_err(|_| Error("animation clock overflow".into()))?;
        if self.theme_id.as_ref() != Some(&theme.manifest.id) {
            self.parts.clear();
            self.theme_id = Some(theme.manifest.id.clone());
        }
        self.parts.retain(|id, _| theme.parts.contains_key(id));
        let mut frame = Frame {
            parts: BTreeMap::new(),
            active: false,
            changed: false,
        };
        let cpu = metrics.cpu_cores.filter(|v| v.is_finite() && *v >= 0.);
        let mut states = states.clone();
        if cpu.is_none() {
            states.insert("telemetry-stale".into());
            states.remove("busy");
        } else {
            states.remove("telemetry-stale");
        }
        for id in theme.parts.keys() {
            let part = self.parts.entry(id.clone()).or_default();
            let style = theme.style.resolve(id, &states);
            let mut base = BTreeMap::from([
                (Property::Opacity, Value::Number(1.)),
                (Property::Tint, Value::Color([1.; 4])),
                (Property::Transform, Value::Transform(vec![])),
            ]);
            base.extend(style.values);
            let binding = theme.manifest.bindings.iter().find(|b| &b.part == id);
            if part.binding.as_ref() != binding {
                part.last_meter = None;
                part.values.remove(&Property::Value);
                part.binding = binding.cloned();
            }
            if let Some(binding) = binding {
                if let Some(value) = binding.map(cpu) {
                    part.last_meter = Some(value);
                }
                if let Some(value) = part.last_meter {
                    base.insert(Property::Value, Value::Number(value));
                }
            }
            part.values.retain(|p, _| base.contains_key(p));
            let mut values = BTreeMap::new();
            for (property, target) in base {
                let transition = style.transitions.as_ref().and_then(|t| t.get(&property));
                let duration = if reduced_motion {
                    Duration::ZERO
                } else {
                    transition.map(|t| t.duration).unwrap_or_default()
                };
                let easing = transition.map(|t| t.easing).unwrap_or_default();
                if let Some(previous) = part.values.get_mut(&property) {
                    if previous.target != target || reduced_motion && previous.active(time) {
                        let from = previous
                            .sample(time)
                            .ok_or_else(|| Error("incompatible transition value".into()))?;
                        if !duration.is_zero() && from.interpolate(&target, 0.).is_none() {
                            return Err(Error("transition transform functions mismatch".into()));
                        }
                        *previous = Tween {
                            from,
                            target,
                            start: time,
                            duration,
                            easing,
                        };
                    }
                } else {
                    part.values.insert(
                        property,
                        Tween {
                            from: target.clone(),
                            target,
                            start: time,
                            duration: Duration::ZERO,
                            easing,
                        },
                    );
                }
                let tween = &part.values[&property];
                values.insert(
                    property,
                    tween
                        .sample(time)
                        .ok_or_else(|| Error("invalid tween clock".into()))?,
                );
                frame.active |= tween.active(time);
            }
            let animation = if reduced_motion {
                None
            } else {
                style.animation.flatten()
            };
            if let Some(definition) = animation {
                let clip = &theme.style.clips[&definition.name];
                if !part
                    .clip
                    .as_ref()
                    .is_some_and(|p| p.definition == definition && p.clip == *clip)
                {
                    part.clip = Some(Playing {
                        definition,
                        clip: clip.clone(),
                        start: time,
                    });
                }
                let p = part.clip.as_ref().unwrap();
                let elapsed = time
                    .checked_sub(p.start)
                    .ok_or_else(|| Error("invalid clip clock".into()))?;
                let finished = p.definition.count.is_some_and(|count| {
                    elapsed.as_nanos() >= p.definition.duration.as_nanos() * u128::from(count)
                });
                if !finished {
                    let fraction = cycle_fraction(elapsed, p.definition.duration)
                        .map_err(|_| Error("zero clip duration".into()))?;
                    values.extend(
                        p.clip
                            .sample(fraction, p.definition.easing)
                            .ok_or_else(|| Error("invalid clip".into()))?,
                    );
                    frame.active = true;
                }
            } else {
                part.clip = None;
            }
            let binding = theme.manifest.rotations.iter().find(|r| &r.part == id);
            if part.rotation_binding.as_ref() != binding {
                part.rotation.clear_metric();
                part.rotation_binding = binding.cloned();
            }
            if let Some(binding) = binding {
                let sample = match binding.metric.as_str() {
                    "workload.cpu.cores" => {
                        cpu.map(|v| if states.contains("busy") { v } else { 0. })
                    }
                    "workload.memory.rss_bytes" => metrics.rss_bytes.map(|v| v as f64),
                    "workload.io.bytes_per_second" => metrics
                        .io_bytes_per_second
                        .filter(|v| v.is_finite() && *v >= 0.),
                    _ => None,
                };
                let speed = sample
                    .map(|v| (v / binding.input_max).clamp(0., 1.) * binding.degrees_per_second);
                let (angle, active) = part
                    .rotation
                    .sample(time, speed, reduced_motion || !visible);
                let transforms = match values.get(&Property::Transform) {
                    Some(Value::Transform(v)) => v.clone(),
                    _ => Vec::new(),
                };
                values.insert(
                    Property::Transform,
                    Value::Transform(
                        transforms
                            .into_iter()
                            .chain([Transform::Rotate(angle)])
                            .collect(),
                    ),
                );
                if sample.is_none()
                    && let Some(Value::Number(opacity)) = values.get_mut(&Property::Opacity)
                {
                    *opacity *= 0.35;
                }
                frame.active |= active;
            }
            frame.parts.insert(id.clone(), values);
        }
        frame.active &= visible;
        frame.changed = self
            .last
            .as_ref()
            .is_none_or(|old| old.parts != frame.parts);
        self.last = Some(frame.clone());
        Ok(frame)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{raster, style::Transform};
    use eidolon_model::pixels::PixelBudget;
    fn s(v: u64) -> Duration {
        Duration::from_secs(v)
    }
    #[test]
    fn legacy_v1_keeps_phase_on_focus_sample_and_equivalent_reload() {
        let mut bundle = crate::cyan_orbit();
        let manifest = String::from_utf8(bundle.files["theme.toml"].clone()).unwrap();
        bundle.files.insert(
            "theme.toml".into(),
            manifest
                .split("[[rotations]]")
                .next()
                .unwrap()
                .replace("format_version = 2", "format_version = 1")
                .into_bytes(),
        );
        let mut css = String::from_utf8(bundle.files["style.css"].clone()).unwrap();
        css.push_str("\n#ring-outer { animation: spin-cw 12s linear infinite; transform: rotate(0deg); }\n#ring-middle { animation: spin-ccw 18s linear infinite; transform: rotate(0deg); }\n#ring-inner { animation: spin-cw 8s linear infinite; }");
        bundle.files.insert("style.css".into(), css.into_bytes());
        let budget = PixelBudget::default();
        let quota = raster::RasterBudget::default();
        let theme = raster::compile(&bundle, &BTreeMap::new(), &budget, &quota).unwrap();
        let mut player = Player::new(s(0));
        let states = BTreeSet::new();
        player
            .evaluate(&theme, s(0), true, false, &states, None)
            .unwrap();
        let focused = BTreeSet::from(["focused".into()]);
        let frame = player
            .evaluate(&theme, s(3), true, false, &focused, Some(1.))
            .unwrap();
        for (id, angle) in [
            ("ring-outer", 90.),
            ("ring-middle", -60.),
            ("ring-inner", 135.),
        ] {
            assert_eq!(
                frame.parts[id][&Property::Transform],
                Value::Transform(vec![Transform::Rotate(angle)])
            );
        }
        assert!(frame.active && frame.changed);
        let reloaded = raster::compile(&bundle, &BTreeMap::new(), &budget, &quota).unwrap();
        let frame = player
            .evaluate(&reloaded, s(3), true, false, &focused, Some(1.))
            .unwrap();
        assert!(!frame.changed);
        player
            .evaluate(&theme, s(3), false, false, &states, None)
            .unwrap();
        let frame = player
            .evaluate(&theme, s(50), true, false, &states, None)
            .unwrap();
        assert_eq!(
            frame.parts["ring-outer"][&Property::Transform],
            Value::Transform(vec![Transform::Rotate(90.)])
        );
        let reduced = player
            .evaluate(&theme, s(50), true, true, &states, None)
            .unwrap();
        assert_eq!(
            reduced.parts["ring-outer"][&Property::Transform],
            Value::Transform(vec![Transform::Rotate(0.)])
        );
        assert!(!reduced.active && reduced.changed);
        let unchanged = player
            .evaluate(&theme, s(51), true, true, &states, None)
            .unwrap();
        assert!(!unchanged.active && !unchanged.changed);
    }
    #[test]
    fn builtin_cpu_and_rss_rotate_independently_and_missing_does_not_catch_up() {
        let theme = raster::compile(
            &crate::cyan_orbit(),
            &BTreeMap::new(),
            &Default::default(),
            &Default::default(),
        )
        .unwrap();
        let mut player = Player::new(s(0));
        let busy = BTreeSet::from(["busy".into()]);
        let idle = BTreeSet::new();
        let metrics = Metrics {
            cpu_cores: Some(4.),
            rss_bytes: Some(67108864),
            io_bytes_per_second: None,
        };
        let angle = |frame: &Frame, id: &str| match &frame.parts[id][&Property::Transform] {
            Value::Transform(t) => match t.last().unwrap() {
                Transform::Rotate(v) => *v,
                _ => panic!("rotation"),
            },
            _ => panic!("transform"),
        };
        player
            .evaluate_with_metrics(&theme, s(0), true, false, &busy, metrics)
            .unwrap();
        let running = player
            .evaluate_with_metrics(&theme, s(1), true, false, &busy, metrics)
            .unwrap();
        assert_eq!(angle(&running, "ring-outer"), 52.5);
        assert_eq!(angle(&running, "ring-middle"), 325.);
        assert_eq!(angle(&running, "ring-inner"), 0.); // I/O未計測を動きで偽らない。
        let half = Metrics {
            rss_bytes: Some(33554432),
            ..metrics
        };
        player
            .evaluate_with_metrics(&theme, s(1), true, false, &idle, half)
            .unwrap();
        let idle_cpu = player
            .evaluate_with_metrics(&theme, s(2), true, false, &idle, half)
            .unwrap();
        assert_eq!(angle(&idle_cpu, "ring-outer"), 60.);
        assert_eq!(angle(&idle_cpu, "ring-middle"), 302.5);
        assert!(idle_cpu.active); // CPU停止でもRSSは独立して回る。
        let reduced = player
            .evaluate_with_metrics(&theme, s(2), true, true, &idle, half)
            .unwrap();
        assert!(!reduced.active);
        assert_eq!(angle(&reduced, "ring-middle"), 302.5);
        let missing = player
            .evaluate_with_metrics(&theme, s(10), true, false, &busy, Metrics::default())
            .unwrap();
        assert_eq!(angle(&missing, "ring-middle"), 302.5);
        assert!(!missing.active);
        let returned = player
            .evaluate_with_metrics(&theme, s(20), true, false, &busy, metrics)
            .unwrap();
        assert_eq!(angle(&returned, "ring-middle"), 302.5);
        let reloaded = raster::compile(
            &crate::cyan_orbit(),
            &BTreeMap::new(),
            &Default::default(),
            &Default::default(),
        )
        .unwrap();
        let focused = BTreeSet::from(["focused".into(), "busy".into()]);
        let focused_frame = player
            .evaluate_with_metrics(&reloaded, s(20), true, false, &focused, metrics)
            .unwrap();
        assert_eq!(angle(&focused_frame, "ring-middle"), 302.5);
    }
    #[test]
    fn never_received_cpu_has_no_fill_but_stale_preserves_last_value() {
        let theme = raster::compile(
            &crate::cyan_orbit(),
            &BTreeMap::new(),
            &PixelBudget::default(),
            &raster::RasterBudget::default(),
        )
        .unwrap();
        let mut p = Player::new(s(0));
        let states = BTreeSet::from(["busy".into()]);
        let empty = p.evaluate(&theme, s(0), true, true, &states, None).unwrap();
        assert!(!empty.parts["cpu-meter"].contains_key(&Property::Value));
        let fresh = p
            .evaluate(&theme, s(1), true, true, &states, Some(2.))
            .unwrap();
        assert_eq!(
            fresh.parts["cpu-meter"][&Property::Value],
            Value::Number(0.5)
        );
        let stale = p.evaluate(&theme, s(2), true, true, &states, None).unwrap();
        assert_eq!(
            stale.parts["cpu-meter"][&Property::Value],
            Value::Number(0.5)
        );
        assert_ne!(
            stale.parts["cpu-meter"][&Property::Opacity],
            fresh.parts["cpu-meter"][&Property::Opacity]
        );
        p.clear_metrics();
        let changed_work = p.evaluate(&theme, s(3), true, true, &states, None).unwrap();
        assert!(!changed_work.parts["cpu-meter"].contains_key(&Property::Value));
    }
    #[test]
    fn inner_io_rotation_is_independent_zero_stops_and_missing_holds() {
        let theme = raster::compile(
            &crate::cyan_orbit(),
            &BTreeMap::new(),
            &Default::default(),
            &Default::default(),
        )
        .unwrap();
        let mut player = Player::new(s(0));
        let states = BTreeSet::new(); // CPU stale/idle does not gate IO.
        let angle = |frame: &Frame| match &frame.parts["ring-inner"][&Property::Transform] {
            Value::Transform(t) => match t.last().unwrap() {
                Transform::Rotate(v) => *v,
                _ => panic!("rotation"),
            },
            _ => panic!("transform"),
        };
        let mut metrics = Metrics {
            io_bytes_per_second: Some(1048576.),
            ..Default::default()
        };
        player
            .evaluate_with_metrics(&theme, s(0), true, false, &states, metrics)
            .unwrap();
        let running = player
            .evaluate_with_metrics(&theme, s(1), true, false, &states, metrics)
            .unwrap();
        assert_eq!(angle(&running), 26.25);
        assert!(running.active);
        metrics.io_bytes_per_second = Some(0.);
        player
            .evaluate_with_metrics(&theme, s(1), true, false, &states, metrics)
            .unwrap();
        let zero = player
            .evaluate_with_metrics(&theme, s(2), true, false, &states, metrics)
            .unwrap();
        assert_eq!(angle(&zero), 30.);
        assert!(!zero.active);
        metrics.io_bytes_per_second = None;
        let missing = player
            .evaluate_with_metrics(&theme, s(20), true, false, &states, metrics)
            .unwrap();
        assert_eq!(angle(&missing), 30.);
        assert!(!missing.active);
        assert_ne!(
            zero.parts["ring-inner"][&Property::Opacity],
            missing.parts["ring-inner"][&Property::Opacity]
        );
        metrics.io_bytes_per_second = Some(1048576.);
        let resumed = player
            .evaluate_with_metrics(&theme, s(40), true, false, &states, metrics)
            .unwrap();
        assert_eq!(angle(&resumed), 30.); // no catch-up
        let reduced = player
            .evaluate_with_metrics(&theme, s(41), true, true, &states, metrics)
            .unwrap();
        assert_eq!(angle(&reduced), 30.);
        assert!(!reduced.active);
    }
}
