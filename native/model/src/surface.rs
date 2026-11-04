use crate::{
    ids::{SceneEpoch, SessionId, SurfaceGeneration, WindowId},
    limits,
    state::Status,
};
use serde::{Deserialize, Serialize};

/// SDKとnative ownerの間で必ず全fieldを照合する。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TargetKey {
    pub session: SessionId,
    pub window: WindowId,
    pub scene: SceneEpoch,
    pub generation: SurfaceGeneration,
}
impl TargetKey {
    pub fn from_raw(
        session: i64,
        window: i64,
        scene: i64,
        generation: i64,
    ) -> Result<Self, Status> {
        Ok(Self {
            session: SessionId::new(session).map_err(|_| Status::InvalidArgument)?,
            window: WindowId::new(window).map_err(|_| Status::InvalidArgument)?,
            scene: SceneEpoch::new(scene).map_err(|_| Status::InvalidArgument)?,
            generation: SurfaceGeneration::new(generation).map_err(|_| Status::InvalidArgument)?,
        })
    }
    pub fn same_window(self, other: Self) -> bool {
        self.session == other.session && self.window == other.window
    }
    pub fn newer_than(self, other: Self) -> bool {
        self.same_window(other) && (self.scene, self.generation) > (other.scene, other.generation)
    }
}

pub fn validate_dimensions(width: i32, height: i32) -> Result<(), Status> {
    if width < 32
        || height < 32
        || width as u32 > limits::BUFFER_DIMENSION
        || height as u32 > limits::BUFFER_DIMENSION
    {
        return Err(Status::InvalidArgument);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn tuple_and_dimensions_are_checked_before_native_acquisition() {
        let a = TargetKey::from_raw(1, 1, 1, 1).unwrap();
        assert!(TargetKey::from_raw(1, 1, 1, 2).unwrap().newer_than(a));
        assert!(!TargetKey::from_raw(2, 1, 1, 2).unwrap().newer_than(a));
        assert!(TargetKey::from_raw(1, 0, 1, 1).is_err());
        assert!(validate_dimensions(1024, 768).is_ok());
        for size in [(-1, 768), (0, 768), (31, 768), (2049, 768)] {
            assert!(validate_dimensions(size.0, size.1).is_err());
        }
    }
}
