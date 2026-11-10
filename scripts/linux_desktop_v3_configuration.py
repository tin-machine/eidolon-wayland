"""Strict local configuration export/import; not a remote command or wire record."""
from linux_desktop_v3_contract import (
    Manifest, Pins, _Record, canonical_json, fields, parse_json, require,
)

MAX_CONFIG_BYTES = 32768
KIND = "linux-desktop-v3-connection"


class ConnectionConfig(_Record):
    @classmethod
    def _validate(cls, raw):
        value = parse_json(raw, MAX_CONFIG_BYTES)
        fields(value, {"schema", "kind", "pins", "app_manifest"})
        require(type(value["schema"]) is int and value["schema"] == 1 and value["kind"] == KIND)
        pins = Pins.parse(canonical_json(value["pins"]))
        manifest = Manifest.parse(canonical_json(value["app_manifest"]))
        manifest.matches_pins(pins)
        return value

    @classmethod
    def capture(cls, pins, manifest):
        require(type(pins) is Pins and type(manifest) is Manifest)
        return cls.parse(canonical_json({"schema": 1, "kind": KIND,
            "pins": pins.record(), "app_manifest": manifest.record()}))

    def pins(self):
        return Pins.parse(canonical_json(self.record()["pins"]))

    def manifest(self):
        return Manifest.parse(canonical_json(self.record()["app_manifest"]))
