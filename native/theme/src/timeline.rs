//! ownerがInstant基準のDurationを渡す。frame数やappのContentSeqを時計にしない。
use std::time::Duration;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ClockError {
    TimeReversed,
    Overflow,
    InvalidDuration,
}

#[derive(Clone, Debug)]
pub struct Timeline {
    last: Duration,
    elapsed: Duration,
    running: bool,
}

impl Timeline {
    pub fn new(now: Duration, running: bool) -> Self {
        Self {
            last: now,
            elapsed: Duration::ZERO,
            running,
        }
    }

    pub fn elapsed(&self, now: Duration) -> Result<Duration, ClockError> {
        let delta = now.checked_sub(self.last).ok_or(ClockError::TimeReversed)?;
        self.elapsed
            .checked_add(if self.running { delta } else { Duration::ZERO })
            .ok_or(ClockError::Overflow)
    }

    /// visibility変更の時刻まで旧状態で進めてからpause/resumeする。同値通知でresetしない。
    pub fn set_running(&mut self, now: Duration, running: bool) -> Result<(), ClockError> {
        let elapsed = self.elapsed(now)?;
        self.elapsed = elapsed;
        self.last = now;
        self.running = running;
        Ok(())
    }
}

/// 周期内の位置。整数nsで剰余を取ってからfloatへ変換し、長時間稼働の桁落ちを避ける。
pub fn cycle_fraction(elapsed: Duration, duration: Duration) -> Result<f64, ClockError> {
    if duration.is_zero() {
        return Err(ClockError::InvalidDuration);
    }
    Ok((elapsed.as_nanos() % duration.as_nanos()) as f64 / duration.as_nanos() as f64)
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Easing {
    #[default]
    Linear,
    Ease,
    EaseIn,
    EaseOut,
    EaseInOut,
}

impl Easing {
    pub fn sample(self, progress: f64) -> Option<f64> {
        if !progress.is_finite() {
            return None;
        }
        let x = progress.clamp(0., 1.);
        if self == Self::Linear || x == 0. || x == 1. {
            return Some(x);
        }
        let [x1, y1, x2, y2] = match self {
            Self::Ease => [0.25, 0.1, 0.25, 1.],
            Self::EaseIn => [0.42, 0., 1., 1.],
            Self::EaseOut => [0., 0., 0.58, 1.],
            Self::EaseInOut => [0.42, 0., 0.58, 1.],
            Self::Linear => unreachable!(),
        };
        let bezier =
            |t: f64, a, b| 3. * (1. - t).powi(2) * t * a + 3. * (1. - t) * t * t * b + t.powi(3);
        let (mut lo, mut hi) = (0., 1.);
        for _ in 0..40 {
            let t = (lo + hi) / 2.;
            if bezier(t, x1, x2) < x {
                lo = t;
            } else {
                hi = t;
            }
        }
        Some(bezier((lo + hi) / 2., y1, y2))
    }
}

/// CSS transitionの目標更新。現在の表示値を始点にし、同じ目標の再通知では延長しない。
#[derive(Clone, Debug)]
pub struct ScalarTransition {
    from: f64,
    target: f64,
    start: Duration,
    duration: Duration,
    easing: Easing,
}

impl ScalarTransition {
    pub fn new(value: f64, now: Duration) -> Option<Self> {
        value.is_finite().then_some(Self {
            from: value,
            target: value,
            start: now,
            duration: Duration::ZERO,
            easing: Easing::Linear,
        })
    }
    pub fn sample(&self, now: Duration) -> Option<f64> {
        let elapsed = now.checked_sub(self.start)?;
        if self.duration.is_zero() || elapsed >= self.duration {
            return Some(self.target);
        }
        let t = self
            .easing
            .sample(elapsed.as_secs_f64() / self.duration.as_secs_f64())?;
        let value = self.from * (1. - t) + self.target * t;
        value.is_finite().then_some(value)
    }
    pub fn retarget(
        &mut self,
        now: Duration,
        target: f64,
        duration: Duration,
        easing: Easing,
    ) -> Option<bool> {
        if !target.is_finite() {
            return None;
        }
        let current = self.sample(now)?;
        if self.target == target {
            return Some(false);
        }
        *self = Self {
            from: current,
            target,
            start: now,
            duration,
            easing,
        };
        Some(true)
    }
    pub fn active(&self, now: Duration) -> bool {
        self.from != self.target
            && now
                .checked_sub(self.start)
                .is_some_and(|d| d < self.duration)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn s(v: u64) -> Duration {
        Duration::from_secs(v)
    }
    #[test]
    fn three_independent_spins_keep_direction_across_frame_skips() {
        let clock = Timeline::new(s(0), true);
        let elapsed = clock.elapsed(s(3)).unwrap();
        let angles: Vec<_> = [(12, 1.), (18, -1.), (8, 1.)]
            .into_iter()
            .map(|(period, direction)| {
                cycle_fraction(elapsed, s(period)).unwrap() * 360. * direction
            })
            .collect();
        assert_eq!(angles, [90., -60., 135.]);
        assert_eq!(
            cycle_fraction(clock.elapsed(s(86403)).unwrap(), s(12)).unwrap(),
            0.25
        );
    }
    #[test]
    fn visibility_pause_and_redundant_notifications_keep_phase() {
        let mut clock = Timeline::new(s(0), true);
        clock.set_running(s(2), true).unwrap();
        clock.set_running(s(3), false).unwrap();
        assert_eq!(clock.elapsed(s(100)), Ok(s(3)));
        clock.set_running(s(100), true).unwrap();
        assert_eq!(clock.elapsed(s(102)), Ok(s(5)));
        assert_eq!(
            clock.set_running(s(99), false),
            Err(ClockError::TimeReversed)
        );
        assert_eq!(clock.elapsed(s(102)), Ok(s(5)));
    }
    #[test]
    fn retarget_starts_at_current_value_and_same_target_does_not_extend() {
        let mut v = ScalarTransition::new(0., s(0)).unwrap();
        assert_eq!(v.retarget(s(0), 1., s(10), Easing::Linear), Some(true));
        assert_eq!(v.sample(s(4)), Some(0.4));
        assert_eq!(v.retarget(s(4), 1., s(10), Easing::Linear), Some(false));
        assert_eq!(v.retarget(s(4), 0., s(2), Easing::Linear), Some(true));
        assert_eq!(v.sample(s(4)), Some(0.4));
        assert_eq!(v.sample(s(5)), Some(0.2));
        assert_eq!(v.sample(s(6)), Some(0.));
        assert!(!v.active(s(6)));
        assert_eq!(v.retarget(s(6), f64::NAN, s(1), Easing::Linear), None);
    }
    #[test]
    fn easing_solves_x_instead_of_using_parameter_as_progress() {
        assert!((Easing::EaseInOut.sample(0.25).unwrap() - 0.129161931).abs() < 1e-8);
        assert!((Easing::Ease.sample(0.5).unwrap() - 0.802403388).abs() < 1e-8);
        assert_eq!(Easing::Ease.sample(f64::NAN), None);
        assert_eq!(cycle_fraction(s(1), s(0)), Err(ClockError::InvalidDuration));
    }
}
