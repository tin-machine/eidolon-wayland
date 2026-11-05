//! popup geometryを計算する前に、上限とenumを検査する。
use super::ProtocolState;
use eidolon_model::limits;
use smithay::{
    reexports::{
        wayland_protocols::xdg::shell::server::xdg_positioner::{self, XdgPositioner},
        wayland_server::{Client, DataInit, Dispatch, DisplayHandle, Resource, WEnum},
    },
    wayland::shell::xdg::{XdgPositionerUserData, XdgShellState},
};

fn size(width: i32, height: i32) -> bool {
    width > 0
        && height > 0
        && width as u32 <= limits::BUFFER_DIMENSION
        && height as u32 <= limits::BUFFER_DIMENSION
}
fn position(x: i32, y: i32) -> bool {
    x.unsigned_abs() <= limits::POSITIONER_COORDINATE
        && y.unsigned_abs() <= limits::POSITIONER_COORDINATE
}
impl Dispatch<XdgPositioner, XdgPositionerUserData> for ProtocolState {
    fn request(
        state: &mut Self,
        client: &Client,
        object: &XdgPositioner,
        request: xdg_positioner::Request,
        data: &XdgPositionerUserData,
        dh: &DisplayHandle,
        init: &mut DataInit<'_, Self>,
    ) {
        let valid = match &request {
            xdg_positioner::Request::SetSize { width, height } => size(*width, *height),
            xdg_positioner::Request::SetAnchorRect {
                x,
                y,
                width,
                height,
            } => position(*x, *y) && size(*width, *height),
            xdg_positioner::Request::SetOffset { x, y } => position(*x, *y),
            xdg_positioner::Request::SetAnchor { anchor } => matches!(anchor, WEnum::Value(_)),
            xdg_positioner::Request::SetGravity { gravity } => matches!(gravity, WEnum::Value(_)),
            xdg_positioner::Request::SetConstraintAdjustment {
                constraint_adjustment,
            } => matches!(constraint_adjustment, WEnum::Value(_)),
            xdg_positioner::Request::SetParentSize {
                parent_width,
                parent_height,
            } => size(*parent_width, *parent_height),
            _ => true,
        };
        if !valid {
            object.post_error(
                xdg_positioner::Error::InvalidInput,
                "positioner exceeds geometry limits",
            );
            return;
        }
        <XdgShellState as Dispatch<XdgPositioner, XdgPositionerUserData, Self>>::request(
            state, client, object, request, data, dh, init,
        );
    }
}
