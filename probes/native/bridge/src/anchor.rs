// The SDK owns the live instance. Read capabilities during its scene callback only.
use jni::{
    EnvUnowned,
    objects::{JClass, JString},
    sys::jlong,
};
use openxr_sys::{self as xr, Handle, pfn};
use serde_json::{Value, json};
use std::{ffi::CStr, ptr};

const COMMANDS: &[(&str, &CStr)] = &[
    (
        "XR_EXT_spatial_persistence",
        c"xrEnumerateSpatialPersistenceScopesEXT",
    ),
    (
        "XR_EXT_spatial_persistence_operations",
        c"xrPersistSpatialEntityAsyncEXT",
    ),
    ("XR_EXT_future", c"xrPollFutureEXT"),
    ("XR_EXT_spatial_entity", c"xrCreateSpatialContextAsyncEXT"),
    ("XR_EXT_spatial_anchor", c"xrCreateSpatialAnchorEXT"),
];

fn enumerate(get: pfn::GetInstanceProcAddr) -> Result<Vec<String>, String> {
    let mut function = None;
    let result = unsafe {
        get(
            xr::Instance::NULL,
            c"xrEnumerateInstanceExtensionProperties".as_ptr(),
            &mut function,
        )
    };
    if result != xr::Result::SUCCESS {
        return Err(format!("{result:?}"));
    }
    // The loader returns the typed entry point identified above.
    let enumerate: pfn::EnumerateInstanceExtensionProperties =
        unsafe { std::mem::transmute(function.ok_or("missing enumeration function")?) };
    let mut count = 0;
    let result = unsafe { enumerate(ptr::null(), 0, &mut count, ptr::null_mut()) };
    if result != xr::Result::SUCCESS {
        return Err(format!("{result:?}"));
    }
    if count == 0 {
        return Ok(Vec::new());
    }
    let mut rows = vec![
        xr::ExtensionProperties {
            ty: xr::ExtensionProperties::TYPE,
            next: ptr::null_mut(),
            extension_name: [0; xr::MAX_EXTENSION_NAME_SIZE],
            extension_version: 0,
        };
        count as usize
    ];
    let result = unsafe { enumerate(ptr::null(), count, &mut count, rows.as_mut_ptr()) };
    if result != xr::Result::SUCCESS || count as usize > rows.len() {
        return Err(format!("enumeration: {result:?}, count={count}"));
    }
    Ok(rows
        .iter()
        .take(count as usize)
        .map(|row| {
            let bytes: Vec<u8> = row
                .extension_name
                .iter()
                .take_while(|&&c| c != 0)
                .map(|&c| c as u8)
                .collect();
            String::from_utf8_lossy(&bytes).into_owned()
        })
        .collect())
}

fn inspect(instance: jlong, proc_addr: jlong) -> Value {
    if instance == 0 || proc_addr == 0 {
        return json!({"error":"sdk_handles_unavailable"});
    }
    // Both handles are read together from the same live SDK scene by AnchorProbeActivity.
    let get: pfn::GetInstanceProcAddr = unsafe { std::mem::transmute(proc_addr as usize) };
    let instance = xr::Instance::from_raw(instance as u64);
    let advertised = enumerate(get);
    let mut rows = serde_json::Map::new();
    for &(extension, command) in COMMANDS {
        let mut function = None;
        let result = unsafe { get(instance, command.as_ptr(), &mut function) };
        rows.insert(extension.into(), json!({
            "advertised": advertised.as_ref().ok().map(|list| list.iter().any(|name| name == extension)),
            "command": command.to_str().unwrap(),
            "lookup": format!("{result:?}"),
            "callable": result == xr::Result::SUCCESS && function.is_some()
        }));
    }
    let scopes = persistence_scopes(get, instance);
    json!({"extensions":rows,"enumeration_error":advertised.err(), "persistence_scopes":scopes})
}

fn persistence_scopes(get: pfn::GetInstanceProcAddr, instance: xr::Instance) -> Value {
    let mut get_system = None;
    let mut enumerate = None;
    let lookup = unsafe {
        get(
            instance,
            c"xrEnumerateSpatialPersistenceScopesEXT".as_ptr(),
            &mut enumerate,
        )
    };
    if lookup != xr::Result::SUCCESS || enumerate.is_none() {
        return json!({"lookup":format!("{lookup:?}")});
    }
    let lookup = unsafe { get(instance, c"xrGetSystem".as_ptr(), &mut get_system) };
    if lookup != xr::Result::SUCCESS || get_system.is_none() {
        return json!({"get_system_lookup":format!("{lookup:?}")});
    }
    let get_system: pfn::GetSystem = unsafe { std::mem::transmute(get_system.unwrap()) };
    let enumerate: pfn::EnumerateSpatialPersistenceScopesEXT =
        unsafe { std::mem::transmute(enumerate.unwrap()) };
    let info = xr::SystemGetInfo {
        ty: xr::SystemGetInfo::TYPE,
        next: ptr::null(),
        form_factor: xr::FormFactor::HEAD_MOUNTED_DISPLAY,
    };
    let mut system = xr::SystemId::NULL;
    let result = unsafe { get_system(instance, &info, &mut system) };
    if result != xr::Result::SUCCESS {
        return json!({"get_system":format!("{result:?}")});
    }
    let mut count = 0;
    let result = unsafe { enumerate(instance, system, 0, &mut count, ptr::null_mut()) };
    if result != xr::Result::SUCCESS {
        return json!({"count_result":format!("{result:?}")});
    }
    let mut scopes = vec![xr::SpatialPersistenceScopeEXT::SYSTEM_MANAGED; count as usize];
    if count > 0 {
        let result = unsafe { enumerate(instance, system, count, &mut count, scopes.as_mut_ptr()) };
        if result != xr::Result::SUCCESS || count as usize > scopes.len() {
            return json!({"enumerate_result":format!("{result:?}")});
        }
        scopes.truncate(count as usize);
    }
    json!({"local_anchors":scopes.contains(&xr::SpatialPersistenceScopeEXT::LOCAL_ANCHORS),
        "scopes":scopes.iter().map(|s|format!("{s:?}")).collect::<Vec<_>>()})
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_io_github_tinmachine_eidolon_probe_NativeProbe_anchorExtensions<'a>(
    mut env: EnvUnowned<'a>,
    _: JClass<'a>,
    instance: jlong,
    proc_addr: jlong,
) -> JString<'a> {
    env.with_env(|env| JString::from_str(env, inspect(instance, proc_addr).to_string()))
        .resolve::<jni::errors::ThrowRuntimeExAndDefault>()
}
