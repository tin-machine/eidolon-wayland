//! 速度を250msで遷移し、その積分で角度を保持する。欠測とpauseで過去の回転を追いかけない。
use std::time::Duration;

const RAMP_SECONDS: f64 = 0.25;

#[derive(Default)]
pub(crate) struct Rotation {
    angle: f64,
    from: f64,
    target: f64,
    ramp_at: Duration,
    last: Option<Duration>,
}
impl Rotation {
    pub fn clear_metric(&mut self) {
        self.from = 0.;
        self.target = 0.;
        self.last = None;
    }
    fn area(&self, time: f64) -> f64 {
        let ramp = time.min(RAMP_SECONDS);
        self.from * ramp
            + (self.target - self.from) * ramp * ramp / (2. * RAMP_SECONDS)
            + self.target * (time - RAMP_SECONDS).max(0.)
    }
    pub fn sample(&mut self, time: Duration, desired: Option<f64>, paused: bool) -> (f64, bool) {
        let Some(desired) = desired.filter(|v| v.is_finite() && !paused) else {
            self.clear_metric();
            self.last = Some(time);
            self.ramp_at = time;
            return (self.angle, false);
        };
        let mut speed = 0.;
        if let Some(last) = self.last {
            let elapsed = time.saturating_sub(self.ramp_at).as_secs_f64();
            let previous = last.saturating_sub(self.ramp_at).as_secs_f64();
            self.angle = (self.angle + self.area(elapsed) - self.area(previous)).rem_euclid(360.);
            speed = self.from + (self.target - self.from) * (elapsed / RAMP_SECONDS).min(1.);
        } else {
            self.ramp_at = time;
        }
        if self.target != desired {
            self.from = speed;
            self.target = desired;
            self.ramp_at = time;
        }
        self.last = Some(time);
        (self.angle, self.target != 0. || speed != 0.)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn speed_changes_preserve_phase_and_stop_after_finite_ramp() {
        let mut r = Rotation::default();
        let t = Duration::from_millis;
        assert_eq!(r.sample(t(0), Some(60.), false), (0., true));
        assert_eq!(r.sample(t(1000), Some(60.), false), (52.5, true));
        assert_eq!(r.sample(t(1000), Some(0.), false), (52.5, true));
        assert_eq!(r.sample(t(1250), Some(0.), false), (60., false));
        assert_eq!(r.sample(t(8000), Some(0.), false), (60., false));
        assert_eq!(r.sample(t(8000), Some(-40.), false), (60., true));
        assert_eq!(r.sample(t(9000), Some(-40.), false), (25., true));
    }
    #[test]
    fn missing_reduced_and_new_workload_stop_without_replaying_old_time() {
        let mut r = Rotation::default();
        let t = Duration::from_secs;
        r.sample(t(0), Some(60.), false);
        let angle = r.sample(t(1), Some(60.), false).0;
        assert_eq!(r.sample(t(2), None, false), (angle, false));
        assert_eq!(r.sample(t(50), Some(60.), true), (angle, false));
        r.clear_metric();
        assert_eq!(r.sample(t(100), Some(40.), false), (angle, true));
    }
}
