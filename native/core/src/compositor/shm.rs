//! SHM共有mappingのraw readをこのmoduleへ隔離する。
use eidolon_model::pixels::{OwnedRgba, PixelBudget, PixelError, byte_len};
use smithay::{
    reexports::wayland_server::protocol::{wl_buffer::WlBuffer, wl_shm::Format},
    wayland::shm::{BufferAccessError, BufferData, with_buffer_contents},
};
use std::sync::Arc;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CopyError {
    InvalidBuffer,
    UnsupportedFormat,
    ResourceLimit,
    BadMap,
}

struct Layout {
    offset: usize,
    stride: usize,
    row_bytes: usize,
    width: u32,
    height: u32,
    opaque: bool,
}

pub(super) fn validate_layout(data: BufferData, pool_len: usize) -> Result<(), CopyError> {
    layout(data, pool_len).map(|_| ())
}

fn layout(data: BufferData, pool_len: usize) -> Result<Layout, CopyError> {
    let opaque = match data.format {
        Format::Argb8888 => false,
        Format::Xrgb8888 => true,
        _ => return Err(CopyError::UnsupportedFormat),
    };
    let width = u32::try_from(data.width).map_err(|_| CopyError::InvalidBuffer)?;
    let height = u32::try_from(data.height).map_err(|_| CopyError::InvalidBuffer)?;
    byte_len(width, height).map_err(|_| CopyError::InvalidBuffer)?;
    let offset = usize::try_from(data.offset).map_err(|_| CopyError::InvalidBuffer)?;
    let stride = usize::try_from(data.stride).map_err(|_| CopyError::InvalidBuffer)?;
    let row_bytes = (width as usize)
        .checked_mul(4)
        .ok_or(CopyError::InvalidBuffer)?;
    let end = (height as usize - 1)
        .checked_mul(stride)
        .and_then(|n| n.checked_add(row_bytes))
        .and_then(|n| n.checked_add(offset))
        .ok_or(CopyError::InvalidBuffer)?;
    if stride < row_bytes || end > pool_len || end > isize::MAX as usize {
        return Err(CopyError::InvalidBuffer);
    }
    Ok(Layout {
        offset,
        stride,
        row_bytes,
        width,
        height,
        opaque,
    })
}

/// SIGBUS保護callback内でcopyし、所有済みpixelだけを返す。releaseはcallerの責務。
pub fn copy_buffer(buffer: &WlBuffer, budget: &PixelBudget) -> Result<Arc<OwnedRgba>, CopyError> {
    with_buffer_contents(buffer, |ptr, len, data| {
        let layout = layout(data, len)?;
        // SAFETY: pointerの寿命とSIGBUS保護はwith_buffer_contentsのcallback内。
        // layoutはmapping範囲と全offset算術を検査済み。
        unsafe { copy_rows(ptr, layout, budget) }.map(Arc::new)
    })
    .map_err(|error| match error {
        BufferAccessError::BadMap => CopyError::BadMap,
        _ => CopyError::InvalidBuffer,
    })?
}

/// # Safety
/// ptrはlayoutが検証したmappingを指し、呼出中はSIGBUS保護が必要。
unsafe fn copy_rows(
    ptr: *const u8,
    layout: Layout,
    budget: &PixelBudget,
) -> Result<OwnedRgba, CopyError> {
    let mut owned = budget
        .allocate(layout.width, layout.height)
        .map_err(|error| match error {
            PixelError::ResourceLimit => CopyError::ResourceLimit,
            PixelError::InvalidDimensions => CopyError::InvalidBuffer,
        })?;
    for (row, destination) in owned
        .pixels_mut()
        .chunks_exact_mut(layout.row_bytes)
        .enumerate()
    {
        // SAFETY: 検査済み各行だけを別allocationへcopy。共有mappingのsliceは作らない。
        unsafe {
            std::ptr::copy_nonoverlapping(
                ptr.add(layout.offset + row * layout.stride),
                destination.as_mut_ptr(),
                layout.row_bytes,
            );
        }
    }
    // この時点から共有pointerに触れず、所有済みbyteだけを変換する。
    for pixel in owned.pixels_mut().chunks_exact_mut(4) {
        pixel.swap(0, 2);
        if layout.opaque {
            pixel[3] = 255;
        }
    }
    Ok(owned)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn metadata() -> BufferData {
        BufferData {
            offset: 4,
            width: 2,
            height: 2,
            stride: 12,
            format: Format::Argb8888,
        }
    }

    #[test]
    fn copy_respects_offset_padding_row_order_and_premultiplied_alpha() {
        let source = [
            99, 99, 99, 99, 10, 20, 30, 128, 0, 0, 0, 0, 99, 99, 99, 99, 1, 2, 3, 255, 40, 50, 60,
            128,
        ];
        let budget = PixelBudget::default();
        let checked = layout(metadata(), source.len()).unwrap();
        // SAFETY: fixtureは有効な通常のallocationで、テスト中に変更しない。
        let image = unsafe { copy_rows(source.as_ptr(), checked, &budget) }.unwrap();
        assert_eq!(
            image.pixels(),
            &[30, 20, 10, 128, 0, 0, 0, 0, 3, 2, 1, 255, 60, 50, 40, 128]
        );
        let mut data = metadata();
        data.format = Format::Xrgb8888;
        // SAFETY: 上と同じfixture、検査済み範囲。
        let image = unsafe {
            copy_rows(
                source.as_ptr(),
                layout(data, source.len()).unwrap(),
                &budget,
            )
        }
        .unwrap();
        assert!(image.pixels().chunks_exact(4).all(|pixel| pixel[3] == 255));
        assert_eq!(image.pixels()[0..3], [30, 20, 10]);
    }

    #[test]
    fn malformed_metadata_is_rejected_before_any_read_or_allocation() {
        let mut cases = vec![];
        for (width, height, offset, stride) in [
            (-1, 2, 0, 8),
            (0, 2, 0, 8),
            (2, -1, 0, 8),
            (2, 0, 0, 8),
            (2049, 2, 0, 8196),
            (2, 2049, 0, 8),
            (2, 2, -1, 8),
            (2, 2, 0, -1),
            (2, 2, 0, 7),
            (2, 2, i32::MAX, i32::MAX),
        ] {
            let mut data = metadata();
            (data.width, data.height, data.offset, data.stride) = (width, height, offset, stride);
            cases.push(data);
        }
        cases.push(metadata()); // 最終行の1 byte不足も拒否する。
        for data in cases {
            assert!(matches!(layout(data, 23), Err(CopyError::InvalidBuffer)));
        }
        let mut unsupported = metadata();
        unsupported.format = Format::Rgb565;
        assert!(matches!(
            layout(unsupported, 24),
            Err(CopyError::UnsupportedFormat)
        ));
        assert!(layout(metadata(), 24).is_ok());
    }
}
