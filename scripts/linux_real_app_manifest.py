"""Pure, shared Mousepad manifest schema; validation is not package acceptance.

This is the original A/A0 manifest validator, separated from filesystem
inspection so the B1 contract has no dependency on an inspector or helper.
"""
import re

APP_ID = "mousepad-editor-v1"
WINDOW_APP_ID = "mousepad"
EXECUTABLE = "/usr/bin/mousepad"
SETTINGS_KIND = "eidolon-keyfile-v1"
SETTINGS_POLICY = "mousepad-single-main-no-restore-v1"
SETTINGS_RELATIVE_PATH = "config/glib-2.0/settings/keyfile"
MANIFEST_FIELDS = frozenset({"schema", "app_id", "package", "package_version", "architecture", "executable", "executable_sha256", "dependency_fingerprint", "settings_kind", "settings_policy", "settings_module_sha256", "settings_relative_path", "window_app_ids", "launch_policy", "state_policy"})
HASH = re.compile(r"[0-9a-f]{64}\Z")


def _ascii(value):
    return type(value) is str and 1 <= len(value) <= 128 and all(33 <= ord(c) <= 126 for c in value)


def _hash(value):
    return type(value) is str and HASH.fullmatch(value) is not None


def validate_manifest(value):
    """Return the original object, with exactly the A/A0 rejection conditions."""
    valid = (type(value) is dict and set(value) == MANIFEST_FIELDS and type(value["schema"]) is int and value["schema"] == 1 and
             value["app_id"] == APP_ID and value["package"] == "mousepad" and _ascii(value["package_version"]) and
             _ascii(value["architecture"]) and value["executable"] == EXECUTABLE and
             all(_hash(value[k]) for k in ("executable_sha256", "dependency_fingerprint", "settings_module_sha256")) and
             value["settings_kind"] == SETTINGS_KIND and value["settings_policy"] == SETTINGS_POLICY and
             value["settings_relative_path"] == SETTINGS_RELATIVE_PATH and value["window_app_ids"] == [WINDOW_APP_ID] and
             value["launch_policy"] == "mousepad-private-bus-v1" and value["state_policy"] == "persistent-keyfile-locked-v1")
    if not valid:
        raise ValueError("APP_PACKAGE_UNSUPPORTED")
    return value
