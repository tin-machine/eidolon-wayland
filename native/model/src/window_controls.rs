//! Fixed window actions; themes select bounded positions, never code or window identities.
use serde::{Deserialize, Serialize};
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum ControlAnchor {
    TopLeft,
    #[default]
    TopRight,
    BottomLeft,
    BottomRight,
}
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct WindowControls {
    pub anchor: ControlAnchor,
    /// Nonnegative distances outward from the chosen body corner, in metres.
    pub offset_m: [f64; 2],
    pub diameter_m: f64,
    pub gap_m: f64,
}
impl Default for WindowControls {
    fn default() -> Self {
        Self {
            anchor: ControlAnchor::TopRight,
            offset_m: [0., 0.],
            diameter_m: 0.08,
            gap_m: 0.025,
        }
    }
}
impl WindowControls {
    pub fn valid(&self) -> bool {
        self.offset_m
            .iter()
            .all(|n| n.is_finite() && (0. ..=0.2).contains(n))
            && self.diameter_m.is_finite()
            && (0.06..=0.12).contains(&self.diameter_m)
            && self.gap_m.is_finite()
            && (0.02..=0.08).contains(&self.gap_m)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn defaults_remain_accessible_and_configuration_cannot_carry_actions() {
        assert!(WindowControls::default().valid());
        let value: WindowControls = serde_json::from_str(
            r#"{"anchor":"bottom-left","diameter_m":0.1,"offset_m":[0.1,0.2]}"#,
        )
        .unwrap();
        assert!(value.valid());
        for input in [
            r#"{"script":"exit"}"#,
            r#"{"anchor":"world"}"#,
            r#"{"window":"1"}"#,
        ] {
            assert!(serde_json::from_str::<WindowControls>(input).is_err());
        }
    }
    #[test]
    fn dimensions_and_offsets_are_bounded_not_clamped() {
        for diameter in [f64::NAN, f64::INFINITY, 0., 0.059, 0.121] {
            assert!(
                !WindowControls {
                    diameter_m: diameter,
                    ..WindowControls::default()
                }
                .valid()
            );
        }
        for offset in [-0.01, 0.201, f64::NAN] {
            assert!(
                !WindowControls {
                    offset_m: [offset, 0.],
                    ..WindowControls::default()
                }
                .valid()
            );
        }
        assert!(
            !WindowControls {
                gap_m: 0.,
                ..WindowControls::default()
            }
            .valid()
        );
    }
}
