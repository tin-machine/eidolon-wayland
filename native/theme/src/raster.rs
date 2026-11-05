//! immutableなimport結果をcompileする。filesystem/networkをSVG resolverに渡さない。
use crate::{
    manifest::{Error, Kind, Manifest, Part, Slot, relative_file},
    style::Stylesheet,
};
use eidolon_model::pixels::{OwnedRgba, PixelBudget};
use resvg::{tiny_skia, usvg};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
};

const MAX_SOURCE: usize = 8 * 1024 * 1024;
pub const MAX_RASTER_BYTES: usize = 32 * 1024 * 1024;

/// 同じtheme ownerのreloadも同じquotaを使う。旧画像の最後の参照が消えるまで返却しない。
#[derive(Clone, Debug, Default)]
pub struct RasterBudget(Arc<AtomicUsize>);
impl RasterBudget {
    pub fn used_bytes(&self) -> usize {
        self.0.load(Ordering::Acquire)
    }
    fn reserve(&self, bytes: usize) -> Result<Reservation, Error> {
        self.0
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |used| {
                used.checked_add(bytes).filter(|v| *v <= MAX_RASTER_BYTES)
            })
            .map_err(|_| Error("live theme raster budget exceeded".into()))?;
        Ok(Reservation {
            budget: self.clone(),
            bytes,
        })
    }
}
#[derive(Debug)]
struct Reservation {
    budget: RasterBudget,
    bytes: usize,
}
impl Drop for Reservation {
    fn drop(&mut self) {
        self.budget.0.fetch_sub(self.bytes, Ordering::AcqRel);
    }
}
#[derive(Debug)]
pub struct RasterPixels {
    // この順にdropし、allocation解放後にtheme quotaを返す。
    image: OwnedRgba,
    _reservation: Reservation,
}
impl RasterPixels {
    pub fn image(&self) -> &OwnedRgba {
        &self.image
    }
    pub fn pixels(&self) -> &[u8] {
        self.image.pixels()
    }
}

pub struct Bundle {
    files: BTreeMap<String, Vec<u8>>,
}
impl Bundle {
    pub fn new(entries: impl IntoIterator<Item = (String, Vec<u8>)>) -> Result<Self, Error> {
        let mut files = BTreeMap::new();
        let mut bytes = 0usize;
        for (path, value) in entries {
            if !relative_file(&path) || files.contains_key(&path) || files.len() >= 34 {
                return Err(Error("invalid/duplicate bundle path or file limit".into()));
            }
            bytes = bytes
                .checked_add(value.len())
                .filter(|b| *b <= MAX_SOURCE)
                .ok_or_else(|| Error("bundle byte limit".into()))?;
            files.insert(path, value);
        }
        Ok(Self { files })
    }
    fn text(&self, path: &str) -> Result<&str, Error> {
        let bytes = self
            .files
            .get(path)
            .ok_or_else(|| Error(format!("missing {path}")))?;
        std::str::from_utf8(bytes).map_err(|_| Error(format!("non-UTF8 {path}")))
    }
}

#[derive(Clone, Debug)]
pub struct Raster {
    /// 角度・opacity・tintは含めない。GPU側はさらにcontext generationで区別する。
    pub key: ([u8; 32], [u32; 2]),
    pub pixels: Arc<RasterPixels>,
}
#[derive(Clone, Debug)]
pub struct CompiledPart {
    pub definition: Part,
    pub raster: Option<Raster>,
}
pub struct CompiledTheme {
    pub manifest: Manifest,
    pub style: Stylesheet,
    pub parts: BTreeMap<String, CompiledPart>,
    pub rasterizations: usize,
    pub raster_bytes: usize,
}

fn tree(source: &str) -> Result<usvg::Tree, Error> {
    if source.len() > 512 * 1024 {
        return Err(Error("SVG byte limit".into()));
    }
    let doc = roxmltree::Document::parse_with_options(
        source,
        roxmltree::ParsingOptions {
            allow_dtd: false,
            nodes_limit: 4096,
            entity_resolver: None,
        },
    )
    .map_err(|e| Error(format!("SVG XML: {e}")))?;
    let root = doc.root_element();
    if root.tag_name().name() != "svg"
        || root.tag_name().namespace() != Some("http://www.w3.org/2000/svg")
    {
        return Err(Error("SVG root/namespace".into()));
    }
    for node in doc.descendants() {
        if node.is_pi() {
            return Err(Error("SVG processing instruction".into()));
        }
        if node.is_text() && !node.text().unwrap_or_default().trim().is_empty() {
            return Err(Error("SVG text is unsupported".into()));
        }
        if !node.is_element() {
            continue;
        }
        if node.ancestors().count() > 32
            || node.tag_name().namespace() != root.tag_name().namespace()
            || ![
                "svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon",
            ]
            .contains(&node.tag_name().name())
        {
            return Err(Error("SVG element/depth outside v1 subset".into()));
        }
        for a in node.attributes() {
            if a.namespace().is_some()
                || ![
                    "id",
                    "viewBox",
                    "width",
                    "height",
                    "preserveAspectRatio",
                    "version",
                    "x",
                    "y",
                    "cx",
                    "cy",
                    "r",
                    "rx",
                    "ry",
                    "x1",
                    "y1",
                    "x2",
                    "y2",
                    "d",
                    "points",
                    "transform",
                    "fill",
                    "fill-rule",
                    "fill-opacity",
                    "stroke",
                    "stroke-width",
                    "stroke-opacity",
                    "stroke-linecap",
                    "stroke-linejoin",
                    "stroke-miterlimit",
                    "stroke-dasharray",
                    "stroke-dashoffset",
                ]
                .contains(&a.name())
                || a.value().len() > 128 * 1024
                || a.value().contains(['\\', '&'])
                || a.value().to_ascii_lowercase().contains("url")
            {
                return Err(Error(format!("SVG unsupported attribute {}", a.name())));
            }
        }
    }
    let options = usvg::Options {
        image_href_resolver: usvg::ImageHrefResolver {
            resolve_data: Box::new(|_, _, _| None),
            resolve_string: Box::new(|_, _| None),
        },
        ..Default::default()
    };
    usvg::Tree::from_str(source, &options).map_err(|e| Error(format!("SVG: {e}")))
}

/// slot寸法はhostが確定したcanvas contractから渡す。themeのlayout式は評価しない。
pub fn compile(
    bundle: &Bundle,
    slots: &BTreeMap<Slot, [u32; 2]>,
    budget: &PixelBudget,
    quota: &RasterBudget,
) -> Result<CompiledTheme, Error> {
    let manifest = Manifest::parse(bundle.text("theme.toml")?)?;
    let style = Stylesheet::parse(bundle.text(&manifest.style)?, &manifest)?;
    let mut cache = BTreeMap::<([u8; 32], [u32; 2]), Arc<RasterPixels>>::new();
    let mut parts = BTreeMap::new();
    let mut raster_bytes = 0;
    for (id, definition) in &manifest.parts {
        let raster = if definition.kind == Kind::Meter {
            None
        } else {
            let size = if let Some(size) = definition.size {
                size.map(|v| v.ceil() as u32)
            } else {
                *slots
                    .get(&definition.slot)
                    .ok_or_else(|| Error("host slot size missing".into()))?
            };
            if size.iter().any(|v| *v == 0 || *v > 2048) {
                return Err(Error("raster dimension limit".into()));
            }
            let src = bundle.text(definition.src.as_ref().unwrap())?;
            let key = (Sha256::digest(src.as_bytes()).into(), size);
            let pixels = if let Some(image) = cache.get(&key) {
                image.clone()
            } else {
                let bytes = size[0] as usize * size[1] as usize * 4;
                raster_bytes += bytes;
                if raster_bytes > MAX_RASTER_BYTES {
                    return Err(Error("theme raster byte limit".into()));
                }
                let svg = tree(src)?;
                let mut reservation = quota.reserve(bytes)?;
                let mut image = budget
                    .allocate(size[0], size[1])
                    .map_err(|_| Error("shared pixel budget exceeded".into()))?;
                let extra = image.allocated_bytes() - bytes;
                if extra > 0 {
                    let mut additional = quota.reserve(extra)?;
                    reservation.bytes += additional.bytes;
                    additional.bytes = 0;
                    raster_bytes += extra;
                }
                // 共通budgetへ計上したallocationへ直接描く。Pixmapをもう一つ確保してコピーしない。
                let mut pixmap =
                    tiny_skia::PixmapMut::from_bytes(image.pixels_mut(), size[0], size[1])
                        .ok_or_else(|| Error("pixmap dimensions".into()))?;
                resvg::render(
                    &svg,
                    tiny_skia::Transform::from_scale(
                        size[0] as f32 / svg.size().width(),
                        size[1] as f32 / svg.size().height(),
                    ),
                    &mut pixmap,
                );
                let image = Arc::new(RasterPixels {
                    image,
                    _reservation: reservation,
                });
                cache.insert(key, image.clone());
                image
            };
            Some(Raster { key, pixels })
        };
        parts.insert(
            id.clone(),
            CompiledPart {
                definition: definition.clone(),
                raster,
            },
        );
    }
    Ok(CompiledTheme {
        manifest,
        style,
        parts,
        rasterizations: cache.len(),
        raster_bytes,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn bundle(svg: &str) -> Bundle {
        Bundle::new([
            ("theme.toml".into(), b"format_version=1\nid='test'\nname='Test'\nstyle='style.css'\n[parts.a]\nkind='svg-mask'\nslot='window-backdrop'\nsrc='a.svg'\nsize=[8.0,8.0]\n[parts.b]\nkind='svg-mask'\nslot='window-backdrop'\nsrc='a.svg'\nsize=[8.0,8.0]".to_vec()),
            ("style.css".into(), b"#a {opacity:1;} #b {opacity:0.5;}".to_vec()),
            ("a.svg".into(), svg.as_bytes().to_vec()),
        ]).unwrap()
    }
    const SVG: &str = r##"<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect x="2" y="2" width="4" height="4" fill="#ff0000" fill-opacity="0.5"/></svg>"##;
    #[test]
    fn reload_overlap_is_bounded_and_failed_candidate_returns_all_charges() {
        let budget = PixelBudget::default();
        let quota = RasterBudget::default();
        let a = compile(&crate::cyan_orbit(), &BTreeMap::new(), &budget, &quota).unwrap();
        let b = compile(&crate::cyan_orbit(), &BTreeMap::new(), &budget, &quota).unwrap();
        let used = quota.used_bytes();
        assert_eq!(used, a.raster_bytes + b.raster_bytes);
        assert!(compile(&crate::cyan_orbit(), &BTreeMap::new(), &budget, &quota).is_err());
        assert_eq!(quota.used_bytes(), used);
        assert_eq!(budget.used_bytes(), used);
        // GLへ渡す参照が残っている間、theme本体を落としても予算を返さない。
        let held = a.parts["ring-outer"].raster.clone().unwrap();
        drop(a);
        drop(b);
        assert_eq!(quota.used_bytes(), held.pixels.image().allocated_bytes());
        drop(held);
        assert_eq!(quota.used_bytes(), 0);
        assert_eq!(budget.used_bytes(), 0);
    }
    #[test]
    fn real_resvg_is_premultiplied_shared_and_budget_released() {
        let budget = PixelBudget::default();
        let theme = compile(
            &bundle(SVG),
            &BTreeMap::new(),
            &budget,
            &RasterBudget::default(),
        )
        .unwrap();
        assert_eq!(theme.rasterizations, 1);
        assert_eq!(theme.raster_bytes, 8 * 8 * 4);
        let a = &theme.parts["a"].raster.as_ref().unwrap().pixels;
        let b = &theme.parts["b"].raster.as_ref().unwrap().pixels;
        assert!(Arc::ptr_eq(a, b));
        assert_eq!(&a.pixels()[0..4], &[0, 0, 0, 0]);
        assert_eq!(
            &a.pixels()[4 * (3 * 8 + 3)..4 * (3 * 8 + 3) + 4],
            &[128, 0, 0, 128]
        );
        assert_eq!(budget.used_bytes(), theme.raster_bytes);
        drop(theme);
        assert_eq!(budget.used_bytes(), 0);
    }
    #[test]
    fn reject_external_assets_scripts_animation_filters_and_group_buffers() {
        let budget = PixelBudget::default();
        for node in [
            "<image href='/etc/passwd'/>",
            "<script/>",
            "<animate/>",
            "<filter/>",
            "<style/>",
            "<text>x</text>",
            "<g opacity='0.5'><circle r='4'/></g>",
        ] {
            let svg = format!(
                "<svg xmlns='http://www.w3.org/2000/svg' width='8' height='8'>{node}</svg>"
            );
            assert!(
                compile(
                    &bundle(&svg),
                    &BTreeMap::new(),
                    &budget,
                    &RasterBudget::default()
                )
                .is_err(),
                "{node}"
            );
            assert_eq!(budget.used_bytes(), 0);
        }
        assert!(
            tree("<!DOCTYPE svg [<!ENTITY x 'x'>]><svg xmlns='http://www.w3.org/2000/svg'/>")
                .is_err()
        );
        assert!(Bundle::new([("../theme.toml".into(), vec![])]).is_err());
        assert!(Bundle::new([("a.svg".into(), vec![]), ("a.svg".into(), vec![])]).is_err());
    }
}
