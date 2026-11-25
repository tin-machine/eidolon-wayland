set -eu
LC_ALL=C
PATH=/usr/local/bin:/usr/bin:/bin
export LC_ALL PATH

operation=$1
uid=$(id -u)
read -r boot < /proc/sys/kernel/random/boot_id
if [ "$uid" = 0 ] || [ "$uid" != "$2" ] || [ "$boot" != "$3" ]; then
    printf '%s\n' SYSTEMD_OWNER_MISMATCH >&2
    exit 1
fi
invocation=$4
app=$5
timer=$6
expiry=$7
home=$8
installation=$9
shift 9
storage=$1
app_key=$2
export XDG_RUNTIME_DIR="/run/user/$uid"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
lock=$home/.local/state/eidolon-gui/$installation/$storage/$app_key/.operation.lock
if [ ! -f "$lock" ] || [ -L "$lock" ] ||
    [ "$(stat -c '%u:%a:%h' -- "$lock")" != "$uid:600:1" ] ||
    [ "$(realpath -e -- "$lock")" != "$lock" ]; then
    printf '%s\n' SYSTEMD_PROFILE_INVALID >&2
    exit 1
fi

exec flock --exclusive --timeout 8 --close -- "$lock" /bin/sh -s -- \
    "$operation" "$invocation" "$app" "$timer" "$expiry" <<'CONTROL'
set -eu
operation=$1
invocation=$2
app=$3
timer=$4
expiry=$5
property() {
    systemctl --user show --property="$1" --value "$2"
}
if [ "$operation" = reap-after-exit ]; then
    if [ "$(property LoadState "$app")" != not-found ]; then
        printf '%s\n' SYSTEMD_INVOCATION_MISMATCH >&2
        exit 1
    fi
elif [ "$(property InvocationID "$app")" != "$invocation" ]; then
    printf '%s\n' SYSTEMD_INVOCATION_MISMATCH >&2
    exit 1
fi
case "$operation" in
    renew)
        if [ "$(property ActiveState "$app")" != active ] ||
            [ "$(property SubState "$timer")" != waiting ] ||
            [ "$(property ActiveState "$expiry")" != inactive ]; then
            printf '%s\n' SYSTEMD_LEASE_NOT_WAITING >&2
            exit 1
        fi
        # A queued expiry service remains independent of this timer transaction.
        systemctl --user try-restart "$timer"
        if [ "$(property SubState "$timer")" != waiting ] ||
            [ "$(property InvocationID "$app")" != "$invocation" ]; then
            printf '%s\n' SYSTEMD_LEASE_UPDATE_UNKNOWN >&2
            exit 1
        fi
        property NextElapseUSecMonotonic "$timer"
        ;;
    stop|reap-after-exit)
        # The expiry worker takes this same lock before it can act on the application.
        # Cancel any waiting job, then let systemd stop the application's own cgroup.
        for unit in "$timer" "$expiry"; do
            if [ "$(property LoadState "$unit")" = loaded ]; then
                systemctl --user stop "$unit"
            fi
        done
        if [ "$operation" = stop ]; then
            systemctl --user stop "$app"
        fi
        ;;
    *)
        exit 1
        ;;
esac
CONTROL
