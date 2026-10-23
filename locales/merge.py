"""Merge extracted app and ROM snapshots into format 1.0 token-version ranges.

The timeline is an explicit, reviewed input: application revisions must never be
silently interpreted as OS versions. Existing English data is copied unchanged.
Run with ``python -m locales.merge --help``.
"""

import argparse
import bisect
import collections
import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from .token_api import OsVersion, OsVersions
from .font import decode_font


def profile_sources(profile):
    """Select either built-in ROM data or an installable app for each locale."""
    apps, roms = profile.get("apps", {}), profile.get("roms", {})
    if apps.keys() & roms.keys():
        raise ValueError("A locale cannot select both a ROM and an app")
    return apps | roms


def bound(element):
    if element is None:
        return OsVersions.LATEST
    return OsVersion.from_element(element)


def token_elements(root, prefix=""):
    for child in root:
        if child.tag == "token":
            yield prefix + child.attrib["value"][1:], child
        elif child.tag == "two-byte":
            yield from token_elements(child, prefix + child.attrib["value"][1:])


def english(element, version):
    for item in element:
        start, end = bound(item.find("since")), bound(item.find("until"))
        if start <= version < end:
            lang = item.find("lang[@code='en']")
            return {
                "ti_ascii": lang.attrib["ti-ascii"],
                "display": lang.attrib["display"],
                "accessible": lang.findtext("accessible"),
                "variants": [v.text for v in lang.findall("variant")],
            }
    return None


SYMBOLS = {
    "►": ">",
    "→": "->",
    "≤": "<=",
    "≥": ">=",
    "≠": "!=",
    "⁻¹": "^-1",
    "⁻": "~",
    "ᴛ": "T",
    "ʳ": "^r",
    "ᵀ": "^T",
    "₀": "0",
    "₁": "1",
    "₂": "2",
    "₃": "3",
    "₄": "4",
    "₅": "5",
    "₆": "6",
    "₇": "7",
    "₈": "8",
    "₉": "9",
    "²": "^2",
    "³": "^3",
    "ˣ": "^x",
    "ᴇ": "E",
    "ʟ": "L",
    "𝑛": "n",
    "𝑖": "i",
    "𝑒": "e",
    "𝙵": "F",
    "𝗡": "N",
    "χ": "chi",
    "θ": "theta",
    "Σ": "Sigma",
    "σ": "sigma",
    "μ": "mu",
    "ρ": "rho",
    "π": "pi",
    "λ": "lambda",
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "Δ": "Delta",
    "δ": "delta",
    "ε": "epsilon",
    "τ": "tau",
    "Φ": "Phi",
    "Ω": "Omega",
    "x̄": "xbar",
    "ȳ": "ybar",
    "p̂": "phat",
    "∫": "integral",
    "∠": "angle",
    "√": "sqrt",
    "…": "...",
    "﹢": "+",
    "ᴜ": "u",
    "ᴠ": "v",
    "ᴡ": "w",
    "⁄": "/",
    "⬚": "[]",
    "□": "square",
    "␣": "space",
    "↑": "^",
    "↓": "v",
    "◄": "<",
    "🠿": "[bolddownarrow]",
}

# ANOVA result variables share their on-calculator labels. Follow the English
# sheet's bracketed factor/error distinction instead of accepting ambiguous names.
STAT_CONTEXT = {
    "6227": "",
    "6237": "factor",
    "6238": "factor",
    "6239": "factor",
    "623A": "error",
    "623B": "error",
    "623C": "error",
}


def accessible(display):
    """Keep Latin-1 accents; transliterate other letters and math notation."""
    if len(display) == 1 and ord(display) > 255 and display.isalpha():
        # A lone Polish/Hungarian letter must not collide with ASCII A, O, etc.
        name = unicodedata.name(display)
        if "LATIN " in name:
            name = (
                name.replace("LATIN SMALL LETTER ", "")
                .replace("LATIN CAPITAL LETTER ", "")
                .replace(" WITH ", "-")
            )
            name = name.lower().replace(" ", "-")
            if display.isupper():
                name = name[0].upper() + name[1:]
            return "[" + name + "]"
    text = display
    for symbol in sorted(SYMBOLS, key=len, reverse=True):
        text = text.replace(symbol, SYMBOLS[symbol])
    text = text.replace("ł", "l").replace("Ł", "L")
    result = ""
    for char in text:
        if ord(char) <= 255:
            result += char
        else:
            parts = unicodedata.normalize("NFKD", char)
            plain = "".join(
                c for c in parts if ord(c) <= 255 and not unicodedata.combining(c)
            )
            if not plain:
                raise ValueError(f"No accessible spelling for {char!r} in {display!r}")
            result += plain
    return result


def translated(token, en, app):
    record = app["tokens"].get(token)
    if record and not record.get("placeholder"):
        native, display = record["ti_ascii"], record["display"]
    else:
        native, display = en["ti_ascii"], en["display"]
        raw = bytes.fromhex(native)
        overrides = app.get("canonical_font_overrides", app.get("font_overrides", {}))
        if any(f"{b:02X}" in overrides for b in raw):
            display = decode_font(raw, app["font_mapping"])
    if native == en["ti_ascii"] and display == en["display"]:
        return None
    name = en["accessible"] if display == en["display"] else accessible(display)
    if token in STAT_CONTEXT and display != en["display"]:
        name = f"[{STAT_CONTEXT[token]}{name}]"
    return {"ti_ascii": native, "display": display, "accessible": name, "variants": []}


def load_extracted(paths):
    apps = {}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            rows = data
        elif "tokens" in data:
            rows = [data]
        else:
            rows = [
                json.loads((path.parent / row["output"]).read_text(encoding="utf-8"))
                for row in data["apps"]
            ]
        for row in rows:
            apps[row["sha256"]] = row
    return apps


def snapshot(elements, version, profile, apps, overrides, report):
    output = {}
    en = {
        t: value
        for t, element in elements.items()
        if (value := english(element, version))
    }
    for lang, digest in profile_sources(profile).items():
        app = apps[digest]
        assert app["language"] == lang
        if app.get("kind") == "rom" and app["tokens"].keys() != en.keys():
            raise ValueError(
                "ROM token availability differs from the English baseline; "
                "review explicit availability ranges before merging"
            )
        changes = {
            t: value for t, base in en.items() if (value := translated(t, base, app))
        }
        names = collections.defaultdict(set)
        for token, base in en.items():
            if token not in changes:
                for name in [base["accessible"], *base["variants"]]:
                    names[name].add(token)
        # Accessible overrides resolve actual ambiguous on-calculator spellings;
        # no raw native bytes or displays are changed to make names unique.
        for token, value in changes.items():
            option = overrides.get(lang, {}).get(token)
            if isinstance(option, dict):
                option = option.get(profile["model"], option.get("default"))
            if option:
                value["accessible"] = option
            names[value["accessible"]].add(token)
        collisions = {
            name: sorted(tokens) for name, tokens in names.items() if len(tokens) > 1
        }
        if collisions:
            report["collisions"].append(
                {
                    "profile": profile["model"],
                    "version": {"model": version.model, "os_version": version.version},
                    "language": lang,
                    "names": collisions,
                }
            )
        display_counts = collections.Counter(
            value["display"] for value in changes.values()
        )
        for token, value in changes.items():
            candidate = value["display"]
            if (
                candidate != value["accessible"]
                and display_counts[candidate] == 1
                and not names.get(candidate, set()) - {token}
            ):
                value["variants"].append(candidate)
                names[candidate].add(token)
        output[lang] = changes
    return {
        token: {
            lang: changes[token] for lang, changes in output.items() if token in changes
        }
        for token in en
    }


def render_bound(tag, value, indent):
    return (
        f"{indent}<{tag}>\n{indent}\t<model>{value.model}</model>\n"
        f"{indent}\t<os-version>{value.version}</os-version>\n{indent}</{tag}>"
    )


def render_lang(code, value, indent):
    # Keep spaces explicit in generated language fields, including trailing ones.
    entities = {" ": "&#032;"}
    attrs = (
        f"code={quoteattr(code)} ti-ascii={quoteattr(value['ti_ascii'])} "
        f"display={quoteattr(value['display'], entities)}"
    )
    result = [
        f"{indent}<lang {attrs}>",
        f"{indent}\t<accessible>{escape(value['accessible'], entities)}</accessible>",
    ]
    result += [
        f"{indent}\t<variant>{escape(v, entities)}</variant>" for v in value["variants"]
    ]
    return "\n".join([*result, f"{indent}</lang>"])


def merge(source, timeline, apps):
    root = ET.fromstring(source)
    expected_hash = timeline.get("baseline_sha256")
    actual_hash = hashlib.sha256(source.encode()).hexdigest()
    if expected_hash and expected_hash != actual_hash:
        raise ValueError("Baseline differs from the reviewed source in the timeline")
    if any(lang.attrib["code"] != "en" for lang in root.iter("lang")):
        raise ValueError("The merge requires the reviewed English-only baseline")
    elements = dict(token_elements(root))
    profiles = timeline["profiles"]
    profile_versions = [OsVersion(p["model"], p["os_version"]) for p in profiles]
    assert profile_versions == sorted(set(profile_versions))
    points = {bound(v.find("since")) for e in elements.values() for v in e}
    points |= {
        bound(v.find("until"))
        for e in elements.values()
        for v in e
        if v.find("until") is not None
    }
    points |= set(profile_versions)
    all_points = sorted(points)
    report = {
        "baseline_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "collisions": [],
        "localized_entries": 0,
        "split_ranges": 0,
    }
    snapshots = {}
    for point in all_points:
        index = bisect.bisect_right(profile_versions, point) - 1
        profile = profiles[index] if index >= 0 else {"apps": {}}
        snapshots[point] = snapshot(
            elements,
            point,
            profile,
            apps,
            timeline.get("accessible_overrides", {}),
            report,
        )
    if report["collisions"]:
        return None, report
    tokens = iter(elements)
    pattern = re.compile(
        r"(?P<i>\t+)<token [^>]+>\n(?P<body>.*?)\n(?P=i)</token>", re.S
    )

    def replace(match):
        token = next(tokens)
        element = elements[token]
        vi = match["i"] + "\t"
        ci = vi + "\t"
        versions = list(
            re.finditer(
                re.escape(vi) + r"<version>\n(.*?)\n" + re.escape(vi) + r"</version>",
                match["body"],
                re.S,
            )
        )
        assert len(versions) == len(element)
        rendered = []
        for raw, version in zip(versions, element):
            start, end = bound(version.find("since")), bound(version.find("until"))
            cuts = [
                start,
                *(p for p in all_points if start < p < end),
            ]
            intervals = []
            for a, b in zip(cuts, [*cuts[1:], end]):
                translations = snapshots[a].get(token, {})
                if intervals and intervals[-1][2] == translations:
                    intervals[-1] = intervals[-1][0], b, translations
                else:
                    intervals.append((a, b, translations))
            if len(intervals) == 1 and not intervals[0][2]:
                rendered.append(raw.group())
                continue
            en_match = re.search(
                re.escape(ci) + r'<lang code="en".*?</lang>', raw.group(), re.S
            )
            assert en_match
            en_raw = re.sub(r"(?m)^ +(?=\t)", "", en_match.group())

            # Original spelling of bounds is retained whenever that bound survives.
            def as_bound(point):
                if point == start:
                    return start
                if point == end:
                    return end
                return point

            for a, b, translations in intervals:
                parts = [f"{vi}<version>", render_bound("since", as_bound(a), ci)]
                if b != OsVersions.LATEST:
                    parts.append(render_bound("until", as_bound(b), ci))
                parts.append(en_raw)
                parts += [
                    render_lang(lang, value, ci)
                    for lang, value in sorted(translations.items())
                ]
                parts.append(f"{vi}</version>")
                rendered.append("\n".join(parts))
                report["localized_entries"] += len(translations)
            report["split_ranges"] += len(intervals) - 1
        return match.group().replace(match["body"], "\n".join(rendered), 1)

    result = pattern.sub(replace, source)
    # Existing mixed space/tab indentation can become a newly added diff line
    # when surrounding ranges expand; normalize indentation without changing text.
    result = re.sub(r"(?m)^ +(?=\t)", "", result)
    assert next(tokens, None) is None
    after = dict(token_elements(ET.fromstring(result)))
    assert after.keys() == elements.keys()
    for point in all_points:
        for token, element in elements.items():
            assert english(element, point) == english(after[token], point), (
                token,
                point,
            )
    report["english_snapshots_verified"] = len(all_points)
    report["result_sha256"] = hashlib.sha256(result.encode()).hexdigest()
    return result, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--extracted", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result, report = merge(
        args.baseline.read_text(encoding="utf-8"),
        json.loads(args.timeline.read_text(encoding="utf-8")),
        load_extracted(args.extracted),
    )
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if result is None:
        raise SystemExit(
            "Ambiguous locale names; inspect the report and supply accessible_overrides."
        )
    args.output.write_text(result, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
