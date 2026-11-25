set -eu
umask 077
LC_ALL=C
PATH=/usr/local/bin:/usr/bin:/bin
export LC_ALL PATH

check_owner() {
    uid=$(id -u)
    read -r boot < /proc/sys/kernel/random/boot_id
    [ "$uid" != 0 ] && [ "$uid" = "$1" ] && [ "$boot" = "$2" ] ||
        fail SYSTEMD_OWNER_MISMATCH
}
fail() {
    printf '%s\n' "$1" >&2
    exit 1
}
check_owner "$1" "$2"
home=$3
installation=$4
storage=$5
app_key=$6
app_unit=$7
expiry_unit=$8
runtime_name=$9
shift 9
grace=$1
waypipe=$2
pin=$3
chrome=$4

export XDG_RUNTIME_DIR="/run/user/$uid"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
runtime=$XDG_RUNTIME_DIR/$runtime_name
[ "$(stat -c '%u:%a' -- "$XDG_RUNTIME_DIR")" = "$uid:700" ] &&
    [ ! -L "$XDG_RUNTIME_DIR" ] &&
    [ "$(findmnt -n -o FSTYPE -T "$XDG_RUNTIME_DIR")" = tmpfs ] ||
    fail CACHE_RUNTIME_UNAVAILABLE
[ "$(getent passwd "$uid" | cut -d: -f6)" = "$home" ] &&
    [ "$(realpath -e -- "$home")" = "$home" ] || fail SYSTEMD_PROFILE_INVALID
[ -f "$waypipe" ] && [ -x "$waypipe" ] || fail SYSTEMD_DEPENDENCY_MISSING
fingerprint=$(sha256sum < "$waypipe")
[ "${fingerprint%% *}" = "$pin" ] || fail WAYPIPE_PIN_MISMATCH
[ -f "$chrome" ] && [ -x "$chrome" ] || fail SYSTEMD_APP_UNAVAILABLE

owned_directory() {
    [ ! -L "$1" ] && [ -d "$1" ] && [ "$(stat -c '%u' -- "$1")" = "$uid" ] ||
        fail SYSTEMD_PROFILE_INVALID
    case "$(stat -c '%a' -- "$1")" in
        *[2367]?|*?[2367]) fail SYSTEMD_PROFILE_INVALID ;;
    esac
}
ensure_directory() {
    mkdir -- "$1" 2>/dev/null || test -d "$1" || fail SYSTEMD_PROFILE_INVALID
    owned_directory "$1"
}
base=$home/.local/state/eidolon-gui
profile=$base/$installation/$storage/$app_key
owned_directory "$home"
for dir in "$home/.local" "$home/.local/state" "$base" "$base/$installation" \
    "$base/$installation/$storage" "$profile" "$profile/home" "$profile/config" \
    "$profile/data" "$profile/state" "$profile/Documents" "$profile/config/google-chrome"; do
    ensure_directory "$dir"
done
operation_lock=$profile/.operation.lock
profile_lock=$profile/.profile.lock
for lock in "$operation_lock" "$profile_lock"; do
    if [ ! -e "$lock" ] && [ ! -L "$lock" ]; then
        (set -C; : > "$lock") 2>/dev/null || test -f "$lock" || fail SYSTEMD_PROFILE_INVALID
    fi
    [ -f "$lock" ] && [ ! -L "$lock" ] &&
        [ "$(stat -c '%u:%a:%h' -- "$lock")" = "$uid:600:1" ] ||
        fail SYSTEMD_PROFILE_INVALID
done

flock --exclusive --timeout 8 --close -- "$operation_lock" /bin/sh -s -- \
    "$operation_lock" "$app_unit" "$expiry_unit" "$runtime" "$runtime_name" \
    "$profile" "$profile_lock" "$grace" "$waypipe" "$chrome" <<'START'
set -eu
operation_lock=$1
app_unit=$2
expiry_unit=$3
runtime=$4
runtime_name=$5
profile=$6
profile_lock=$7
grace=$8
waypipe=$9
shift 9
chrome=$1

for unit in "$app_unit" "$expiry_unit.timer" "$expiry_unit.service"; do
    [ "$(systemctl --user show --property=LoadState --value "$unit")" = not-found ] ||
        { printf '%s\n' SYSTEMD_UNIT_EXISTS >&2; exit 1; }
done
[ ! -e "$runtime" ] && [ ! -L "$runtime" ] ||
    { printf '%s\n' SYSTEMD_UNIT_EXISTS >&2; exit 1; }

systemd-run --user --quiet --collect --unit="$expiry_unit" --on-active="${grace}s" \
    --timer-property=AccuracySec=1s --expand-environment=no -- \
    /usr/bin/flock --exclusive --close -- "$operation_lock" \
    /usr/bin/systemctl --user stop "$app_unit"

main=$(cat <<'APP'
set -eu
runtime=$1
profile=$2
profile_lock=$3
waypipe=$4
chrome=$5
attempt=0
while [ ! -S "$runtime/p" ]; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 120 ] || exit 74
    sleep .1
done
export HOME="$profile/home"
export XDG_CONFIG_HOME="$profile/config"
export XDG_DATA_HOME="$profile/data"
export XDG_STATE_HOME="$profile/state"
export XDG_CACHE_HOME="$runtime/cache"
export XDG_RUNTIME_DIR="$runtime/r"
export TMPDIR="$runtime/tmp"
cd -- "$profile/Documents"
cat > "$runtime/bus.xml" <<BUS
<busconfig>
  <type>session</type>
  <listen>unix:path=$XDG_RUNTIME_DIR/b</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*"/>
    <allow receive_sender="*"/>
    <allow own="*"/>
  </policy>
</busconfig>
BUS
exec /usr/bin/flock --nonblock --conflict-exit-code 73 --close -- "$profile_lock" \
    /usr/bin/dbus-run-session --config-file="$runtime/bus.xml" -- \
    "$waypipe" --no-gpu --compress none --threads 1 --socket "$runtime/p" \
    --display "$XDG_RUNTIME_DIR/w" server -- "$chrome" --ozone-platform=wayland \
    "--user-data-dir=$XDG_CONFIG_HOME/google-chrome"
APP
)
if ! systemd-run --user --quiet --no-block --collect --unit="$app_unit" \
    --slice=app.slice --property=Type=exec --property=KillMode=control-group --property=UMask=0077 \
    --property=RuntimeDirectoryMode=0700 \
    --property="RuntimeDirectory=$runtime_name $runtime_name/r $runtime_name/tmp $runtime_name/cache" \
    --property=TimeoutStartSec=15s --property=TimeoutStopSec=5s --expand-environment=no -- \
    /bin/sh -c "$main" eidolon-app "$runtime" "$profile" "$profile_lock" "$waypipe" "$chrome"; then
    systemctl --user --no-block stop "$expiry_unit.timer" >/dev/null 2>&1 || true
    exit 1
fi
printf '%s\n' starting
START
