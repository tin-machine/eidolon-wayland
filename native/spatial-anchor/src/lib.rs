//! Native OpenXR bridge for the session owned by the Spatial SDK.

#[cfg(target_os = "android")]
mod capabilities;
#[cfg(target_os = "android")]
mod xr;

#[cfg(target_os = "android")]
use serde_json::Value;
#[cfg(target_os = "android")]
use std::ffi::c_void;

pub const REQUIRED_EXTENSIONS: [&str; 6] = [
    "XR_EXT_future",
    "XR_EXT_spatial_entity",
    "XR_EXT_spatial_anchor",
    "XR_EXT_spatial_persistence",
    "XR_EXT_spatial_persistence_operations",
    "XR_KHR_convert_timespec_time",
];

/// Enumerate before the SDK creates its instance.
///
/// # Safety
/// The pointers must identify the caller's Java VM and a live Android application Context.
#[cfg(target_os = "android")]
pub unsafe fn startup_extensions(
    vm: *mut c_void,
    context: *mut c_void,
) -> Result<Vec<String>, String> {
    capabilities::startup_extensions(vm, context)
}

/// # Safety
/// Both values must come from the same live SDK instance, on its owning Activity thread.
#[cfg(target_os = "android")]
pub unsafe fn inspect(instance: u64, proc_addr: usize) -> Value {
    capabilities::inspect(instance, proc_addr)
}

/// # Safety
/// The handles and dispatch pointer must come from the same live SDK scene.
/// Drive and cancel the operation on the Activity thread before the session is destroyed.
#[cfg(target_os = "android")]
pub unsafe fn step(
    instance: u64,
    session: u64,
    proc_addr: usize,
    owner: &str,
    command: &str,
    uuid: &str,
) -> Value {
    xr::persistence::step(
        instance,
        session,
        proc_addr,
        owner,
        command,
        uuid,
        xr::persistence::PlacementSpace::Probe,
    )
}

/// Run one SDK-owned panel operation in the same LOCAL_FLOOR space as the app.
///
/// # Safety
/// The SDK scene must own the handles. Run on its Activity thread and cancel before shutdown.
#[cfg(target_os = "android")]
pub unsafe fn panel_step(
    scene_handles: (u64, u64, usize),
    owner: &str,
    command: &str,
    uuid: &str,
    pose: Option<[f32; 7]>,
) -> Value {
    use openxr_sys::{Posef, Quaternionf, Vector3f};
    let (instance, session, proc_addr) = scene_handles;
    let pose = pose.map(|[x, y, z, qx, qy, qz, qw]| Posef {
        // Reflect across Z to convert the SDK's +Z-forward coordinates.
        position: Vector3f { x, y, z: -z },
        orientation: Quaternionf {
            x: -qx,
            y: -qy,
            z: qz,
            w: qw,
        },
    });
    xr::persistence::step(
        instance,
        session,
        proc_addr,
        owner,
        command,
        uuid,
        xr::persistence::PlacementSpace::Panel(pose),
    )
}

/// Request the complete set only when the loader advertises every dependency.
pub fn required_if_advertised(advertised: &[String]) -> &[&'static str] {
    if REQUIRED_EXTENSIONS
        .iter()
        .all(|required| advertised.iter().any(|name| name == required))
    {
        &REQUIRED_EXTENSIONS
    } else {
        &[]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_partial_runtime_keeps_default_sdk_extensions() {
        let mut advertised = REQUIRED_EXTENSIONS.map(str::to_owned).to_vec();
        advertised.push("XR_KHR_unrelated".into());
        assert_eq!(required_if_advertised(&advertised), REQUIRED_EXTENSIONS);
        advertised.retain(|name| name != "XR_EXT_spatial_persistence_operations");
        assert!(required_if_advertised(&advertised).is_empty());
        assert!(required_if_advertised(&[]).is_empty());
    }
}
