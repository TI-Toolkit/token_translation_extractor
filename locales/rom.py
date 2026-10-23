#!/usr/bin/env python3
"""Extract native token spellings from pinned TI-83-family ROMs.

The small interpreter follows each ROM's actual GetTokString dispatch, including
TI-76.fr's reserved-token clamp and the French contextual error-dialog Goto.
Only the contiguous native pointer tables are enumerated: dispatch aliases and
out-of-range reads do not establish token availability. No emulator, calculator
ROM, or third-party Python package is bundled with this extractor.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

try:
    from .font import FONT_MAP, FONT_SOURCE, decode_font
except ImportError:
    from font import FONT_MAP, FONT_SOURCE, decode_font

REPO = Path(__file__).resolve().parents[1]
SOURCES = Path(__file__).with_name("rom-sources.json")
# Prefixes recognized by the native TI-83 token parser. 5E uses four bit-selected
# subgroups; these selectors are preserved as token-byte values, not row numbers.
GROUP_SELECTORS = [
    (0, 0),
    (0x5C, 0),
    (0x5D, 0),
    (0x5E, 0x10),
    (0x5E, 0x20),
    (0x5E, 0x40),
    (0x5E, 0x80),
    (0x60, 0),
    (0x61, 0),
    (0xAA, 0),
    (0x62, 0),
    (0x63, 0),
    (0x7E, 0),
    (0xBB, 0),
]


def u16(data, offset):
    return int.from_bytes(data[offset : offset + 2], "little")


def physical(address, page):
    """These routines and all their token records live in one banked page."""
    if not 0x4000 <= address < 0x8000:
        raise ValueError(f"Native banked address outside page: {address:04X}")
    return page + address - 0x4000


def locate_dispatch(data):
    hits = [m.start() for m in re.finditer(b"\x6b\x26\x00\x7a\xb7", data)]
    if len(hits) != 1:
        raise ValueError(f"Expected one GetTokString dispatcher, found {hits}")
    return hits[0]


def lookup(data, start, prefix, suffix, context=0):
    """Execute the bounded native dispatch through RET, rejecting unknown code.

    DE is the two-byte token (D=0 for a single byte). RAM 815B is the native
    current context selector; 'R' selects the French error-dialog Goto label.
    The return value HL addresses the native length byte, as GetTokString does.
    """
    pc, hl, de, a = start, 0, prefix << 8 | suffix, 0
    carry = zero = False
    page = start // 0x4000 * 0x4000
    entry = pointer = base = None
    trace = []

    def immediate(count=1):
        nonlocal pc
        value = int.from_bytes(data[pc : pc + count], "little")
        pc += count
        return value

    for _ in range(200):
        at, op = pc, immediate()
        if op == 0x6B:  # LD L,E
            hl = hl & 0xFF00 | de & 0xFF
        elif op == 0x26:  # LD H,n
            hl = hl & 0xFF | immediate() << 8
        elif op == 0x2E:  # LD L,n (TI-76.fr reserved-BB clamp)
            hl = hl & 0xFF00 | immediate()
        elif op == 0x7A:
            a = de >> 8
        elif op == 0x7D:
            a = hl & 0xFF
        elif op == 0xB7:
            zero, carry = a == 0, False
        elif op in (0xFE, 0xBB):
            operand = immediate() if op == 0xFE else de & 0xFF
            zero, carry = a == operand, a < operand
        elif op == 0x11:
            de = immediate(2)
        elif op == 0x21:
            hl = immediate(2)
        elif op == 0x3E:
            a = immediate()
        elif op == 0x3A:
            address = immediate(2)
            if address != 0x815B:
                raise ValueError(f"Unexpected native RAM read at {at:X}")
            a = context
        elif op == 0xCB:
            sub = immediate()
            bit = sub >> 3 & 7
            if sub & 0xC0 == 0x40 and sub & 7 == 7:  # BIT n,A
                zero = not a & 1 << bit
            elif sub & 0xC0 == 0x80 and sub & 7 == 5:  # RES n,L
                hl &= ~(1 << bit)
            else:
                raise ValueError(f"Unexpected CB opcode {sub:02X} at {at:X}")
        elif op in (0x18, 0x20, 0x28, 0x30, 0x38):
            displacement = immediate()
            if displacement >= 128:
                displacement -= 256
            if {0x18: True, 0x20: not zero, 0x28: zero, 0x30: not carry, 0x38: carry}[
                op
            ]:
                pc += displacement
        elif op == 0x29:
            base = de
            hl = hl * 2 & 0xFFFF
        elif op == 0x19:
            hl = hl + de & 0xFFFF
        elif op == 0x5E:
            entry = physical(hl, page)
            pointer = u16(data, entry)
            de = de & 0xFF00 | data[entry]
        elif op == 0x23:
            hl = hl + 1 & 0xFFFF
        elif op == 0x56:
            de = de & 0xFF | data[physical(hl, page)] << 8
        elif op == 0xEB:
            de, hl = hl, de
        elif op == 0xC9:
            trace.append(at)
            return {
                "length_pointer": hl,
                "table_pointer": pointer,
                "pointer_entry_offset": entry,
                "table_base": base,
                "instruction_offsets": trace,
            }
        else:
            raise ValueError(f"Unsupported GetTokString opcode {op:02X} at {at:X}")
        trace.append(at)
    raise ValueError("Native GetTokString dispatcher exceeded instruction bound")


def native_record(data, result, page, mapping):
    offset = physical(result["length_pointer"], page)
    length = data[offset]
    if not 0 < length <= 25:
        raise ValueError(f"Invalid native token length {length} at {offset:X}")
    raw = data[offset + 1 : offset + 1 + length]
    row = {
        "ti_ascii": raw.hex().upper(),
        "display": decode_font(raw, mapping),
        "length": length,
        "length_pointer": result["length_pointer"],
        "length_file_offset": offset,
        "length_prefixed_bytes": data[offset : offset + length + 1].hex().upper(),
        "gettokstring_instruction_offsets": result["instruction_offsets"],
    }
    if result["pointer_entry_offset"] is not None:
        pointer = result["table_pointer"]
        row.update(
            record_pointer=pointer,
            record_offset=offset - 1,
            record_id=data[offset - 1],
            record_bytes=data[offset - 1 : offset + length + 1].hex().upper(),
            pointer_entry_offset=result["pointer_entry_offset"],
            pointer_bytes=data[
                result["pointer_entry_offset"] : result["pointer_entry_offset"] + 2
            ]
            .hex()
            .upper(),
        )
    return row


def extract_fonts(data, mapping, used):
    # LD DE,font; ADD HL,DE; LD DE,801B; CALL Mov8B; LD HL,801B; RET.
    # The large renderer consumes the seven row bytes at base+8*code. The byte
    # immediately before those rows is the width, not the first row.
    matches = list(
        re.finditer(rb"\x11(..)\x19\x11\x1b\x80\xcd..\x21\x1b\x80\xc9", data)
    )
    if len(matches) != 1:
        raise ValueError("Cannot uniquely identify the native large font loader")
    match = matches[0]
    base = physical(u16(match[1], 0), match.start() // 0x4000 * 0x4000)
    glyphs = {}
    for code in sorted(used):
        offset = base + code * 8
        glyphs[f"{code:02X}"] = {
            "unicode": mapping[f"{code:02X}"],
            "rows_file_offset": offset,
            "width": data[offset - 1],
            "rows": data[offset : offset + 7].hex().upper(),
        }
    return {
        "loader_file_offset": match.start(),
        "loader_bytes": match[0].hex().upper(),
        "rows_base_file_offset": base,
        "glyph_stride": 8,
        "row_count": 7,
        "glyphs": glyphs,
        "unicode_policy": "Human Unicode labels use the existing TI font map; exact native program-editor raster rows are preserved for every used byte.",
    }


def extract(path, source):
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != source["sha256"] or len(data) != source["size"]:
        raise ValueError(f"ROM differs from pinned input: {path}")
    start = locate_dispatch(data)
    page = start // 0x4000 * 0x4000
    mapping = dict(FONT_MAP)
    groups = []
    for prefix, selector in GROUP_SELECTORS:
        result = lookup(data, start, prefix, selector)
        groups.append(
            {
                "prefix": prefix,
                "selector": selector,
                "base": physical(result["table_base"], page),
            }
        )
    groups.sort(key=lambda row: row["base"])
    # The contiguous table region ends where its first actual record starts.
    first_record = min(
        u16(data, i)
        for i in range(groups[0]["base"], groups[0]["base"] + 512, 2)
        if u16(data, i)
    )
    table_end = physical(first_record, page)
    tokens, nulls, placeholders, tables = {}, [], {}, []
    # TI-76.fr replaces its unused zero-byte entry and reserved slots by one
    # shared question-mark record. AF is a real question-mark token and has its
    # own distinct record; do not mistake that legitimate spelling for a hole.
    placeholder_pointer = u16(data, groups[0]["base"]) or None
    for index, group in enumerate(groups):
        end = groups[index + 1]["base"] if index + 1 < len(groups) else table_end
        if (end - group["base"]) % 2:
            raise ValueError("Odd native pointer table length")
        count = (end - group["base"]) // 2
        if group["prefix"] == 0 and count != 256:
            raise ValueError("Single-byte table does not contain 256 entries")
        table = {
            **group,
            "entry_count": count,
            "pointer_bytes": data[group["base"] : end].hex().upper(),
        }
        table["prefix"] = f"{group['prefix']:02X}"
        table["selector"] = f"{group['selector']:02X}"
        tables.append(table)
        for ordinal in range(count):
            prefix, suffix = group["prefix"], group["selector"] + ordinal
            key = f"{prefix:02X}{suffix:02X}" if prefix else f"{suffix:02X}"
            result = lookup(data, start, prefix, suffix)
            expected = group["base"] + 2 * ordinal
            if result["pointer_entry_offset"] != expected:
                raise ValueError(f"Native dispatch disagrees with table row {key}")
            if result["table_pointer"] == 0:
                nulls.append(key)
                continue
            record = native_record(data, result, page, mapping)
            if result["table_pointer"] == placeholder_pointer:
                record["placeholder"] = True
                placeholders[key] = record
            else:
                tokens[key] = record
    contextual = []
    for key in tokens:
        raw = bytes.fromhex(key)
        prefix, suffix = (raw[0], raw[1]) if len(raw) == 2 else (0, raw[0])
        alternate = lookup(data, start, prefix, suffix, ord("R"))
        if alternate["length_pointer"] != tokens[key]["length_pointer"]:
            contextual.append(
                {
                    "token": key,
                    "ram_context_address": "815B",
                    "ram_context_value": "52",
                    "scope": "Error-dialog context; not canonical BASIC token spelling",
                    **native_record(data, alternate, page, mapping),
                }
            )
    version_matches = list(
        re.finditer(
            rb"(?:Enter Self Test\?|Enter l'auto-tst)([0-9][0-9A-Za-z.]*)", data
        )
    )
    versions = [
        {
            "file_offset": m.start(1),
            "version": m[1].decode("ascii"),
            "bytes": m[1].hex().upper(),
        }
        for m in version_matches
    ]
    if not versions or {v["version"] for v in versions} != {source["embedded_version"]}:
        raise ValueError("Embedded native version differs from pinned source")
    used = {byte for row in tokens.values() for byte in bytes.fromhex(row["ti_ascii"])}
    return {
        "kind": "rom",
        "family": "ti83",
        "language": source["language"],
        "model": source["model"],
        "os_version": source["os_version"],
        "source_file": path.name,
        "source_filename": path.name,
        "sha256": digest,
        "size": len(data),
        "version_evidence": versions,
        "gettokstring_file_offset": start,
        "token_page_file_offset": page,
        "gettokstring_bytes": data[
            start : max(
                max(row["gettokstring_instruction_offsets"]) for row in tokens.values()
            )
            + 1
        ]
        .hex()
        .upper(),
        "token_tables": tables,
        "tokens": tokens,
        "null_pointer_tokens": nulls,
        "placeholder_records": placeholders,
        "contextual_strings": contextual,
        "font_mapping": mapping,
        "canonical_font_overrides": {},
        "font_mapping_source": {
            **FONT_SOURCE,
            "scope": "Unicode labels reused from existing TI-83 token correspondences; exact native large-font rasters retained below. The four pinned ROMs have equivalent used glyph pixels.",
        },
        "native_large_font": extract_fonts(data, mapping, used),
        "scope": "Native bounded GetTokString pointer tables. Null pointers and shared reserved-slot question-mark placeholders are not canonical token spellings; AF is a real question-mark token. This proves table availability and text, not successful execution of every command.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=SOURCES)
    parser.add_argument(
        "--rom-directory",
        "--rom-dir",
        type=Path,
        required=True,
        help="Directory containing the four pinned local ROM files",
    )
    parser.add_argument("--output", type=Path, default=REPO / "research/rom-locales")
    args = parser.parse_args()
    sources = json.loads(args.sources.read_text(encoding="utf-8"))
    results = [
        extract(args.rom_directory / item["filename"], item) for item in sources["roms"]
    ]
    args.output.mkdir(parents=True, exist_ok=True)
    for item in results:
        (args.output / (item["sha256"] + ".json")).write_text(
            json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (args.output / "index.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for item in results:
        print(
            f"{item['model']} {item['os_version']}: {len(item['tokens'])} token records, "
            f"{len(item['null_pointer_tokens'])} null, {len(item['placeholder_records'])} placeholders"
        )


if __name__ == "__main__":
    main()
