#!/usr/bin/env python3
"""Extract Z80 localization app pointer tables with binary provenance.

Inputs are the preserved TIFL files referenced by the supplied catalog.  Only
explicit nonzero table pointers are translations; zero entries retain the OS
token spelling.  Versions in this output are application versions, not OS
versions.  No binary disassembler or third party Python dependency is needed.
"""

from pathlib import Path
import argparse
import collections
import hashlib
import json
import re
import xml.etree.ElementTree as ET

try:
    from .font import FONT_MAP, FONT_SOURCE, decode_font
except ImportError:
    from font import FONT_MAP, FONT_SOURCE, decode_font

ROOT = Path(__file__).resolve().parents[1] / "research/localization-apps"

# Human assignments from the extracted raster glyphs, validated below against
# exact native glyph bytes for every application.  Small and large fonts are
# intentionally separate: Swedish small-font ø/Ø are not present in its large
# mono/CSE font, while the CE app does override those large glyphs.
LARGE_SEMANTICS = {
    "pl": dict(
        zip(
            "8D 91 B2 B3 95 99 9D A1 B4 B5 AC B0 A5 A9 AD B1".split(),
            "ĄąĆćĘęŁłŃńŚśŹźŻż",
        )
    ),
    "hu": dict(zip("A4 A8 AC B0".split(), "ŐőŰű")),
    "da": dict(zip("91 8D 90 8C A9 A5".split(), "æÆåÅøØ")),
    "no": dict(zip("91 8D 90 8C A9 A5".split(), "æÆåÅøØ")),
    "pt": dict(zip("91 8D A9 A5".split(), "ãÃõÕ")),
    "sv": dict(zip("90 8C".split(), "åÅ")),
    "de": {"BA": "ß"},
}
SMALL_SEMANTICS = {
    **LARGE_SEMANTICS,
    "hu": {**LARGE_SEMANTICS["hu"], "A7": "ó"},
    "fi": {"8C": "Å", "90": "å"},
    "sv": {**LARGE_SEMANTICS["sv"], "A8": "ø", "A4": "Ø"},
}


VERIFIED_GLYPHS = {
    (
        "cse",
        "da",
        "large",
        "8C",
    ): "088010400880000038E078F0C018C018C018F8F8F8F8C018C018C018",
    (
        "cse",
        "da",
        "large",
        "8D",
    ): "38F838F868806880C880C880C8F0C8F0F880F880C880C880C8F8C8F8",
    (
        "cse",
        "da",
        "large",
        "90",
    ): "0880104008800000000038F078F8001838F878F8C018C018F8F878F8",
    (
        "cse",
        "da",
        "large",
        "91",
    ): "0000000000000000306078F00898089838F878F8C880C880F8F87870",
    (
        "cse",
        "da",
        "large",
        "A5",
    ): "18D838F870706070C0D8C0D8C898C898D818D81870307070F8E0D8C0",
    (
        "cse",
        "da",
        "large",
        "A9",
    ): "000000000000000038F878F0E078C0D8C898C898D818F03878F0F8E0",
    ("cse", "da", "small", "8C"): "0A1800180000003C007E00C300C300FF00FF00C300C300C300",
    ("cse", "da", "small", "8D"): "0B000000003F807F80CC00CC00CF00FF00FC00CC00CF80CF80",
    ("cse", "da", "small", "90"): "0A0000000018001800000038007C00CC00CC00CC007F003300",
    ("cse", "da", "small", "91"): "0C00000000000000003B007F80CCC0CCC0CF80CC007FC03380",
    ("cse", "da", "small", "A5"): "0A000000003D007F0066006E006E00760076006600FE00BC00",
    ("cse", "da", "small", "A9"): "0900000000000000003A007C00CE00D600D600E6007C00B800",
    (
        "cse",
        "pt",
        "large",
        "8D",
    ): "380068B000E0000038E078F0C018C018C018F8F8F8F8C018C018C018",
    (
        "cse",
        "pt",
        "large",
        "91",
    ): "380068B000E00000000038F078F8001838F878F8C018C018F8F878F8",
    (
        "cse",
        "pt",
        "large",
        "A5",
    ): "380068B000E0000018C03060603060306030603060306030306018C0",
    (
        "cse",
        "pt",
        "large",
        "A9",
    ): "380068B000E00000000038E078F0E038C018C018C018E03878F038E0",
    ("cse", "pt", "small", "8D"): "0A33007B00CE0000003C007E00C300C300FF00FF00C300C300",
    ("cse", "pt", "small", "91"): "0A000033007B00CE00000038007C00CC00CC00CC007F003300",
    ("cse", "pt", "small", "A5"): "0A33007B00CE0000003C007E00E700C300C300E7007E003C00",
    ("cse", "pt", "small", "A9"): "09000033007B00CE00000038007C00C600C600C6007C003800",
    (
        "cse",
        "sv",
        "large",
        "8C",
    ): "088010400880000038E078F0C018C018C018F8F8F8F8C018C018C018",
    (
        "cse",
        "sv",
        "large",
        "90",
    ): "0880104008800000000038F078F8001838F878F8C018C018F8F878F8",
    ("cse", "sv", "small", "8C"): "0A1800180000003C007E00C300C300FF00FF00C300C300C300",
    ("cse", "sv", "small", "90"): "0A0000000018001800000038007C00CC00CC00CC007F003300",
    ("mono", "da", "large", "8C"): "0504000E11111F11",
    ("mono", "da", "large", "8D"): "050F15141E141517",
    ("mono", "da", "large", "90"): "0504000E010F110F",
    ("mono", "da", "large", "91"): "0500001A050F140B",
    ("mono", "da", "large", "A5"): "05000D1215150916",
    ("mono", "da", "large", "A9"): "0500000E1315190E",
    ("mono", "da", "small", "8C"): "040400040A0E0A00",
    ("mono", "da", "small", "8D"): "06001E283C282E00",
    ("mono", "da", "small", "90"): "050008000C140A00",
    ("mono", "da", "small", "91"): "060000341A2C1600",
    ("mono", "da", "small", "A5"): "06000A141C142800",
    ("mono", "da", "small", "A9"): "0600000A14142800",
    ("mono", "de", "large", "BA"): "050C121216111116",
    ("mono", "de", "small", "BA"): "05000C1214121400",
    ("mono", "fi", "small", "8C"): "050C120C121E1200",
    ("mono", "fi", "small", "90"): "050814000C140A00",
    ("mono", "hu", "large", "A4"): "0509120E1111110E",
    ("mono", "hu", "large", "A8"): "050912000E11110E",
    ("mono", "hu", "large", "AC"): "050912001111110E",
    ("mono", "hu", "large", "B0"): "050912001111130D",
    ("mono", "hu", "small", "A4"): "0612241C22221C00",
    ("mono", "hu", "small", "A7"): "060004081C221C00",
    ("mono", "hu", "small", "A8"): "061224001C221C00",
    ("mono", "hu", "small", "AC"): "0612240022223E00",
    ("mono", "hu", "small", "B0"): "0612240024241A00",
    ("mono", "no", "large", "8C"): "0504000E11111F11",
    ("mono", "no", "large", "8D"): "050F15141E141517",
    ("mono", "no", "large", "90"): "0504000E010F110F",
    ("mono", "no", "large", "91"): "0500001A050F140B",
    ("mono", "no", "large", "A5"): "05000D1215150916",
    ("mono", "no", "large", "A9"): "0500000E1315190E",
    ("mono", "no", "small", "8C"): "040400040A0E0A00",
    ("mono", "no", "small", "8D"): "06001E283C282E00",
    ("mono", "no", "small", "90"): "050008000C140A00",
    ("mono", "no", "small", "91"): "060000341A2C1600",
    ("mono", "no", "small", "A5"): "06000A141C142800",
    ("mono", "no", "small", "A9"): "0600000A14142800",
    ("mono", "pl", "large", "8D"): "050E11111F111102",
    ("mono", "pl", "large", "91"): "05000E010F110F01",
    ("mono", "pl", "large", "95"): "051F101E10101F01",
    ("mono", "pl", "large", "99"): "05000E111F100E02",
    ("mono", "pl", "large", "9D"): "0508080C1808080F",
    ("mono", "pl", "large", "A1"): "050C04060C04040E",
    ("mono", "pl", "large", "A5"): "0502041F0204081F",
    ("mono", "pl", "large", "A9"): "050204001E04081F",
    ("mono", "pl", "large", "AC"): "0502040F100E011E",
    ("mono", "pl", "large", "AD"): "0504001F0204081F",
    ("mono", "pl", "large", "B0"): "0502040E100E011E",
    ("mono", "pl", "large", "B1"): "050004001E04081F",
    ("mono", "pl", "large", "B2"): "0502040E1110110E",
    ("mono", "pl", "large", "B3"): "050204000E10110E",
    ("mono", "pl", "large", "B4"): "0502041119151311",
    ("mono", "pl", "large", "B5"): "0502040016191111",
    ("mono", "pl", "small", "8D"): "05000C121E120400",
    ("mono", "pl", "small", "91"): "0500000C140A0200",
    ("mono", "pl", "small", "95"): "040E080C080E0200",
    ("mono", "pl", "small", "99"): "05000814180E0200",
    ("mono", "pl", "small", "9D"): "0500080C18080E00",
    ("mono", "pl", "small", "A1"): "04000C060C040400",
    ("mono", "pl", "small", "A5"): "0504081E04081E00",
    ("mono", "pl", "small", "A9"): "0504081E04081E00",
    ("mono", "pl", "small", "AC"): "0302040604020600",
    ("mono", "pl", "small", "AD"): "0508001E04081E00",
    ("mono", "pl", "small", "B0"): "0302040604020600",
    ("mono", "pl", "small", "B1"): "0508001E04081E00",
    ("mono", "pl", "small", "B2"): "0402040608080600",
    ("mono", "pl", "small", "B3"): "0402040006080600",
    ("mono", "pl", "small", "B4"): "050408121A161200",
    ("mono", "pl", "small", "B5"): "040204000C0A0A00",
    ("mono", "pt", "large", "8D"): "050A140E11111F11",
    ("mono", "pt", "large", "91"): "050A140E010F110F",
    ("mono", "pt", "large", "A5"): "050A140E1111110E",
    ("mono", "pt", "large", "A9"): "050A14000E11110E",
    ("mono", "pt", "small", "8D"): "050A140C121E1200",
    ("mono", "pt", "small", "91"): "050A14000C140A00",
    ("mono", "pt", "small", "A5"): "0614281C22221C00",
    ("mono", "pt", "small", "A9"): "061428001C221C00",
    ("mono", "sv", "large", "8C"): "0504000E11111F11",
    ("mono", "sv", "large", "90"): "0504000E010F110F",
    ("mono", "sv", "small", "8C"): "040400040A0E0A00",
    ("mono", "sv", "small", "90"): "050008000C140A00",
    ("mono", "sv", "small", "A4"): "0608141C22221C00",
    ("mono", "sv", "small", "A8"): "060814001C221C00",
}


def extract_fonts(data, family, language):
    if family == "mono":
        pattern = rb"\xe5\x29\x29\x29\x29\x29\x7c\x21(..)\x01(..)\xed\xb1\x28.\xe1\xc9\xe1\xb7\x21(..)\xed\x42\xed\x42\xef\x09\x40\xbf\xc9"
    else:
        # CSE selectors 75/76 branch backwards to CALL $0050;dw $0099/$009F.
        # Those application jump-table entries select a different app page.
        pattern = rb"\xe5\x78\x21(..)\x01(..)\xed\xb1\xe1\xc0\x21(..)\xed\x42\xed\x42\xef\x09\x40\xbf\xc9"
    tables = []
    for ordinal, match in enumerate(re.finditer(pattern, data, re.S)):
        kind = "small" if ordinal == 0 else "large"
        assert ordinal < 2
        page = match.start() // 0x4000 * 0x4000
        codes = pointer_offset(u16(match[1]), page)
        count = u16(match[2])
        last = pointer_offset(u16(match[3]), page)
        table = last - 2 * (count - 1)
        glyph_size = 8 if family == "mono" else 25 if kind == "small" else 28
        glyphs = []
        for i, code in enumerate(data[codes : codes + count]):
            pointer = u16(data[table + i * 2 : table + i * 2 + 2])
            offset = pointer_offset(pointer, page)
            glyph = data[offset : offset + glyph_size]
            assert len(glyph) == glyph_size
            glyphs.append(
                {
                    "code": f"{code:02X}",
                    "pointer_entry_offset": table + i * 2,
                    "pointer": pointer,
                    "offset": offset,
                    "bytes": glyph.hex().upper(),
                }
            )
        tables.append(
            {
                "kind": kind,
                "lookup_offset": match.start(),
                "lookup_bytes": match[0].hex().upper(),
                "page_offset": page,
                "codes_offset": codes,
                "count": count,
                "pointer_table_offset": table,
                "glyph_record_size": glyph_size,
                "glyph_format": (
                    "width byte plus seven raster rows"
                    if family == "mono"
                    else (
                        "width byte plus twelve two-byte raster rows"
                        if kind == "small"
                        else "fourteen rows of two bytes, five significant pixels (bits 7..3) per byte"
                    )
                ),
                "glyphs": glyphs,
            }
        )
    mapping, small_mapping = dict(FONT_MAP), dict(FONT_MAP)
    canonical, small = {}, {}
    for table in tables:
        assigned = LARGE_SEMANTICS if table["kind"] == "large" else SMALL_SEMANTICS
        selected = assigned.get(language, {})
        for glyph in table["glyphs"]:
            code = glyph["code"]
            if code in selected:
                # Unicode names are accepted only for byte-exact raster glyphs
                # inspected during the audit, not merely a language guess.
                expected = VERIFIED_GLYPHS.get((family, language, table["kind"], code))
                assert expected == glyph["bytes"], (
                    family,
                    language,
                    table["kind"],
                    code,
                    glyph["bytes"],
                )
                glyph["unicode"] = selected[code]
                (canonical if table["kind"] == "large" else small)[code] = selected[
                    code
                ]
    mapping.update(canonical)
    small_mapping.update(small)
    dispatch = []
    signature = re.search(rb"\x83\xc5\xd5", data)
    if signature:
        start = signature.end()
        for match in re.finditer(
            rb"\xfe([\x75-\x78])([\x28\xca])", data[start : start + 48]
        ):
            offset = start + match.start()
            selector = match[1][0]
            if match[2] == b"\x28":
                displacement = data[offset + 3]
                if displacement >= 128:
                    displacement -= 256
                target = offset + 4 + displacement
                instruction = data[offset : offset + 4]
            else:
                target = pointer_offset(
                    u16(data[offset + 3 : offset + 5]), offset // 0x4000 * 0x4000
                )
                instruction = data[offset : offset + 5]
            detail = {
                "selector": selector,
                "selector_hex": f"{selector:02X}",
                "dispatch_offset": offset,
                "dispatch_bytes": instruction.hex().upper(),
                "wrapper_offset": target,
            }
            if data[target : target + 3] == b"\xcd\x50\x00":
                vector = u16(data[target + 3 : target + 5])
                address = u16(data[vector : vector + 2])
                page = data[vector + 2]
                detail.update(
                    app_jump_table_entry_offset=vector,
                    app_jump_table_entry_bytes=data[vector : vector + 3].hex().upper(),
                    app_jump_target_offset=pointer_offset(address, page * 0x4000),
                )
            dispatch.append(detail)
    return {
        "font_mapping": mapping,
        "small_font_mapping": small_mapping,
        "canonical_font_overrides": canonical,
        "small_font_overrides": small,
        "native_font_tables": tables,
        "native_font_dispatch": dispatch,
        "font_mapping_source": FONT_SOURCE,
        "font_display_policy": "Canonical XML display follows the large program editor font; small-font-only semantic overrides are recorded separately.",
    }


def u16(data):
    return int.from_bytes(data, "little")


def decode_container(data):
    sections = []
    pos = 0
    while pos < len(data):
        assert data[pos : pos + 8] == b"**TIFL**"
        head = data[pos : pos + 78]
        size = int.from_bytes(head[74:78], "little")
        payload = data[pos + 78 : pos + 78 + size]
        decoded = bytearray()
        records = []
        segment = 0
        line_offset = 0
        if head[10] == 1:
            for line in payload.splitlines(keepends=True):
                if line.strip():
                    rec = bytes.fromhex(line.strip()[1:].decode("ascii"))
                    assert rec[0] + 5 == len(rec) and sum(rec) & 255 == 0
                    if rec[3] == 2:
                        segment = int.from_bytes(rec[4:-1], "big")
                    if rec[3] == 0:
                        records.append(
                            {
                                "decoded_offset": len(decoded),
                                "length": rec[0],
                                "segment": segment,
                                "address": int.from_bytes(rec[1:3], "big"),
                                "container_line_offset": pos + 78 + line_offset,
                            }
                        )
                        decoded.extend(rec[4:-1])
                line_offset += len(line)
        else:
            decoded.extend(payload)
        end = pos + 78 + size
        checksum = None
        if end < len(data) and data[end : end + 8] != b"**TIFL**":
            checksum = u16(data[end : end + 2]) == sum(payload) & 65535
            end += 2
        sections.append(
            {
                "header_offset": pos,
                "payload_offset": pos + 78,
                "decoded": bytes(decoded),
                "records": records,
                "container_checksum_valid": checksum,
            }
        )
        pos = end
    return sections


def pointer_offset(address, page):
    assert 0x4000 <= address < 0x8000, hex(address)
    return page + address - 0x4000


def token_index(key, cse=False):
    raw = bytes.fromhex(key)
    if len(raw) == 1:
        return raw[0] * 2
    prefix, index = raw
    bases = {
        0x5C: 0x200,
        0x5D: 0x214,
        0x60: 0x25E,
        0x61: 0x272,
        0xAA: 0x286,
        0x62: 0x29A,
        0x63: 0x314,
        0x7E: 0x386 if cse else 0x384,
        0xBB: 0x3AC if cse else 0x3AA,
        0xEF: 0x598 if cse else 0x596,
    }
    if prefix == 0x5E:
        return {0x10: 0x220, 0x20: 0x234, 0x40: 0x24C, 0x80: 0x258}[index & 0xF0] + (
            index & 15
        ) * 2
    return bases[prefix] + index * 2


def xml_keys(xml_path):
    keys = []
    for group in ET.parse(xml_path).getroot():
        for token in ([group] if group.tag == "token" else group):
            keys.append(
                (group.get("value")[1:] if group.tag == "two-byte" else "")
                + token.get("value")[1:]
            )
    return keys


def locate_hook(data):
    bound = list(
        re.finditer(rb"\x83\xe5\x21(..)\xb7\xed\x52\x38.\x21(..)\x19", data, re.S)
    )
    old = list(re.finditer(rb"\x83\xe5\x21(..)\x19", data, re.S))
    assert len(bound) + len(old) == 1
    match = (bound or old)[0]
    page = match.start() // 0x4000 * 0x4000
    base_addr = u16(match[2] if bound else match[1])
    base = pointer_offset(base_addr, page)
    tail = data[match.end() : match.end() + 90]
    special = re.match(
        rb"\x11(..)\xef\x0c\x40\x20\x0c\x3a(..)\xfe\x52\x20\x05\x21(..)\x18\x0a",
        tail,
        re.S,
    )
    special_info = None
    if special:
        special_info = {
            "token": "D7",
            "table_index": u16(special[1]) - base_addr,
            "condition": "cxCurApp == cxError (0x52); alternate Goto label for the error dialog, not a BASIC token spelling",
            "ram_address": u16(special[2]),
            "pointer": u16(special[3]),
            "record_offset": pointer_offset(u16(special[3]), page),
        }
        assert special_info["table_index"] == 0x1AE
    if bound:
        max_index = u16(match[1])
        size = max_index + 2
        bounds_source = "LD HL,max_index; OR A; SBC HL,DE; JR C returns without translation; max index inclusive"
    else:
        # This older hook has no bounds check. Its contiguous pointer table ends
        # at the first length-prefixed string, possibly the special Goto case.
        # Every app independently yields 0x548 bytes; do not read strings as
        # pointers beyond this boundary.
        candidates = [
            pointer_offset(u16(data[i : i + 2]), page)
            for i in range(base, base + 0x548, 2)
            if u16(data[i : i + 2])
        ]
        if special_info and special_info["record_offset"] >= base:
            candidates.append(special_info["record_offset"])
        size = min(candidates) - base
        assert size == 0x548, hex(size)
        max_index = size - 2
        bounds_source = "No app bounds check; 0x548-byte contiguous pointer table ends at first referenced length-prefixed string"
    return {
        "hook_offset": match.start(),
        "hook_page_offset": page,
        "hook_bytes": data[
            match.start() : base if 0 < base - match.start() < 100 else match.end() + 48
        ]
        .hex()
        .upper(),
        "pointer_table_address": base_addr,
        "pointer_table_offset": base,
        "pointer_table_bytes": size,
        "max_index": max_index,
        "bounds_source": bounds_source,
        "special_case": special_info,
    }


def extract(app, font_map, keys):
    path = ROOT / app["file"]
    source = path.read_bytes()
    assert hashlib.sha256(source).hexdigest() == app["sha256"]
    section = decode_container(source)[app["app_header_index"]]
    data = section["decoded"]
    fonts = extract_fonts(data, app["family"], app["language"])
    font_map = fonts["font_mapping"]
    h = app["headers"][app["app_header_index"]]
    assert hashlib.sha256(data).hexdigest() == h["decoded_payload_sha256"]
    lookup = locate_hook(data)
    version = h["app_version"]
    version_evidence = h.get("version_evidence")
    if version is None:
        matches = list(re.finditer(rb"V(4\.0)\x00", data))
        assert app["family"] == "cse" and len(matches) == 1
        version = matches[0][1].decode("ascii")
        version_evidence = {
            "source": "Embedded application UI version string",
            "offset": matches[0].start(),
            "bytes": matches[0][0].hex().upper(),
        }
    base, page = lookup["pointer_table_offset"], lookup["hook_page_offset"]
    unknown = collections.Counter()

    def record(pointer):
        offset = pointer_offset(pointer, page)
        size = data[offset]
        # The hook copies the length byte and string into a fixed RAM buffer.
        limit = 25 if app["family"] == "cse" else 15
        assert 0 < size <= limit, (app["file"], hex(pointer), size)
        native = data[offset + 1 : offset + 1 + size]
        assert len(native) == size
        for n in native:
            if f"{n:02X}" not in font_map:
                unknown[f"{n:02X}"] += 1
        return {
            "ti_ascii": native.hex().upper(),
            "display": "".join(
                font_map.get(f"{n:02X}", f"[0x{n:02X}]") for n in native
            ),
            "display_mapping_status": "Native large-font glyphs with byte-validated locale overrides",
            "record_pointer": pointer,
            "record_offset": offset,
            "record_bytes": data[offset : offset + size + 1].hex().upper(),
            "length": size,
        }

    tokens, zero, beyond = {}, [], []
    for key in keys:
        index = token_index(key, app["family"] == "cse")
        if index > lookup["max_index"]:
            beyond.append(key)
            continue
        entry = base + index
        pointer = u16(data[entry : entry + 2])
        if pointer == 0:
            zero.append(key)
            continue
        tokens[key] = {
            **record(pointer),
            "table_index": index,
            "pointer_entry_offset": entry,
            "pointer_bytes": data[entry : entry + 2].hex().upper(),
        }
    if lookup["special_case"]:
        lookup["special_case"]["record"] = record(lookup["special_case"]["pointer"])
    # Record lengths are part of the app ABI, including vendor mistakes.  In
    # several apps a length extends into the next record's length byte.  Keep
    # those bytes unchanged and expose the overlap instead of silently fixing
    # the data or treating the extra glyph as our extraction error.
    records_by_offset = collections.defaultdict(list)
    for key, rec in tokens.items():
        records_by_offset[rec["record_offset"]].append(key)
    overlap_warnings = []
    offsets = sorted(records_by_offset)
    for offset, next_offset in zip(offsets, offsets[1:]):
        token = records_by_offset[offset][0]
        rec = tokens[token]
        if offset + 1 + rec["length"] > next_offset:
            warning = {
                "tokens": records_by_offset[offset],
                "record_offset": offset,
                "record_bytes": rec["record_bytes"],
                "next_record_offset": next_offset,
                "next_tokens": records_by_offset[next_offset],
                "overlapping_byte_count": offset + 1 + rec["length"] - next_offset,
            }
            overlap_warnings.append(warning)
            for key in records_by_offset[offset]:
                tokens[key]["native_length_overlaps_next_record"] = warning
    gate = re.search(
        rb"\xef(?:\x6f|\x5a)\x4c\x67\x68\x11(..)\xb7\xed\x52\xc9", data, re.S
    )
    gate_info = (
        {
            "offset": gate.start(),
            "bytes": gate[0].hex().upper(),
            "minimum_os": f"{gate[1][1]}.{gate[1][0]:02d}",
            "semantics": "GetBaseVer returns binary A=major B=minor; compare HL=(A<<8)|B to DE; caller accepts NC",
        }
        if gate
        else None
    )
    return {
        "sha256": app["sha256"],
        "file": app["file"],
        "language": app["language"],
        "app_version": version,
        "app_version_evidence": version_evidence,
        "target_model": app["target_model"],
        "family": app["family"],
        "app_header_index": app["app_header_index"],
        "app_header_offset": section["header_offset"],
        "app_container_checksum_valid": section["container_checksum_valid"],
        "decoded_payload_sha256": h["decoded_payload_sha256"],
        "decoded_payload_size": len(data),
        "lookup": lookup,
        "minimum_os_gate": gate_info,
        "tokens": tokens,
        "native_record_overlap_warnings": overlap_warnings,
        "zero_pointer_fallback_tokens": zero,
        "beyond_table_tokens": beyond,
        "fallback_semantics": "Nonzero pointer copies length-prefixed TI-ASCII string to RAM. Zero pointer returns with original HL unchanged so OS spelling remains. Bounded hooks return for DE above inclusive max. Old hooks have no explicit upper-bound check.",
        "unknown_base_font_bytes": dict(unknown),
        **fonts,
        "intel_hex_records": section["records"],
    }


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "catalog.json")
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "extraction/z80")
    args = parser.parse_args()
    ROOT = args.catalog.resolve().parent
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    font_map = dict(FONT_MAP)
    apps = [
        a
        for a in catalog["apps"]
        if a["family"] in ("mono", "cse") and a["category"] == "os-localization"
    ]
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    keys = xml_keys(args.xml)
    for app in apps:
        item = extract(app, font_map, keys)
        output = args.output / (app["sha256"] + ".json")
        output.write_text(
            json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        results.append(
            {
                k: item[k]
                for k in [
                    "sha256",
                    "file",
                    "language",
                    "app_version",
                    "target_model",
                    "family",
                    "decoded_payload_sha256",
                    "minimum_os_gate",
                    "unknown_base_font_bytes",
                ]
            }
            | {
                "output": output.name,
                "token_count": len(item["tokens"]),
                "token_table_sha256": hashlib.sha256(
                    json.dumps(
                        {k: v["ti_ascii"] for k, v in item["tokens"].items()},
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
            }
        )
        print(
            item["family"],
            item["language"],
            item["app_version"],
            app["sha256"][:12],
            len(item["tokens"]),
            item["unknown_base_font_bytes"],
        )
    (args.output / "manifest.json").write_text(
        json.dumps({"apps": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
