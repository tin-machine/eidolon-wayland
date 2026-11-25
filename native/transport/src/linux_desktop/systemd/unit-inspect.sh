set -eu
LC_ALL=C
PATH=/usr/local/bin:/usr/bin:/bin
export LC_ALL PATH

uid=$(id -u)
read -r boot < /proc/sys/kernel/random/boot_id
if [ "$uid" != "$1" ] || [ "$boot" != "$2" ]; then
    printf '%s\n' SYSTEMD_OWNER_MISMATCH >&2
    exit 1
fi
export XDG_RUNTIME_DIR="/run/user/$uid"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
exec systemctl --user show --no-pager \
    --property=LoadState,ActiveState,SubState,InvocationID,ControlGroup,Result "$3"
