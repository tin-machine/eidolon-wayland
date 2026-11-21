//! thread間で共有するpacked RGBA8。共有mappingへの参照は保持しない。
use crate::limits;
use std::{
    fmt,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PixelError {
    InvalidDimensions,
    ResourceLimit,
}

struct Counter {
    used: AtomicUsize,
    limit: usize,
}

/// A common allocation limit with optional accounting for one owner.
#[derive(Clone)]
pub struct PixelBudget {
    total: Arc<Counter>,
    owner_used: Option<Arc<AtomicUsize>>,
}

impl Default for PixelBudget {
    fn default() -> Self {
        Self::with_limit(limits::PIXEL_BYTES)
    }
}

impl PixelBudget {
    fn with_limit(limit: usize) -> Self {
        Self {
            total: Arc::new(Counter {
                used: AtomicUsize::new(0),
                limit,
            }),
            owner_used: None,
        }
    }

    /// Track this owner's images through their last reference while charging
    /// the same global limit as the theme and other owners.
    pub fn for_owner(&self) -> Self {
        Self {
            total: self.total.clone(),
            owner_used: Some(Arc::new(AtomicUsize::new(0))),
        }
    }

    pub fn used_bytes(&self) -> usize {
        self.owner_used
            .as_deref()
            .unwrap_or(&self.total.used)
            .load(Ordering::Acquire)
    }

    fn charge(&self, bytes: usize) -> Result<(), PixelError> {
        self.total
            .used
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |used| {
                used.checked_add(bytes)
                    .filter(|next| *next <= self.total.limit)
            })
            .map_err(|_| PixelError::ResourceLimit)?;
        if let Some(used) = self.owner_used.as_deref() {
            used.fetch_add(bytes, Ordering::AcqRel);
        }
        Ok(())
    }

    fn release(&self, bytes: usize) {
        if let Some(used) = self.owner_used.as_deref() {
            used.fetch_sub(bytes, Ordering::AcqRel);
        }
        self.total.used.fetch_sub(bytes, Ordering::AcqRel);
    }

    pub fn allocate(&self, width_px: u32, height_px: u32) -> Result<OwnedRgba, PixelError> {
        let len = byte_len(width_px, height_px)?;
        self.charge(len)?;
        let mut owned = OwnedRgba {
            width_px,
            height_px,
            pixels: Vec::new(),
            charged: len,
            budget: self.clone(),
        };
        owned
            .pixels
            .try_reserve_exact(len)
            .map_err(|_| PixelError::ResourceLimit)?;
        // allocatorが余分に確保した場合もallocation単位で計上する。
        let capacity = owned.pixels.capacity();
        self.charge(capacity - len)?;
        owned.charged = capacity;
        owned.pixels.resize(len, 0);
        Ok(owned)
    }
}

pub fn byte_len(width_px: u32, height_px: u32) -> Result<usize, PixelError> {
    if width_px == 0
        || height_px == 0
        || width_px > limits::BUFFER_DIMENSION
        || height_px > limits::BUFFER_DIMENSION
    {
        return Err(PixelError::InvalidDimensions);
    }
    (width_px as usize)
        .checked_mul(height_px as usize)
        .and_then(|n| n.checked_mul(4))
        .filter(|n| *n <= limits::BUFFER_BYTES)
        .ok_or(PixelError::InvalidDimensions)
}

/// 上から下の行順、RGBA8、premultiplied alpha。Arcの最後の参照で予算を解放する。
/// pixelそのものをDebug/Serializeへ含めない。
pub struct OwnedRgba {
    width_px: u32,
    height_px: u32,
    pixels: Vec<u8>,
    charged: usize,
    budget: PixelBudget,
}

impl OwnedRgba {
    pub fn allocated_bytes(&self) -> usize {
        self.charged
    }
    pub fn width_px(&self) -> u32 {
        self.width_px
    }
    pub fn height_px(&self) -> u32 {
        self.height_px
    }
    pub fn pixels(&self) -> &[u8] {
        &self.pixels
    }
    /// publish前の排他的な所有者だけが書き込む。
    pub fn pixels_mut(&mut self) -> &mut [u8] {
        &mut self.pixels
    }
}

impl fmt::Debug for OwnedRgba {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("OwnedRgba")
            .field("width_px", &self.width_px)
            .field("height_px", &self.height_px)
            .field("bytes", &self.pixels.len())
            .finish()
    }
}

impl Drop for OwnedRgba {
    fn drop(&mut self) {
        // GL等のconsumer threadが最後の参照を落とす場合も、実allocationを
        // 解放してからprotocol ownerの次の割当に予算を返す。
        drop(std::mem::take(&mut self.pixels));
        self.budget.release(self.charged);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn session_retires_with_theme_pixels_still_charged_to_the_shared_limit() {
        let shared = PixelBudget::with_limit(32);
        let theme = shared.allocate(2, 2).unwrap();
        let session = shared.for_owner();
        let image = Arc::new(session.allocate(2, 2).unwrap());
        let renderer = image.clone();

        assert_eq!(shared.used_bytes(), 32);
        assert_eq!(session.used_bytes(), 16);
        assert!(matches!(
            session.allocate(1, 1),
            Err(PixelError::ResourceLimit)
        ));
        drop(image);
        assert_eq!(session.used_bytes(), 16);
        drop(renderer);
        assert_eq!(session.used_bytes(), 0);
        assert_eq!(shared.used_bytes(), 16);
        drop(theme);
        assert_eq!(shared.used_bytes(), 0);
    }

    #[test]
    fn snapshots_share_one_charge_until_the_last_consumer_drops() {
        let budget = PixelBudget::with_limit(32);
        let image = Arc::new(budget.allocate(2, 2).unwrap());
        let render = image.clone();
        let cache = image.clone();
        let pending = budget.allocate(2, 2).unwrap();
        assert_eq!(budget.used_bytes(), 32);
        assert!(matches!(
            budget.allocate(1, 1),
            Err(PixelError::ResourceLimit)
        ));
        drop(image);
        drop(render);
        assert_eq!(budget.used_bytes(), 32);
        drop(cache);
        assert_eq!(budget.used_bytes(), 16);
        let next = budget.allocate(2, 2).unwrap();
        drop(pending);
        drop(next);
        assert_eq!(budget.used_bytes(), 0);
    }

    #[test]
    fn invalid_sizes_and_failed_reservations_leave_no_charge() {
        let budget = PixelBudget::with_limit(15);
        for (width, height) in [(0, 1), (1, 0), (2049, 1), (u32::MAX, u32::MAX)] {
            assert!(matches!(
                budget.allocate(width, height),
                Err(PixelError::InvalidDimensions)
            ));
        }
        assert!(matches!(
            budget.allocate(2, 2),
            Err(PixelError::ResourceLimit)
        ));
        assert_eq!(budget.used_bytes(), 0);
        let image = budget.allocate(1, 1).unwrap();
        assert_eq!(image.pixels(), &[0; 4]);
        assert!(!format!("{image:?}").contains("pixels"));
    }
}
