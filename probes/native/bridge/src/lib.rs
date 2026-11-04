// 製品coreとは独立した事前検証。JNIとrendererはAndroidだけで構築する。
#[cfg(target_os = "android")]
mod android;
#[cfg(feature = "compositor")]
mod compositor;
#[cfg(feature = "ssh")]
mod transport;
