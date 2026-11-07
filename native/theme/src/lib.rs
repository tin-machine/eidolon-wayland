//! 制限付きテーマのcompileと時刻評価。GL・SDK・プロセス操作を所有しない。
pub mod loader;
pub mod manifest;
pub mod package;
pub mod player;
pub mod raster;
mod rotation;
pub mod style;
pub mod timeline;

pub fn cyan_orbit() -> raster::Bundle {
    raster::Bundle::new([
        (
            "theme.toml".into(),
            include_bytes!("../assets/cyan-orbit/theme.toml").to_vec(),
        ),
        (
            "style.css".into(),
            include_bytes!("../assets/cyan-orbit/style.css").to_vec(),
        ),
        (
            "assets/outer.svg".into(),
            include_bytes!("../assets/cyan-orbit/outer.svg").to_vec(),
        ),
        (
            "assets/middle.svg".into(),
            include_bytes!("../assets/cyan-orbit/middle.svg").to_vec(),
        ),
        (
            "assets/inner.svg".into(),
            include_bytes!("../assets/cyan-orbit/inner.svg").to_vec(),
        ),
    ])
    .expect("builtin theme has validated paths and size")
}
