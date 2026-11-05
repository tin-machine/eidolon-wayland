//! CSSの限定構文を型へ変換する。未知構文をブラウザのように黙って捨てない。
use crate::{
    manifest::{Error, Kind, Manifest, identifier},
    timeline::Easing,
};
use cssparser::{Delimiter, ParseError, Parser, Token};
use std::{
    collections::{BTreeMap, BTreeSet},
    time::Duration,
};

type ResultAt<'i, T> = Result<T, ParseError<String>>;
const STATES: &[&str] = &[
    "focused",
    "busy",
    "pressure",
    "disconnected",
    "telemetry-stale",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Property {
    Opacity,
    Tint,
    Value,
    Transform,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Transform {
    Translate(f64, f64),
    Rotate(f64),
    Scale(f64, f64),
}

#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Number(f64),
    Color([f64; 4]),
    Transform(Vec<Transform>),
}

impl Value {
    pub fn interpolate(&self, other: &Self, progress: f64) -> Option<Self> {
        if !progress.is_finite() {
            return None;
        }
        let t = progress.clamp(0., 1.);
        let mix = |a: f64, b: f64| a * (1. - t) + b * t;
        Some(match (self, other) {
            (Self::Number(a), Self::Number(b)) => Self::Number(mix(*a, *b)),
            (Self::Color(a), Self::Color(b)) => {
                Self::Color(std::array::from_fn(|i| mix(a[i], b[i])))
            }
            (Self::Transform(a), Self::Transform(b)) if a.len() == b.len() => {
                let mut out = vec![];
                for (a, b) in a.iter().zip(b) {
                    out.push(match (a, b) {
                        (Transform::Translate(x, y), Transform::Translate(u, v)) => {
                            Transform::Translate(mix(*x, *u), mix(*y, *v))
                        }
                        (Transform::Rotate(a), Transform::Rotate(b)) => {
                            Transform::Rotate(mix(*a, *b))
                        }
                        (Transform::Scale(x, y), Transform::Scale(u, v)) => {
                            Transform::Scale(mix(*x, *u), mix(*y, *v))
                        }
                        _ => return None,
                    });
                }
                Self::Transform(out)
            }
            _ => return None,
        })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Animation {
    pub name: String,
    pub duration: Duration,
    pub easing: Easing,
    pub count: Option<u32>,
}
#[derive(Clone, Debug, PartialEq)]
pub struct Transition {
    pub duration: Duration,
    pub easing: Easing,
}
#[derive(Clone, Debug, Default)]
pub struct Declarations {
    pub values: BTreeMap<Property, Value>,
    pub animation: Option<Option<Animation>>,
    pub transitions: Option<BTreeMap<Property, Transition>>,
}
#[derive(Clone, Debug)]
pub struct Rule {
    pub part: String,
    pub states: BTreeSet<String>,
    pub declarations: Declarations,
}
#[derive(Clone, Debug, PartialEq)]
pub struct Clip {
    pub frames: Vec<(f64, BTreeMap<Property, Value>)>,
}
#[derive(Clone, Debug, Default)]
pub struct Stylesheet {
    pub rules: Vec<Rule>,
    pub clips: BTreeMap<String, Clip>,
}

fn fail<'i, T>(p: &Parser<'i>, message: &str) -> ResultAt<'i, T> {
    Err(ParseError::custom(format!(
        "{}:{}: {message}",
        p.current_source_location().line + 1,
        p.current_source_location().column
    )))
}
fn property(name: &str) -> Option<Property> {
    match name {
        "opacity" => Some(Property::Opacity),
        "-eidolon-tint" => Some(Property::Tint),
        "-eidolon-value" => Some(Property::Value),
        "transform" => Some(Property::Transform),
        _ => None,
    }
}
fn number<'i>(p: &mut Parser<'i>) -> ResultAt<'i, f64> {
    let v = f64::from(p.expect_number()?);
    if !v.is_finite() {
        return fail(p, "nonfinite number");
    }
    Ok(v)
}
fn dimension<'i>(p: &mut Parser<'i>, unit: &str) -> ResultAt<'i, f64> {
    match p.next()?.clone() {
        Token::Dimension {
            value, unit: got, ..
        } if got == unit && value.is_finite() => Ok(f64::from(value)),
        _ => fail(p, "invalid dimension/unit"),
    }
}
fn duration<'i>(p: &mut Parser<'i>, zero: bool) -> ResultAt<'i, Duration> {
    let value = match p.next()?.clone() {
        Token::Dimension { value, unit, .. } if unit == "ms" => f64::from(value) / 1000.,
        Token::Dimension { value, unit, .. } if unit == "s" => f64::from(value),
        _ => return fail(p, "duration requires ms/s"),
    };
    if !value.is_finite() || !(0. ..=86400.).contains(&value) || !zero && value < 0.001 {
        return fail(p, "duration outside limit");
    }
    Ok(Duration::from_secs_f64(value))
}
fn easing<'i>(p: &mut Parser<'i>) -> ResultAt<'i, Easing> {
    match p.expect_ident()?.as_ref() {
        "linear" => Ok(Easing::Linear),
        "ease" => Ok(Easing::Ease),
        "ease-in" => Ok(Easing::EaseIn),
        "ease-out" => Ok(Easing::EaseOut),
        "ease-in-out" => Ok(Easing::EaseInOut),
        _ => fail(p, "unsupported easing"),
    }
}
fn value<'i>(p: &mut Parser<'i>, property: Property) -> ResultAt<'i, Value> {
    match property {
        Property::Opacity | Property::Value => {
            let v = number(p)?;
            if !(0. ..=1.).contains(&v) {
                return fail(p, "value outside 0..1");
            }
            Ok(Value::Number(v))
        }
        Property::Tint => {
            let hex = match p.next()?.clone() {
                Token::Hash(v) | Token::IDHash(v) => v,
                _ => return fail(p, "color requires #RRGGBB[AA]"),
            };
            if ![6, 8].contains(&hex.len()) || !hex.bytes().all(|c| c.is_ascii_hexdigit()) {
                return fail(p, "invalid color");
            }
            let mut color = [1.; 4];
            for (i, component) in color.iter_mut().enumerate().take(hex.len() / 2) {
                *component =
                    f64::from(u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).unwrap()) / 255.;
            }
            Ok(Value::Color(color))
        }
        Property::Transform => {
            let mut ops = vec![];
            while !p.is_exhausted() {
                let name = p.expect_function()?.to_string();
                let op = p.parse_nested_block(|p| {
                    let op = match name.as_str() {
                        "translate" => {
                            let x = dimension(p, "px")?;
                            p.expect_comma()?;
                            let y = dimension(p, "px")?;
                            if x.abs() > 8192. || y.abs() > 8192. {
                                return fail(p, "translation limit");
                            }
                            Transform::Translate(x, y)
                        }
                        "rotate" => {
                            let angle = dimension(p, "deg")?;
                            if angle.abs() > 1e9 {
                                return fail(p, "angle limit");
                            }
                            Transform::Rotate(angle)
                        }
                        "scale" => {
                            let x = number(p)?;
                            p.expect_comma()?;
                            let y = number(p)?;
                            if x <= 0. || y <= 0. || x > 16. || y > 16. {
                                return fail(p, "scale limit");
                            }
                            Transform::Scale(x, y)
                        }
                        _ => return fail(p, "unsupported transform"),
                    };
                    p.expect_exhausted()?;
                    Ok(op)
                })?;
                ops.push(op);
                if ops.len() > 8 {
                    return fail(p, "transform count limit");
                }
            }
            if ops.is_empty() {
                return fail(p, "empty transform");
            }
            Ok(Value::Transform(ops))
        }
    }
}

fn declarations<'i>(p: &mut Parser<'i>, controls: bool) -> ResultAt<'i, Declarations> {
    let mut out = Declarations::default();
    let mut seen = BTreeSet::new();
    while !p.is_exhausted() {
        let name = p.expect_ident()?.to_string();
        p.expect_colon()?;
        if !seen.insert(name.clone()) {
            return fail(p, "duplicate declaration");
        }
        p.parse_until_before(Delimiter::Semicolon, |p| {
            if let Some(property) = property(&name) {
                out.values.insert(property, value(p, property)?);
            } else if controls && name == "animation" {
                let name = p.expect_ident()?.to_string();
                out.animation = Some(if name == "none" {
                    None
                } else {
                    if !identifier(&name) {
                        return fail(p, "invalid animation name");
                    }
                    let duration = duration(p, false)?;
                    let easing = easing(p)?;
                    let count = if p.try_parse(|p| p.expect_ident_matching("infinite")).is_ok() {
                        None
                    } else {
                        let count = p.expect_integer()?;
                        if !(1..=1000000).contains(&count) {
                            return fail(p, "iteration count limit");
                        }
                        Some(count as u32)
                    };
                    Some(Animation {
                        name,
                        duration,
                        easing,
                        count,
                    })
                });
            } else if controls && name == "transition" {
                let items = p.parse_comma_separated(|p| {
                    let name = p.expect_ident()?.to_string();
                    let Some(property) = property(&name) else {
                        return fail(p, "unsupported transition property");
                    };
                    let v = Transition {
                        duration: duration(p, true)?,
                        easing: easing(p)?,
                    };
                    p.expect_exhausted()?;
                    Ok((property, v))
                })?;
                let map: BTreeMap<_, _> = items.iter().cloned().collect();
                if map.len() != items.len() {
                    return fail(p, "duplicate transition");
                }
                out.transitions = Some(map);
            } else {
                return fail(p, "unsupported property");
            }
            p.expect_exhausted()?;
            Ok(())
        })?;
        if !p.is_exhausted() {
            p.expect_semicolon()?;
        }
    }
    Ok(out)
}

impl Stylesheet {
    pub fn parse(source: &str, manifest: &Manifest) -> Result<Self, Error> {
        if source.len() > 256 * 1024 {
            return Err(Error("CSS byte limit".into()));
        }
        let mut p = Parser::new(source);
        let parsed = Self::parse_inner(&mut p).map_err(|e| {
            Error(format!(
                "style.css:{}:{}: {:?}",
                p.current_source_location().line + 1,
                p.current_source_location().column,
                e.kind
            ))
        })?;
        parsed.validate(manifest)?;
        Ok(parsed)
    }
    fn parse_inner<'i>(p: &mut Parser<'i>) -> ResultAt<'i, Self> {
        let mut out = Self::default();
        while !p.is_exhausted() {
            match p.next()?.clone() {
                Token::IDHash(id) => {
                    let mut states = BTreeSet::new();
                    loop {
                        match p.next_including_whitespace()?.clone() {
                            Token::Delim('.') => {
                                let state = match p.next_including_whitespace()?.clone() {
                                    Token::Ident(s) => s.to_string(),
                                    _ => return fail(p, "invalid state selector"),
                                };
                                if !STATES.contains(&state.as_str()) || !states.insert(state) {
                                    return fail(p, "unknown or duplicate state");
                                }
                            }
                            Token::WhiteSpace(_) => {
                                p.expect_curly_bracket_block()?;
                                break;
                            }
                            Token::CurlyBracketBlock => break,
                            _ => return fail(p, "unsupported selector"),
                        }
                    }
                    let declarations = p.parse_nested_block(|p| declarations(p, true))?;
                    out.rules.push(Rule {
                        part: id.to_string(),
                        states,
                        declarations,
                    });
                    if out.rules.len() > 256 {
                        return fail(p, "rule limit");
                    }
                }
                Token::AtKeyword(keyword) if keyword == "keyframes" => {
                    let name = p.expect_ident()?.to_string();
                    if !identifier(&name) || out.clips.contains_key(&name) {
                        return fail(p, "duplicate/invalid clip");
                    }
                    p.expect_curly_bracket_block()?;
                    let clip = p.parse_nested_block(|p| {
                        let mut frames = vec![];
                        while !p.is_exhausted() {
                            let percent = f64::from(p.expect_percentage()?);
                            if !percent.is_finite() || !(0. ..=1.).contains(&percent) {
                                return fail(p, "invalid keyframe position");
                            }
                            p.expect_curly_bracket_block()?;
                            let d = p.parse_nested_block(|p| declarations(p, false))?;
                            if d.values.is_empty() {
                                return fail(p, "empty keyframe");
                            }
                            frames.push((percent, d.values));
                            if frames.len() > 64 {
                                return fail(p, "keyframe limit");
                            }
                        }
                        frames.sort_by(|a, b| a.0.total_cmp(&b.0));
                        if frames.len() < 2 || frames.windows(2).any(|f| f[0].0 == f[1].0) {
                            return fail(p, "missing/duplicate keyframe");
                        }
                        Ok(Clip { frames })
                    })?;
                    out.clips.insert(name, clip);
                    if out.clips.len() > 32 {
                        return fail(p, "clip limit");
                    }
                }
                _ => return fail(p, "unsupported rule"),
            }
        }
        Ok(out)
    }

    fn validate(&self, m: &Manifest) -> Result<(), Error> {
        let err = |s: &str| Error(s.into());
        for rule in &self.rules {
            if let Some(Some(animation)) = &rule.declarations.animation
                && !self.clips.contains_key(&animation.name)
            {
                return Err(err("missing animation (including overridden declarations)"));
            }
        }
        for clip in self.clips.values() {
            let properties: BTreeSet<_> = clip
                .frames
                .iter()
                .flat_map(|(_, v)| v.keys().copied())
                .collect();
            for property in properties {
                let frames: Vec<_> = clip
                    .frames
                    .iter()
                    .filter_map(|(p, v)| v.get(&property).map(|v| (*p, v)))
                    .collect();
                if frames.first().unwrap().0 != 0.
                    || frames.last().unwrap().0 != 1.
                    || frames
                        .windows(2)
                        .any(|v| v[0].1.interpolate(v[1].1, 0.5).is_none())
                {
                    return Err(err("clip endpoints/transform functions mismatch"));
                }
            }
        }
        for (id, part) in &m.parts {
            let rules: Vec<_> = self.rules.iter().filter(|r| &r.part == id).collect();
            let bound = m.bindings.iter().any(|b| &b.part == id);
            // 全状態組合せを評価する。別stateで排他的に指定された値同士を誤って競合させない。
            for mask in 0..(1 << STATES.len()) {
                let states: BTreeSet<_> = STATES
                    .iter()
                    .enumerate()
                    .filter(|(i, _)| mask & (1 << i) != 0)
                    .map(|(_, s)| (*s).to_owned())
                    .collect();
                let d = self.resolve(id, &states);
                let clip = d
                    .animation
                    .as_ref()
                    .and_then(|a| a.as_ref())
                    .map(|a| {
                        self.clips
                            .get(&a.name)
                            .ok_or_else(|| err("missing animation"))
                    })
                    .transpose()?;
                let animated: BTreeSet<_> = clip
                    .into_iter()
                    .flat_map(|c| c.frames.iter().flat_map(|(_, v)| v.keys().copied()))
                    .collect();
                let all: BTreeSet<_> = d
                    .values
                    .keys()
                    .copied()
                    .chain(animated.iter().copied())
                    .chain(d.transitions.iter().flat_map(|v| v.keys().copied()))
                    .collect();
                if part.kind == Kind::Svg && all.contains(&Property::Tint)
                    || part.kind != Kind::Meter && all.contains(&Property::Value)
                {
                    return Err(err("property not supported for part kind"));
                }
                if bound
                    && (d.values.contains_key(&Property::Value)
                        || animated.contains(&Property::Value))
                {
                    return Err(err("binding/CSS writer conflict"));
                }
                if d.transitions
                    .as_ref()
                    .is_some_and(|t| animated.iter().any(|p| t.contains_key(p)))
                {
                    return Err(err("keyframes/transition writer conflict"));
                }
            }
            // どの状態で選ばれてもtransformの形は同じ。transitionの途中切替で行列分解しない。
            let transforms: Vec<_> = rules
                .iter()
                .filter_map(|r| r.declarations.values.get(&Property::Transform))
                .collect();
            if transforms
                .windows(2)
                .any(|v| v[0].interpolate(v[1], 0.5).is_none())
            {
                return Err(err("state transform functions mismatch"));
            }
        }
        if self.rules.iter().any(|r| !m.parts.contains_key(&r.part)) {
            return Err(err("unknown part selector"));
        }
        Ok(())
    }

    pub fn resolve(&self, part: &str, states: &BTreeSet<String>) -> Declarations {
        let mut rules: Vec<_> = self
            .rules
            .iter()
            .filter(|r| r.part == part && r.states.is_subset(states))
            .collect();
        rules.sort_by_key(|r| r.states.len()); // stable: 同specificityはsourceの後勝ち。
        let mut result = Declarations::default();
        for r in rules {
            result.values.extend(r.declarations.values.clone());
            if r.declarations.animation.is_some() {
                result.animation.clone_from(&r.declarations.animation);
            }
            if r.declarations.transitions.is_some() {
                result.transitions.clone_from(&r.declarations.transitions);
            }
        }
        result
    }
}

impl Clip {
    pub fn sample(&self, progress: f64, easing: Easing) -> Option<BTreeMap<Property, Value>> {
        if !progress.is_finite() {
            return None;
        }
        let p = progress.clamp(0., 1.);
        let properties: BTreeSet<_> = self
            .frames
            .iter()
            .flat_map(|(_, v)| v.keys().copied())
            .collect();
        let mut result = BTreeMap::new();
        for property in properties {
            let frames: Vec<_> = self
                .frames
                .iter()
                .filter_map(|(t, v)| v.get(&property).map(|v| (*t, v)))
                .collect();
            let left = frames.iter().rev().find(|(t, _)| *t <= p)?;
            let right = frames.iter().find(|(t, _)| *t >= p)?;
            let t = if left.0 == right.0 {
                0.
            } else {
                easing.sample((p - left.0) / (right.0 - left.0))?
            };
            result.insert(property, left.1.interpolate(right.1, t)?);
        }
        Some(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn manifest() -> Manifest {
        Manifest::parse("format_version=1\nid='test'\nname='Test'\nstyle='style.css'\n[parts.ring]\nkind='svg-mask'\nslot='window-backdrop'\nsrc='r.svg'").unwrap()
    }
    const CSS: &str = "#ring { opacity: 0.2; transform: rotate(0deg); animation: spin 12s linear infinite; } #ring.focused { opacity: 0.8; } @keyframes spin { 0% {transform:rotate(0deg);} 100% {transform:rotate(360deg);} }";
    #[test]
    fn full_turn_interpolates_without_normalizing_360_to_zero() {
        let sheet = Stylesheet::parse(CSS, &manifest()).unwrap();
        assert_eq!(
            sheet.clips["spin"].sample(0.25, Easing::Linear).unwrap()[&Property::Transform],
            Value::Transform(vec![Transform::Rotate(90.)])
        );
        let focused = sheet.resolve("ring", &BTreeSet::from(["focused".into()]));
        assert_eq!(
            focused.animation.unwrap().unwrap().duration,
            Duration::from_secs(12)
        );
        assert_eq!(
            focused.values[&Property::Opacity],
            Value::Number(f64::from(0.8f32))
        );
    }
    #[test]
    fn unsupported_css_and_conflicting_writers_fail_closed() {
        for css in [
            "#ring .focused { opacity: 0.3; }",
            "#ring:focus { opacity: 0.3; }",
            "#ring { unknown: 1; }",
            "#ring { opacity: calc(1); }",
            "#ring { opacity: 1 !important; }",
            "#ring { transform: translate(1px,2px) scale(0,1); }",
            "@import 'other.css';",
            "#other {opacity:1;}",
            "#ring {animation: absent 1s linear infinite;}",
        ] {
            assert!(Stylesheet::parse(css, &manifest()).is_err(), "{css}");
        }
        assert!(Stylesheet::parse(&CSS.replace("100%", "90%"), &manifest()).is_err());
        assert!(
            Stylesheet::parse(&CSS.replace("rotate(360deg)", "scale(1,1)"), &manifest()).is_err()
        );
        assert!(
            Stylesheet::parse(
                &(CSS.to_string() + " #ring {transition: transform 1s linear;}"),
                &manifest()
            )
            .is_err()
        );
    }
}
