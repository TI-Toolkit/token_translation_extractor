#!/usr/bin/env python3
"""Extract CE localization app token hooks and replacement font glyphs.

Token-hook identification derives from TI-Toolkit/token_translation_extractor,
commit 26522877275249e884ddaa9ba32a0553dd842793 (MIT; see LICENSE.extractor).
Certificate/eZ8 relocation reconstruction follows Brandon Wilson's Analyze8EX:
https://brandonw.net/calcstuff/Analyze8EX.zip (source dated 2015-03-25).
The 5E token groups are corrected from upstream using native GetTokString.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

try:
    from .font import decode_font, FONT_MAP, FONT_SOURCE
except ImportError:
    from font import decode_font, FONT_MAP, FONT_SOURCE

# Prefix, first second-byte, relative offset into the native token pointer table.
# The four 5E groups select a base by testing and clearing bits 4, 5, 6, 7.
# Verified by interpreting GetTokString in 31 distinct supplied CE ROM images.
GROUPS = [
    (0, 0, 0),
    (0x5C, 0, 0x300),
    (0x5D, 0, 0x31E),
    (0x5E, 0x10, 0x330),
    (0x5E, 0x20, 0x34E),
    (0x5E, 0x40, 0x372),
    (0x5E, 0x80, 0x384),
    (0x60, 0, 0x38D),
    (0x61, 0, 0x3AB),
    (0xAA, 0, 0x3C9),
    (0x62, 0, 0x3E7),
    (0x63, 0, 0x49E),
    (0x7E, 0, 0x549),
    (0xBB, 0, 0x582),
    (0xEF, 0, 0x864),
]
TOKEN_HOOK = rb"\x21(...)\xb7\xed\x52\x38.\x21(...)"
FONT_LOOKUP = (
    rb"\xe5\x78\x21(...)\x01(...)\xed\xb1\xe1\xc0\x21(...)"
    rb"\xed\x42\xed\x42\xed\x42\xed\x27\xbf\xc9"
)
# Unicode labels verified by rendering the actual replacement bitmap records.
OVERRIDE_CHARS = {
    "pt": {0x91: "ã", 0x8D: "Ã", 0xA9: "õ", 0xA5: "Õ"},
    "sv": {0x90: "å", 0x8C: "Å", 0xA8: "ø", 0xA4: "Ø"},
}


def read_field(data, offset):
    """Return (field ID, payload offset, payload length, next field offset)."""
    field_id = int.from_bytes(data[offset : offset + 2], "big")
    length = field_id & 15
    start = offset + 2
    if length >= 13:
        width = {13: 1, 14: 2, 15: 4}[length]
        length = int.from_bytes(data[start : start + width], "big")
        start += width
    assert start + length <= len(data), (offset, field_id, length)
    return field_id & 0xFFF0, start, length, start + length


def relocate(data):
    """Return code, original code-file offset, relocation records, eZ8 offset."""
    field_id, start, _, end = read_field(data, 78)
    assert field_id == 0x8100
    fields = []
    while start < end:
        field_id, payload, size, start = read_field(data, start)
        fields.append((field_id, payload, size))
    ez8_offset, size = next((p, n) for f, p, n in fields if f == 0x8170)
    raw = data[ez8_offset : ez8_offset + size]
    assert raw[:3] == b"eZ8"
    code_offset = int.from_bytes(raw[18:21], "little")
    assert (code_offset - 42) % 6 == 0
    code = bytearray(raw[code_offset:])
    relocations = {}
    for offset in range(42, code_offset, 6):
        key = int.from_bytes(raw[offset : offset + 3], "little")
        target = int.from_bytes(raw[offset + 3 : offset + 6], "little")
        assert key not in relocations and key + 3 <= len(code)
        assert target < len(code)
        relocations[key] = {"target": target, "file_offset": ez8_offset + offset}
        code[key : key + 3] = target.to_bytes(3, "little")
    return code, ez8_offset + code_offset, relocations, ez8_offset


def token_from_offset(offset):
    prefix, first, base = max(
        (group for group in GROUPS if group[2] <= offset),
        key=lambda group: group[2],
    )
    low = first + (offset - base) // 3
    assert low < 256
    return f"{prefix:02X}{low:02X}" if prefix else f"{low:02X}"


def font_overrides(code, language, code_file_offset, base_mapping=None):
    """Read LocalizeHook75/76 replacement glyph pointers and raw bitmaps."""
    matches = list(re.finditer(FONT_LOOKUP, code, re.S))
    mapping = dict(FONT_MAP if base_mapping is None else base_mapping)
    glyphs = {}
    assert len(matches) == (2 if language in OVERRIDE_CHARS else 0)
    for index, match in enumerate(matches):
        first = int.from_bytes(match[1], "little")
        count = int.from_bytes(match[2], "little")
        last_pointer = int.from_bytes(match[3], "little")
        # CPIR has decremented BC before it returns after a successful match.
        pointer_base = last_pointer - 3 * (count - 1)
        assert count == 4
        assert set(code[first : first + count]) == set(OVERRIDE_CHARS[language])
        for glyph_index, char in enumerate(code[first : first + count]):
            pointer = pointer_base + glyph_index * 3
            target = int.from_bytes(code[pointer : pointer + 3], "little")
            size, kind = (25, "small") if index == 0 else (28, "large")
            assert target + size <= len(code)
            glyph = glyphs.setdefault(
                f"{char:02X}",
                {"display": OVERRIDE_CHARS[language][char]},
            )
            glyph[kind] = {
                "lookup_code_offset": match.start(),
                "lookup_file_offset": code_file_offset + match.start(),
                "code_offset": target,
                "file_offset": code_file_offset + target,
                "record_hex": code[target : target + size].hex().upper(),
                "size": size,
            }
            mapping[f"{char:02X}"] = glyph["display"]
    return mapping, glyphs


def extract_app(app, catalog_dir, base_mapping=None):
    """Extract one catalog entry, with no native-ROM or generated-data dependency."""
    data = (Path(catalog_dir) / app["file"]).read_bytes()
    assert data[:8] == b"**TIFL**"
    assert hashlib.sha256(data).hexdigest() == app["sha256"]
    code, code_file_offset, rels, ez8_offset = relocate(data)
    matches = list(re.finditer(TOKEN_HOOK, code, re.S))
    assert len(matches) == 1
    match = matches[0]
    limit = int.from_bytes(match[1], "little")
    base = int.from_bytes(match[2], "little")
    assert base + limit + 3 <= len(code) and limit % 3 == 0
    assert rels[match.start() + 10]["target"] == base
    mapping, glyphs = font_overrides(
        code,
        app["language"],
        code_file_offset,
        base_mapping,
    )
    tokens = {}
    for index in range(0, limit + 1, 3):
        pointer = base + index
        target = int.from_bytes(code[pointer : pointer + 3], "little")
        if not target:
            continue
        assert rels[pointer]["target"] == target
        length = code[target]
        assert target + 1 + length <= len(code)
        raw = bytes(code[target + 1 : target + 1 + length])
        tokens[token_from_offset(index)] = {
            "ti_ascii": raw.hex().upper(),
            "display": decode_font(raw, mapping),
            "placeholder": raw == b"?",
            "length": length,
            "table_index": index // 3,
            "table_code_offset": pointer,
            "table_file_offset": code_file_offset + pointer,
            "string_code_offset": target,
            "string_file_offset": code_file_offset + target,
            "pointer_relocation_file_offset": rels[pointer]["file_offset"],
        }
    alternate_match = re.search(
        rb"\x19\x11(...)\xcd...\x20.\x3a...\xfe\x52\x20.\x21(...)",
        code[match.start() : match.start() + 70],
        re.S,
    )
    assert alternate_match
    entry = int.from_bytes(alternate_match[1], "little")
    alternate = int.from_bytes(alternate_match[2], "little")
    assert entry == base + 3 * 0xD7
    raw_alternate = bytes(code[alternate + 1 : alternate + 1 + code[alternate]])
    return {
        "sha256": app["sha256"],
        "language": app["language"],
        "app_version": app["headers"][app["app_header_index"]]["app_version"],
        "target_model": app["target_model"],
        "source_file": app["file"],
        "sources": app["sources"],
        "tokens": tokens,
        "font_mapping": mapping,
        "font_mapping_provenance": FONT_SOURCE,
        "font_overrides": glyphs,
        "contextual_alternates": [
            {
                "token": "D7",
                "condition": "OS error-display state byte equals 0x52 (Goto label)",
                "ti_ascii": raw_alternate.hex().upper(),
                "display": decode_font(raw_alternate, mapping),
                "string_code_offset": alternate,
                "string_file_offset": code_file_offset + alternate,
            }
        ],
        "extraction": {
            "certificate_start_file_offset": 78,
            "ez8_file_offset": ez8_offset,
            "code_file_offset": code_file_offset,
            "code_size": len(code),
            "relocation_count": len(rels),
            "relocated_code_sha256": hashlib.sha256(code).hexdigest(),
            "token_hook_code_offset": match.start(),
            "token_hook_file_offset": code_file_offset + match.start(),
            "token_hook_bytes": code[match.start() - 2 : match.start() + 64]
            .hex()
            .upper(),
            "table_code_offset": base,
            "table_file_offset": code_file_offset + base,
            "table_limit": limit,
            "table_entry_count": limit // 3 + 1,
            "nonzero_entry_count": len(tokens),
            "table_ti_ascii_sha256": hashlib.sha256(
                json.dumps(
                    {k: v["ti_ascii"] for k, v in tokens.items()},
                    sort_keys=True,
                ).encode()
            ).hexdigest(),
            "validation": "All nonzero entries have explicit relocations, in-image "
            "pointers and in-bounds length-prefixed strings.",
        },
    }


def version_differences(apps):
    differences = []
    for language in sorted({app["language"] for app in apps}):
        previous = None
        versions = sorted(
            (app for app in apps if app["language"] == language),
            key=lambda app: tuple(int(p) for p in app["app_version"].split(".")),
        )
        for app in versions:
            now = {k: v["ti_ascii"] for k, v in app["tokens"].items()}
            old = (
                {}
                if previous is None
                else {k: v["ti_ascii"] for k, v in previous["tokens"].items()}
            )
            changes = {
                key: {
                    "from": old.get(key),
                    "to": now.get(key),
                    "display": app["tokens"].get(key, {}).get("display"),
                }
                for key in sorted(set(now) | set(old))
                if now.get(key) != old.get(key)
            }
            differences.append(
                {
                    "language": language,
                    "app_version": app["app_version"],
                    "sha256": app["sha256"],
                    "previous": previous["app_version"] if previous else None,
                    "record_count": len(now),
                    "changes": changes,
                    "source_labels": sorted(
                        {source.get("version") or "" for source in app["sources"]}
                    ),
                }
            )
            previous = app
    return differences


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--font-map", type=Path, help="Optional replacement base mapping JSON"
    )
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    mapping = None
    if args.font_map:
        mapping = json.loads(args.font_map.read_text(encoding="utf-8"))
        mapping = mapping.get("mapping", mapping)
    apps = [
        extract_app(app, args.catalog.parent, mapping)
        for app in catalog["apps"]
        if app["family"] == "ce"
    ]
    args.output.mkdir(parents=True, exist_ok=True)
    records = args.output / "records"
    records.mkdir(exist_ok=True)
    for app in apps:
        name = f"{app['language']}-{app['app_version']}-{app['sha256'][:12]}.json"
        (records / name).write_text(
            json.dumps(app, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (args.output / "index.json").write_text(
        json.dumps(apps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "version-differences.json").write_text(
        json.dumps(version_differences(apps), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "apps": len(apps),
                "records": sum(len(app["tokens"]) for app in apps),
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
