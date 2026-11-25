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
app=$3
timer=$4
expiry=$5
runtime_name=$6
export XDG_RUNTIME_DIR="/run/user/$uid"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
cgroup="/sys/fs/cgroup/user.slice/user-$uid.slice/user@$uid.service/app.slice/$app"
runtime="$XDG_RUNTIME_DIR/$runtime_name"

for unit in "$app" "$timer" "$expiry"; do
    [ "$(systemctl --user show --property=LoadState --value "$unit")" = not-found ] || exit 1
done
[ ! -e "$cgroup" ] && [ ! -L "$cgroup" ] && [ ! -e "$runtime" ] && [ ! -L "$runtime" ] || exit 1
printf '%s\n' retired
