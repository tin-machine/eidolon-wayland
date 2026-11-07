//! format v1/v2の入口。schemaの検証前にasset I/OやGPU allocationを開始しない。
use serde::Deserialize;
use std::collections::{BTreeMap, BTreeSet};

pub const MAX_PARTS: usize = 16;
pub const MAX_MANIFEST_BYTES: usize = 64 * 1024;

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub format_version: u32,
    pub id: String,
    pub name: String,
    pub style: String,
    pub parts: BTreeMap<String, Part>,
    #[serde(default)]
    pub bindings: Vec<Binding>,
    #[serde(default)]
    pub rotations: Vec<RotationBinding>,
}

#[derive(Clone, Debug, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RotationBinding {
    pub part: String,
    pub metric: String,
    pub input_max: f64,
    pub degrees_per_second: f64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Kind {
    SvgMask,
    Svg,
    Meter,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Slot {
    WindowBackdrop,
    WindowFrame,
    TitlebarEnd,
    TitlebarBottom,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Part {
    pub kind: Kind,
    pub slot: Slot,
    pub src: Option<String>,
    pub size: Option<[f64; 2]>,
    #[serde(default)]
    pub order: i32,
}

#[derive(Clone, Debug, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    pub part: String,
    pub property: String,
    pub metric: String,
    pub input: [f64; 2],
    pub output: [f64; 2],
    pub clamp: bool,
    pub missing: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Error(pub String);

pub fn identifier(value: &str) -> bool {
    (1..=64).contains(&value.len())
        && value.as_bytes()[0].is_ascii_alphabetic()
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
}

/// archiveとUnix双方で同じ相対path契約。symlink/TOCTOUの検査はloader側でも必要。
pub fn relative_file(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && !value.contains(['\\', ':', '\0'])
        && value
            .split('/')
            .all(|s| !s.is_empty() && s != "." && s != ".." && !s.chars().any(char::is_control))
}

impl Manifest {
    pub fn parse(source: &str) -> Result<Self, Error> {
        if source.len() > MAX_MANIFEST_BYTES {
            return Err(Error("manifest byte limit".into()));
        }
        let value: Self = toml::from_str(source).map_err(|e| Error(format!("theme.toml: {e}")))?;
        value.validate()?;
        Ok(value)
    }

    pub fn validate(&self) -> Result<(), Error> {
        let fail = |reason: &str| Err(Error(reason.into()));
        if !matches!(self.format_version, 1 | 2)
            || self.format_version == 1 && !self.rotations.is_empty()
        {
            return fail("unsupported format_version");
        }
        if !identifier(&self.id)
            || self.name.trim().is_empty()
            || self.name.len() > 128
            || self.name.chars().any(char::is_control)
        {
            return fail("invalid theme id/name");
        }
        if !relative_file(&self.style) || !self.style.ends_with(".css") {
            return fail("invalid style path");
        }
        if self.parts.is_empty() || self.parts.len() > MAX_PARTS || self.bindings.len() > MAX_PARTS
        {
            return fail("part/binding limit");
        }
        for (id, part) in &self.parts {
            if !identifier(id) {
                return fail("invalid part id");
            }
            match (part.kind, &part.src) {
                (Kind::Meter, None) => (),
                (Kind::Svg | Kind::SvgMask, Some(src))
                    if relative_file(src) && src.ends_with(".svg") => {}
                _ => return fail("invalid part source"),
            }
            if part
                .size
                .is_some_and(|s| s.iter().any(|v| !v.is_finite() || *v <= 0. || *v > 2048.))
            {
                return fail("invalid part size");
            }
        }
        let mut bound = BTreeSet::new();
        for b in &self.bindings {
            if !self
                .parts
                .get(&b.part)
                .is_some_and(|p| p.kind == Kind::Meter)
                || b.property != "-eidolon-value"
                || b.metric != "workload.cpu.cores"
                || b.missing != "hold-last"
                || !b.clamp
                || !bound.insert(&b.part)
                || b.input.iter().any(|v| !v.is_finite())
                || b.input[0] >= b.input[1]
                || !(b.input[1] - b.input[0]).is_finite()
                || b.output
                    .iter()
                    .any(|v| !v.is_finite() || !(0. ..=1.).contains(v))
            {
                return fail("invalid or duplicate binding");
            }
        }
        let mut rotated = BTreeSet::new();
        if self.rotations.len() > MAX_PARTS {
            return fail("rotation limit");
        }
        for r in &self.rotations {
            if !self
                .parts
                .get(&r.part)
                .is_some_and(|p| p.kind != Kind::Meter)
                || !matches!(
                    r.metric.as_str(),
                    "workload.cpu.cores"
                        | "workload.memory.rss_bytes"
                        | "workload.io.bytes_per_second"
                )
                || !r.input_max.is_finite()
                || r.input_max <= 0.
                || !r.degrees_per_second.is_finite()
                || r.degrees_per_second.abs() > 360.
                || !rotated.insert(&r.part)
            {
                return fail("invalid or duplicate rotation binding");
            }
        }
        Ok(())
    }

    pub fn ordered_parts(&self) -> Vec<(&str, &Part)> {
        let mut parts: Vec<_> = self.parts.iter().map(|(id, p)| (id.as_str(), p)).collect();
        parts.sort_by_key(|(id, p)| (p.slot, p.order, *id));
        parts
    }
}

impl Binding {
    /// 欠測はNoneのまま。hold-lastとstaleはcallerの状態機械が所有する。
    pub fn map(&self, sample: Option<f64>) -> Option<f64> {
        let sample = sample.filter(|v| v.is_finite())?;
        let t = ((sample.clamp(self.input[0], self.input[1]) - self.input[0])
            / (self.input[1] - self.input[0]))
            .clamp(0., 1.);
        let value = self.output[0] * (1. - t) + self.output[1] * t;
        value.is_finite().then_some(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const SOURCE: &str = r#"
format_version = 1
id = "cyan-orbit"
name = "Cyan Orbit"
style = "style.css"
[parts.ring]
kind = "svg-mask"
slot = "window-backdrop"
src = "assets/ring.svg"
size = [1200.0, 1200.0]
[parts.cpu-meter]
kind = "meter"
slot = "titlebar-bottom"
[[bindings]]
part = "cpu-meter"
property = "-eidolon-value"
metric = "workload.cpu.cores"
input = [0.0, 4.0]
output = [0.0, 1.0]
clamp = true
missing = "hold-last"
"#;
    #[test]
    fn rotations_require_v2_and_one_bounded_writer_per_existing_part() {
        let source = include_str!("../assets/cyan-orbit/theme.toml");
        assert!(Manifest::parse(source).is_ok());
        for (before, after) in [
            ("format_version = 2", "format_version = 1"),
            ("input_max = 4.0", "input_max = 0.0"),
            ("degrees_per_second = 60.0", "degrees_per_second = inf"),
            ("degrees_per_second = 60.0", "degrees_per_second = 361.0"),
            ("part = \"ring-middle\"", "part = \"ring-outer\""),
            ("workload.memory.rss_bytes", "workload.disk.bytes"),
        ] {
            assert!(
                Manifest::parse(&source.replace(before, after)).is_err(),
                "{before}"
            );
        }
    }
    #[test]
    fn manifest_and_binding_keep_missing_distinct_from_zero() {
        let m = Manifest::parse(SOURCE).unwrap();
        assert_eq!(m.ordered_parts()[0].0, "ring");
        assert_eq!(m.bindings[0].map(Some(2.)), Some(0.5));
        assert_eq!(m.bindings[0].map(Some(10.)), Some(1.));
        assert_eq!(m.bindings[0].map(Some(0.)), Some(0.));
        assert_eq!(m.bindings[0].map(None), None);
        assert_eq!(m.bindings[0].map(Some(f64::NAN)), None);
    }
    #[test]
    fn reject_schema_drift_escape_nonfinite_and_ambiguous_binding() {
        for (old, new) in [
            ("format_version = 1", "format_version = 3"),
            ("name =", "unknown ="),
            ("window-backdrop", "window-unknown"),
            ("assets/ring.svg", "../ring.svg"),
            ("1200.0, 1200.0", "nan, 1200.0"),
            ("0.0, 4.0", "4.0, 4.0"),
            ("clamp = true", "clamp = false"),
            ("part = \"cpu-meter\"", "part = \"ring\""),
        ] {
            assert!(Manifest::parse(&SOURCE.replace(old, new)).is_err(), "{new}");
        }
        assert!(Manifest::parse(&(SOURCE.to_string() + "\n[parts.ring]\nkind='svg'\n")).is_err());
        for path in [
            "/a.svg",
            "a/../b.svg",
            "a//b.svg",
            "a\\b.svg",
            "https://host/a.svg",
            "a/./b.svg",
        ] {
            assert!(!relative_file(path));
        }
    }
}
