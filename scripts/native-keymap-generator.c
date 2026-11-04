/* 固定xkeyboard-configだけを解決し、Androidでinclude不要なtextへ展開する。 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <xkbcommon/xkbcommon.h>
#include <xkbcommon/xkbcommon-keysyms.h>

static int validate(const char *text, const char *layout) {
    struct xkb_context *context = xkb_context_new(XKB_CONTEXT_NO_DEFAULT_INCLUDES | XKB_CONTEXT_NO_ENVIRONMENT_NAMES);
    if (!context) return 0;
    struct xkb_keymap *map = xkb_keymap_new_from_string(context, text, XKB_KEYMAP_FORMAT_TEXT_V1, XKB_KEYMAP_COMPILE_NO_FLAGS);
    if (!map) { xkb_context_unref(context); return 0; }
    struct xkb_state *state = xkb_state_new(map);
    if (!state) { xkb_keymap_unref(map); xkb_context_unref(context); return 0; }
    xkb_keycode_t a = xkb_keymap_key_by_name(map, "AC01");
    xkb_keycode_t shift = xkb_keymap_key_by_name(map, "LFSH");
    xkb_keycode_t two = xkb_keymap_key_by_name(map, "AE02");
    int valid = a != XKB_KEYCODE_INVALID && shift != XKB_KEYCODE_INVALID && two != XKB_KEYCODE_INVALID && xkb_state_key_get_one_sym(state, a) == XKB_KEY_a;
    xkb_state_update_key(state, shift, XKB_KEY_DOWN);
    valid = valid && xkb_state_key_get_one_sym(state, a) == XKB_KEY_A;
    valid = valid && xkb_state_key_get_one_sym(state, two) == (strcmp(layout, "jp") == 0 ? XKB_KEY_quotedbl : XKB_KEY_at);
    xkb_state_unref(state);
    xkb_keymap_unref(map);
    xkb_context_unref(context);
    return valid;
}

int main(int argc, char **argv) {
    if (argc != 5) return 2;
    struct xkb_context *context = xkb_context_new(XKB_CONTEXT_NO_DEFAULT_INCLUDES | XKB_CONTEXT_NO_ENVIRONMENT_NAMES);
    if (!context || !xkb_context_include_path_append(context, argv[1])) return 3;
    struct xkb_rule_names names = {.rules=argv[2], .model=argv[3], .layout=argv[4], .variant="", .options=""};
    struct xkb_keymap *map = xkb_keymap_new_from_names(context, &names, XKB_KEYMAP_COMPILE_NO_FLAGS);
    if (!map) { xkb_context_unref(context); return 4; }
    char *text = xkb_keymap_get_as_string(map, XKB_KEYMAP_FORMAT_TEXT_V1);
    int result = text && validate(text, argv[4]) && fputs(text, stdout) >= 0 ? 0 : 5;
    free(text);
    xkb_keymap_unref(map);
    xkb_context_unref(context);
    return result;
}
