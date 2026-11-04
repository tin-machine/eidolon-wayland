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

/// session内のcache/pending/renderすべてで同じbudgetを共有する。
#[derive(Clone)]
pub struct PixelBudget(Arc<Counter>);

impl Default for PixelBudget {
    fn default() -> Self {
        Self::with_limit(limits::PIXEL_BYTES)
    }
}

impl PixelBudget {
    fn with_limit(limit: usize) -> Self {
        Self(Arc::new(Counter {
            used: AtomicUsize::new(0),
            limit,
        }))
    }

    pub fn used_bytes(&self) -> usize {
        self.0.used.load(Ordering::Acquire)
    }

    fn charge(&self, bytes: usize) -> Result<(), PixelError> {
        self.0
            .used
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |used| {
                used.checked_add(bytes).filter(|next| *next <= self.0.limit)
            })
            .map(|_| ())
            .map_err(|_| PixelError::ResourceLimit)
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
        self.budget.0.used.fetch_sub(self.charged, Ordering::AcqRel);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
