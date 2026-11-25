set -eu
LC_ALL=C
PATH=/usr/local/bin:/usr/bin:/bin
export LC_ALL PATH

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

uid=$(id -u) || fail SYSTEMD_USER_UNAVAILABLE
[ "$uid" != 0 ] || fail SYSTEMD_USER_UNAVAILABLE
home=$(getent passwd "$uid" | cut -d: -f6)
[ -d "$home" ] && [ "$(stat -Lc %u -- "$home")" = "$uid" ] ||
    fail SYSTEMD_USER_UNAVAILABLE
runtime_dir=/run/user/$uid
[ -d "$runtime_dir" ] && [ ! -L "$runtime_dir" ] &&
    [ "$(stat -c '%u:%a' -- "$runtime_dir")" = "$uid:700" ] &&
    [ "$(findmnt -n -o FSTYPE -T "$runtime_dir")" = tmpfs ] ||
    fail CACHE_RUNTIME_UNAVAILABLE
export XDG_RUNTIME_DIR="$runtime_dir"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_dir/bus"
systemctl --user show --property=Version >/dev/null 2>&1 || fail SYSTEMD_USER_UNAVAILABLE

for command_name in systemd-run dbus-run-session flock; do
    command -v "$command_name" >/dev/null || fail SYSTEMD_DEPENDENCY_MISSING
done
waypipe=$(realpath -e -- "$1") &&
    [ -f "$waypipe" ] && [ -x "$waypipe" ] || fail SYSTEMD_DEPENDENCY_MISSING
fingerprint=$(sha256sum < "$waypipe")
[ "${fingerprint%% *}" = "$2" ] || fail WAYPIPE_PIN_MISMATCH

chrome=$(command -v "$3" || command -v "$4") &&
    chrome=$(realpath -e -- "$chrome") &&
    [ -f "$chrome" ] && [ -x "$chrome" ] || chrome=''
read -r boot < /proc/sys/kernel/random/boot_id || fail SYSTEMD_USER_UNAVAILABLE
printf '%s\n' "$boot" "$uid" "$home" "$waypipe" "$chrome"
