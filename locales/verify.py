"""Verify effective locale maps, English preservation, ranges and unique names."""

import argparse
import bisect
import collections
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from .token_api import OsVersion, OsVersions, validate
from .font import FONT_MAP, decode_font
from .merge import load_extracted, profile_sources


def bound(element):
    if element is None:
        return OsVersions.LATEST
    return OsVersion.from_element(element)


def tokens(root, prefix=""):
    result = {}
    for element in root:
        value = prefix + element.attrib["value"][1:]
        if element.tag == "two-byte":
            result.update(tokens(element, value))
        else:
            result[value] = element
    return result


def active(element, point):
    matches = [
        v for v in element if bound(v.find("since")) <= point < bound(v.find("until"))
    ]
    assert len(matches) <= 1, (element.attrib, point, "overlapping ranges")
    return {l.attrib["code"]: l for l in matches[0].findall("lang")} if matches else {}


def representation(element):
    return element.attrib, [(child.tag, child.text) for child in element]


def verify(baseline, sheet, timeline, apps):
    before, after = tokens(baseline), tokens(sheet)
    assert before.keys() == after.keys()
    assert baseline.attrib == sheet.attrib
    assert validate(sheet) == len(before)
    points = set()
    for token, element in after.items():
        assert element.attrib == before[token].attrib
        assert representation(element[0].find("since")) == representation(
            before[token][0].find("since")
        )
        for i, item in enumerate(element):
            assert bound(item.find("since")) < bound(item.find("until")), token
            if i:
                assert bound(element[i - 1].find("until")) <= bound(
                    item.find("since")
                ), token
            points.add(bound(item.find("since")))
            if item.find("until") is not None:
                points.add(bound(item.find("until")))
    for model, versions in {
        "TI-82": ["1.0", "9.0", "10.0", "19.0"],
        "TI-83": ["0.01013", "1.010", "1.0200", "1.10", "1.10002"],
        "TI-82ST": ["1.11"],
        "TI-82ST.fr": ["1.11fr7"],
        "TI-76.fr": ["1.00fr5"],
        "TI-83+": ["0.103", "1.00", "1.03", "1.10", "1.15", "1.16"],
        "TI-84+": ["2.30", "2.31", "2.43", "2.53", "2.55"],
        "TI-84+CSE": ["4.0", "4.2"],
        "TI-84+CE": [
            "5.0.0",
            "5.0.1",
            "5.1.0",
            "5.1.5",
            "5.2.0",
            "5.2.2",
            "5.3.0",
            "5.5.0",
            "5.5.5",
            "5.6.0",
            "5.7.0",
            "5.8.4",
        ],
    }.items():
        points.update(OsVersion(model, version) for version in versions)
        points.add(OsVersion(model, "latest"))
    profiles = timeline["profiles"]
    profile_versions = [OsVersion(p["model"], p["os_version"]) for p in profiles]
    languages = {lang for p in profiles for lang in profile_sources(p)}
    checks = collections.Counter()
    for point in sorted(points):
        position = bisect.bisect_right(profile_versions, point) - 1
        selected = profile_sources(profiles[position]) if position >= 0 else {}
        for digest in selected.values():
            if apps[digest].get("kind") == "rom":
                assert set(apps[digest]["tokens"]) == {
                    token for token, element in before.items() if active(element, point)
                }, (point, digest, "ROM token availability differs")
        names = {lang: {} for lang in languages | {"en"}}
        for token, element in before.items():
            old, new = active(element, point), active(after[token], point)
            assert bool(old) == bool(new), (token, point, "availability changed")
            if not old:
                continue
            assert representation(old["en"]) == representation(new["en"]), (
                token,
                point,
                "English changed",
            )
            checks["english_records"] += 1
            for lang in languages | {"en"}:
                actual = new.get(lang, new["en"])
                expected = old["en"].attrib
                raw, display = expected["ti-ascii"], expected["display"]
                if lang in selected:
                    app = apps[selected[lang]]
                    native = app["tokens"].get(token)
                    if native and native["ti_ascii"] != "3F":
                        raw, display = native["ti_ascii"], native["display"]
                    else:
                        data = bytes.fromhex(raw)
                        # Independently detect semantic glyph changes by comparing
                        # complete maps, not the merger's override metadata field.
                        if any(
                            app["font_mapping"].get(f"{b:02X}")
                            != FONT_MAP.get(f"{b:02X}")
                            for b in data
                        ):
                            display = decode_font(data, app["font_mapping"])
                assert (actual.attrib["ti-ascii"], actual.attrib["display"]) == (
                    raw,
                    display,
                ), (token, point, lang, actual.attrib, raw, display)
                checks["effective_locale_records"] += 1
                for child in actual:
                    name = child.text
                    assert name not in names[lang] or names[lang][name] == token, (
                        point,
                        lang,
                        name,
                        names[lang].get(name),
                        token,
                    )
                    names[lang][name] = token
    return {"tokens": len(before), "version_points": len(points), **checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--sheet", required=True, type=Path)
    parser.add_argument("--timeline", required=True, type=Path)
    parser.add_argument("--extracted", required=True, type=Path, nargs="+")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(
        ET.parse(args.baseline).getroot(),
        ET.parse(args.sheet).getroot(),
        json.loads(args.timeline.read_text(encoding="utf-8")),
        load_extracted(args.extracted),
    )
    text = json.dumps(result, indent=2) + "\n"
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
