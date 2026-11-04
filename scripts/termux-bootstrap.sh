#!/data/data/com.termux/files/usr/bin/bash
# 公式GitHub debug版Termuxの初回bootstrap完了後、Termux UIDで実行する。
set -euo pipefail
umask 077
public_key=${1:?PCのSSH公開鍵を一つ指定してください}
[[ "$public_key" == ssh-ed25519\ * ]] || { echo 'ed25519公開鍵が必要です' >&2; exit 2; }
export PREFIX=/data/data/com.termux/files/usr
export HOME=/data/data/com.termux/files/home
export PATH="$PREFIX/bin:/system/bin"
export DEBIAN_FRONTEND=noninteractive

# 公式の署名付きrepositoryへ固定。既存の追加repositoryは変更しない。
printf 'deb https://packages.termux.dev/apt/termux-main stable main\n' > "$PREFIX/etc/apt/sources.list"
apt-get update
apt-get install -y -o Dpkg::Options::=--force-confold openssh termux-services

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
grep -Fqx -- "$public_key" "$HOME/.ssh/authorized_keys" || printf '%s\n' "$public_key" >> "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
ssh-keygen -A
cat > "$HOME/.ssh/eidolon_sshd_config" <<EOF
Port 8022
ListenAddress 127.0.0.1
HostKey $PREFIX/etc/ssh/ssh_host_ed25519_key
PidFile $HOME/.ssh/eidolon_sshd.pid
AuthorizedKeysFile .ssh/authorized_keys
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
StrictModes yes
AllowStreamLocalForwarding yes
ClientAliveInterval 30
ClientAliveCountMax 3
Subsystem sftp $PREFIX/libexec/sftp-server
EOF
sshd -t -f "$HOME/.ssh/eidolon_sshd_config"
sshd -T -f "$HOME/.ssh/eidolon_sshd_config" | grep -Ei '^(port|listenaddress|pubkeyauthentication|passwordauthentication|kbdinteractiveauthentication|allowstreamlocalforwarding) '

# Termuxのservice supervisorに登録。Termux再起動時もこの設定で起動する。
service_dir="$PREFIX/var/service/eidolon-sshd"
mkdir -p "$service_dir/log" "$PREFIX/var/log/sv/eidolon-sshd"
cat > "$service_dir/run" <<EOF
#!$PREFIX/bin/sh
exec 2>&1
exec $PREFIX/bin/sshd -D -e -f $HOME/.ssh/eidolon_sshd_config
EOF
cat > "$service_dir/log/run" <<EOF
#!$PREFIX/bin/sh
exec $PREFIX/bin/svlogd -tt $PREFIX/var/log/sv/eidolon-sshd
EOF
printf '#!%s/bin/bash\n' "$PREFIX" > "$service_dir/check"
cat >> "$service_dir/check" <<'EOF'
exec 3<>/dev/tcp/127.0.0.1/8022 || exit 1
IFS= read -r -t 2 banner <&3 || exit 1
[[ "$banner" == SSH-* ]]
EOF
chmod 700 "$service_dir/run" "$service_dir/log/run" "$service_dir/check"
. "$PREFIX/etc/profile.d/start-services.sh"
# 既存serviceにlogを後から追加した場合はrunsv自身の再生成が必要。
# sshdだけのrestartではlog/superviseが作られない。
if [[ -p "$service_dir/supervise/control" && ! -p "$service_dir/log/supervise/control" ]]; then
    sv exit "$service_dir"
fi
for attempt in {1..30}; do
    [[ -p "$service_dir/log/supervise/control" ]] && break
    sleep 1
done
[[ -p "$service_dir/log/supervise/control" ]] || { echo 'SSH log supervisorの起動待ちがtimeoutしました' >&2; exit 1; }
sv -w 15 stop "$service_dir"
sv-enable eidolon-sshd
sv -w 15 start "$service_dir"
sv status "$service_dir"
sv status "$service_dir/log"
ssh-keygen -lf "$PREFIX/etc/ssh/ssh_host_ed25519_key.pub"
dpkg-query -W openssh termux-services
id
