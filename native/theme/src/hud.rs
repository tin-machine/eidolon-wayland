use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Metric {
    Fps,
    ReusedFrames,
    Cpu,
    Ram,
    AppSlots,
    Battery,
    GpuTime,
    WindowUpdates,
    DrawP95,
    Thermal,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Comparison {
    Below,
    Above,
    AtLeast,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Layout {
    #[default]
    Rings,
    Bar,
    Alerts,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Anchor {
    #[default]
    Space,
    Head,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RingCap {
    #[default]
    Flat,
    Round,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RingLayout {
    Stacked,
    Inline,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Rule {
    pub comparison: Comparison,
    pub threshold: f64,
    pub color: Option<String>,
    #[serde(default)]
    pub motion_ms: u32,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct Profile {
    pub layout: Layout,
    pub anchor: Anchor,
    pub offset_m: Option<[f64; 3]>,
    pub scale: Option<f64>,
    pub face_viewer: Option<bool>,
    pub ring_cap: RingCap,
    pub ring_layout: Option<RingLayout>,
    pub stack_width_dp: Option<f64>,
    pub stack_height_dp: Option<f64>,
    pub stack_stroke_dp: Option<f64>,
    pub normal_color: Option<String>,
    pub metrics: Option<Vec<Metric>>,
    pub rules: Option<BTreeMap<Metric, Rule>>,
}

fn valid_color(color: &str) -> bool {
    color.len() == 7
        && color.starts_with('#')
        && color.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
}

impl Profile {
    pub(crate) fn valid(&self) -> bool {
        self.offset_m.is_none_or(|[x, y, z]| {
            (-1.0..=1.0).contains(&x) && (-0.9..=0.65).contains(&y) && (0.8..=2.5).contains(&z)
        }) && self.scale.is_none_or(|x| (0.65..=1.35).contains(&x))
            && self
                .stack_width_dp
                .is_none_or(|x| (72.0..=156.0).contains(&x))
            && self
                .stack_height_dp
                .is_none_or(|x| (34.0..=52.0).contains(&x))
            && self
                .stack_stroke_dp
                .is_none_or(|x| (2.0..=8.0).contains(&x))
            && self.normal_color.as_deref().is_none_or(valid_color)
            && self.metrics.as_ref().is_none_or(|metrics| {
                !metrics.is_empty()
                    && metrics.iter().copied().collect::<BTreeSet<_>>().len() == metrics.len()
            })
            && self.rules.as_ref().is_none_or(|rules| {
                rules.values().all(|rule| {
                    (0.0..=10_000.0).contains(&rule.threshold)
                        && (rule.motion_ms == 0 || (250..=3000).contains(&rule.motion_ms))
                        && rule.color.as_deref().is_none_or(valid_color)
                })
            })
    }
}
