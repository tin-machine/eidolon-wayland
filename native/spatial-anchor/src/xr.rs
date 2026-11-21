use openxr_sys::{self as xr, Handle, pfn};
use serde_json::{Value, json};
use std::{ffi::CStr, ptr};

struct Session {
    instance: xr::Instance,
    session: xr::Session,
    get: pfn::GetInstanceProcAddr,
}

impl Session {
    fn proc(&self, name: &CStr) -> Result<pfn::VoidFunction, String> {
        let mut function = None;
        let result = unsafe { (self.get)(self.instance, name.as_ptr(), &mut function) };
        check(name.to_string_lossy().as_ref(), result)?;
        function.ok_or_else(|| format!("missing {name:?}"))
    }
}

// The symbol name and the Khronos function type come from the same identifier.
macro_rules! load {
    ($api:expr, $name:ident) => {{
        let name =
            CStr::from_bytes_with_nul(concat!("xr", stringify!($name), "\0").as_bytes()).unwrap();
        let function = $api.proc(name)?;
        unsafe { std::mem::transmute::<pfn::VoidFunction, pfn::$name>(function) }
    }};
}

fn check(op: &str, result: xr::Result) -> Result<(), String> {
    if result.into_raw() >= 0 {
        Ok(())
    } else {
        Err(format!("{op}: {result:?}"))
    }
}

pub(crate) mod hands;
pub(crate) mod persistence;
