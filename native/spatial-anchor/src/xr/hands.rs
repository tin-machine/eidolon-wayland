use super::*;
use std::cell::RefCell;

thread_local! {
    static TRACKING: RefCell<Option<Tracking>> = const { RefCell::new(None) };
}

struct Tracking {
    session: xr::Session,
    instance: xr::Instance,
    space: xr::Space,
    left: xr::HandTrackerEXT,
    right: xr::HandTrackerEXT,
    destroy_space: pfn::DestroySpace,
    destroy_tracker: pfn::DestroyHandTrackerEXT,
    locate: pfn::LocateHandJointsEXT,
    time: pfn::ConvertTimespecTimeToTimeKHR,
}

impl Tracking {
    fn new(api: Session, unobstructed: bool) -> Result<Self, String> {
        let create_space = load!(&api, CreateReferenceSpace);
        let create_tracker = load!(&api, CreateHandTrackerEXT);
        let mut tracking = Self {
            session: api.session,
            instance: api.instance,
            space: xr::Space::NULL,
            left: xr::HandTrackerEXT::NULL,
            right: xr::HandTrackerEXT::NULL,
            destroy_space: load!(&api, DestroySpace),
            destroy_tracker: load!(&api, DestroyHandTrackerEXT),
            locate: load!(&api, LocateHandJointsEXT),
            time: load!(&api, ConvertTimespecTimeToTimeKHR),
        };
        let space_info = xr::ReferenceSpaceCreateInfo {
            ty: xr::ReferenceSpaceCreateInfo::TYPE,
            next: ptr::null(),
            reference_space_type: xr::ReferenceSpaceType::LOCAL_FLOOR,
            pose_in_reference_space: xr::Posef::IDENTITY,
        };
        check("hand base space", unsafe {
            create_space(api.session, &space_info, &mut tracking.space)
        })?;
        let mut source = xr::HandTrackingDataSourceEXT::UNOBSTRUCTED;
        let source_info = xr::HandTrackingDataSourceInfoEXT {
            ty: xr::HandTrackingDataSourceInfoEXT::TYPE,
            next: ptr::null(),
            requested_data_source_count: 1,
            requested_data_sources: &mut source,
        };
        let next = if unobstructed {
            ptr::from_ref(&source_info).cast()
        } else {
            ptr::null()
        };
        for (hand, tracker) in [
            (xr::HandEXT::LEFT, &mut tracking.left),
            (xr::HandEXT::RIGHT, &mut tracking.right),
        ] {
            let info = xr::HandTrackerCreateInfoEXT {
                ty: xr::HandTrackerCreateInfoEXT::TYPE,
                next,
                hand,
                hand_joint_set: xr::HandJointSetEXT::DEFAULT,
            };
            check("create hand tracker", unsafe {
                create_tracker(api.session, &info, tracker)
            })?;
        }
        Ok(tracking)
    }

    fn sample(&self, tracker: xr::HandTrackerEXT, time: xr::Time, out: &mut [f32]) {
        let info = xr::HandJointsLocateInfoEXT {
            ty: xr::HandJointsLocateInfoEXT::TYPE,
            next: ptr::null(),
            base_space: self.space,
            time,
        };
        let mut joints = [xr::HandJointLocationEXT::default(); xr::HAND_JOINT_COUNT_EXT];
        let mut aim = xr::HandTrackingAimStateFB {
            ty: xr::HandTrackingAimStateFB::TYPE,
            next: ptr::null_mut(),
            status: xr::HandTrackingAimFlagsFB::EMPTY,
            aim_pose: xr::Posef::IDENTITY,
            pinch_strength_index: 0.0,
            pinch_strength_middle: 0.0,
            pinch_strength_ring: 0.0,
            pinch_strength_little: 0.0,
        };
        let mut locations = xr::HandJointLocationsEXT {
            ty: xr::HandJointLocationsEXT::TYPE,
            next: ptr::from_mut(&mut aim).cast(),
            is_active: false.into(),
            joint_count: joints.len() as u32,
            joint_locations: joints.as_mut_ptr(),
        };
        if unsafe { (self.locate)(tracker, &info, &mut locations) } != xr::Result::SUCCESS
            || !bool::from(locations.is_active)
            || aim.status.intersects(
                xr::HandTrackingAimFlagsFB::SYSTEM_GESTURE
                    | xr::HandTrackingAimFlagsFB::MENU_PRESSED,
            )
        {
            return;
        }
        let palm = joints[xr::HandJointEXT::PALM.into_raw() as usize];
        let valid = xr::SpaceLocationFlags::POSITION_VALID
            | xr::SpaceLocationFlags::POSITION_TRACKED
            | xr::SpaceLocationFlags::ORIENTATION_VALID
            | xr::SpaceLocationFlags::ORIENTATION_TRACKED;
        if !palm.location_flags.contains(valid) {
            return;
        }
        out[0] = 1.0;
        out[1..8].copy_from_slice(&sdk_pose(palm.pose));
        if aim.status.contains(xr::HandTrackingAimFlagsFB::VALID) {
            out[8] = if aim
                .status
                .contains(xr::HandTrackingAimFlagsFB::INDEX_PINCHING)
            {
                2.0
            } else {
                1.0
            };
            out[9..16].copy_from_slice(&sdk_pose(aim.aim_pose));
        }
    }
}

fn sdk_pose(pose: xr::Posef) -> [f32; 7] {
    let p = pose.position;
    let q = pose.orientation;
    [p.x, p.y, -p.z, -q.x, -q.y, q.z, q.w]
}

impl Drop for Tracking {
    fn drop(&mut self) {
        unsafe {
            if self.left != xr::HandTrackerEXT::NULL {
                (self.destroy_tracker)(self.left);
            }
            if self.right != xr::HandTrackerEXT::NULL {
                (self.destroy_tracker)(self.right);
            }
            if self.space != xr::Space::NULL {
                (self.destroy_space)(self.space);
            }
        }
    }
}

pub(crate) fn sample(
    instance: u64,
    session: u64,
    proc_addr: usize,
    unobstructed: bool,
    out: &mut [f32; 32],
) -> bool {
    if instance == 0 || session == 0 || proc_addr == 0 {
        return false;
    }
    let session = xr::Session::from_raw(session);
    let instance = xr::Instance::from_raw(instance);
    TRACKING.with(|cell| {
        let mut tracker = cell.borrow_mut();
        if tracker
            .as_ref()
            .is_some_and(|old| old.instance != instance || old.session != session)
        {
            // OpenXR already reclaimed child handles when the old SDK session ended.
            std::mem::forget(tracker.take());
        }
        if tracker.is_none() {
            let get = unsafe { std::mem::transmute(proc_addr) };
            let Ok(value) = Tracking::new(
                Session {
                    instance,
                    session,
                    get,
                },
                unobstructed,
            ) else {
                return false;
            };
            *tracker = Some(value);
        }
        let current = tracker.as_ref().unwrap();
        let mut now = libc::timespec {
            tv_sec: 0,
            tv_nsec: 0,
        };
        let mut time = xr::Time::from_nanos(0);
        if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut now) } != 0
            || unsafe { (current.time)(instance, &now, &mut time) } != xr::Result::SUCCESS
        {
            return true;
        }
        current.sample(current.left, time, &mut out[..16]);
        current.sample(current.right, time, &mut out[16..]);
        true
    })
}

pub(crate) fn close(instance: u64, session: u64) {
    TRACKING.with(|cell| {
        let mut tracking = cell.borrow_mut();
        if tracking.as_ref().is_some_and(|old| {
            old.instance.into_raw() == instance && old.session.into_raw() == session
        }) {
            tracking.take();
        }
    });
}
