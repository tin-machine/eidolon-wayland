# shellcheck shell=bash
# Sourced by build-wayland and by the isolated Termux CI container.
fcitx_version=5.1.23
# Termux package revision consumed by scripts/ci/termux-ime.
# shellcheck disable=SC2034
fcitx_package_revision=1
fcitx_revision=27cca6e0239938b2d53937061d0980c087856b22
fcitx_recipe=4977ed6e93ae288cddba46d15e6ffcc4287bd4b6

apply_fcitx_patch() {
    if ! git -C fcitx apply --reverse --check "$1" 2>/dev/null; then
        git -C fcitx apply "$1"
    fi
}

# Work in a private build directory. A recipe directory is supplied only for the
# compatibility run, which uses the source and patches for the installed package.
prepare_fcitx() {
    local recipe_dir=${1:-} patch name
    if [[ ! -d fcitx/.git ]]; then
        git clone --depth=1 --branch "$fcitx_version" https://github.com/fcitx/fcitx5.git fcitx
    fi
    [[ $(git -C fcitx rev-parse HEAD) == "$(git -C fcitx rev-parse --verify "$fcitx_revision^{commit}")" ]]
    mkdir -p fcitx-patches
    if [[ -n $recipe_dir ]]; then
        for patch in "$recipe_dir"/*.patch; do
            [[ -f $patch ]] || continue
            name=${patch##*/}
            sed "s|@TERMUX_PREFIX@|$PREFIX|g" "$patch" > "fcitx-patches/$name"
            apply_fcitx_patch "$PWD/fcitx-patches/$name"
        done
    else
        for name in cmake-FindDL.cmake cmake-FindPthread.cmake fix-hardcoded-paths src-modules-spell-CMakeLists.txt; do
            patch="$PWD/fcitx-patches/$name.patch"
            curl -fsSL --retry 3 "https://raw.githubusercontent.com/termux/termux-packages/$fcitx_recipe/x11-packages/fcitx5/$name.patch" |
                sed "s|@TERMUX_PREFIX@|$PREFIX|g" > "$patch"
            apply_fcitx_patch "$patch"
        done
    fi
}

configure_fcitx() {
    local build=$1 wayland=$2 builtins link_flags
    builtins=$(clang++ --print-libgcc-file-name)
    link_flags="-ldl -Wl,--exclude-libs,${builtins##*/}"
    cmake -S fcitx -B "$build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PLATFORM_NO_VERSIONED_SONAME=ON -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
        -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_PREFIX_PATH="$PREFIX" \
        -DDL_INCLUDE_DIR="$PREFIX/include" -DDL_LIBRARY=0 -DPTHREAD_INCLUDE_DIR="$PREFIX/include" \
        -DENABLE_TEST=OFF -DENABLE_WAYLAND="$wayland" -DUSE_SYSTEM_YOGA=ON \
        -DUSE_SYSTEM_PLASMA_WAYLAND_PROTOCOLS=OFF -DCMAKE_CXX_FLAGS=-fexperimental-library \
        -DCMAKE_SHARED_LINKER_FLAGS="$link_flags" -DCMAKE_EXE_LINKER_FLAGS="$link_flags"
}
