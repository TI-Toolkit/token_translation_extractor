# TI token translation extractor

These standard-library Python tools extract native token strings and replacement font glyphs from TI-83 Plus, TI-84 Plus, TI-84 Plus C Silver Edition, and TI-84 Plus CE localization apps, and built-in French TI-82 Stats.fr / TI-76.fr ROMs. Downloaded binaries and generated evidence belong in `research/`, outside the commit. Run the commands from the repository root.

The original interactive CE extractor remains available as `python3 extractor.py`; it uses the existing `tivars_lib_py` submodule and Analyze8EX output. The `locales` package reads full app containers and ROM images directly, with no third-party Python dependencies.

## Reproduce the XML

Clone this repository and use Python 3.10 or newer. The merger and verifier reuse `OsVersion` and the XML validator from a separate checkout of [TI-Toolkit/tokens](https://github.com/TI-Toolkit/tokens). Point `TI_TOOLKIT_TOKENS` at that checkout; it must include the companion TI-83 build ordering, French revision, and model-lineage parser changes. No parser code or version-comparison rules are copied into this repository. The fetcher and native extractors do not require this environment variable; Z80 extraction takes the reference token sheet explicitly with `--xml`.

`locales/sources.json` pins 131 original app files by SHA-256. `locales/timeline.json` selects 53 publicly recoverable apps for the XML and records the OS-boundary policy. `locales/rom-sources.json` pins four user-supplied ROMs, including the two French sources and two English reference images. Supply these ROMs locally; the fetcher only downloads the app inputs. ROM evidence records filenames relative to `--rom-directory`, so changing the local directory does not change the output JSON. The remaining files preserve independent app revisions, duplicate containers, and prototype history. Twelve supplementary CE files have only local provenance; they are unnecessary for reproducing the XML.

```sh
export TI_TOOLKIT_TOKENS=/path/to/tokens
mkdir -p research/locale-build
git -C "$TI_TOOLKIT_TOKENS" show 37049d6:8X.xml > research/locale-build/baseline.xml
python3 -m locales.fetch --timeline locales/timeline.json --output research/locale-build/inputs
python3 -m locales.z80 --catalog research/locale-build/inputs/catalog.json --xml research/locale-build/baseline.xml --output research/locale-build/z80
python3 -m locales.ce --catalog research/locale-build/inputs/catalog.json --output research/locale-build/ce
python3 -m locales.rom --rom-directory /path/to/TI-83_ROMs --output research/locale-build/rom
python3 -m locales.merge --baseline research/locale-build/baseline.xml --timeline locales/timeline.json --extracted research/locale-build/z80/manifest.json research/locale-build/ce/index.json research/locale-build/rom/index.json --output research/locale-build/8X.xml --report research/locale-build/merge-report.json
python3 -m locales.verify --baseline research/locale-build/baseline.xml --sheet research/locale-build/8X.xml --timeline locales/timeline.json --extracted research/locale-build/z80/manifest.json research/locale-build/ce/index.json research/locale-build/rom/index.json --report research/locale-build/verification.json
cmp research/locale-build/8X.xml "$TI_TOOLKIT_TOKENS/8X.xml"
```

The fetcher checks size and SHA-256 before writing a file. CD members use exact HTTP byte ranges, avoiding full ISO downloads. Every downloaded app remains unaltered, including multi-section License/application containers. Existing hash-matching files are reused, so the extraction and merge run offline after the inputs have been recovered.

To examine the entire collected history, pass its full `catalog.json` to the two extractors instead. Their output retains native bytes, pointer slots, record offsets, hashes, fallback cases, and font bitmaps. The CE extractor also writes `version-differences.json`.

## OS versions versus application revisions

The XML keeps format 1.0: its version selector is the **OS**, not the separately installable localization app. The timeline uses the latest recovered compatible localizer for each supported generation:

| XML boundary | Selected source | Languages |
| --- | --- | --- |
| TI-82 Stats.fr 1.11fr7 | Built into the ROM | French |
| TI-76.fr 1.00fr5 | Built into the ROM | French |
| TI-83 Plus 0.103 | No recovered applicable localizer | English fallback |
| TI-83 Plus 1.00 | 1.02; Hungarian/Polish 1.00 | 12 |
| TI-84 Plus 0.01 | No recovered applicable localizer | English fallback |
| TI-84 Plus 2.31 | 2.40 | 10 |
| TI-84 Plus CSE 4.0 | 4.0 | 7 |
| TI-84 Plus CE 5.0.0 | 5.0.0.0089 | 6 |
| TI-84 Plus CE 5.1.0 | 5.1.0.0110 | 6 |
| TI-84 Plus CE 5.2.0 | 5.2.0.0035 | 6 |
| TI-84 Plus CE 5.3.0 | 5.3.0.0037 | 6 |

Model order follows token-table lineage, not calendar release dates. The later TI-82 Stats, Stats.fr and TI-76.fr continue the TI-83 branch, before the Plus branch in the XML. In particular, Stats.fr is a later calendar release than the TI-83 Plus. Each derivative has its own model step, so French ROM data does not change the original TI-83 or English Stats model. French `frN` revision suffixes remain literal in the sheet.

The two supplied English ROMs have identical 551-token native tables. Both French ROMs likewise agree on all 551 token spellings, with 256 differing from the English reference. The French profile therefore extends across their model boundary without redundant XML splits. Its spellings differ from the effective TI-83 Plus French app 1.02 on 36 tokens. The ROM profile ends at the sheet's earliest Plus boundary, 0.103, and the app profile begins at 1.00, regardless of calendar release order. No earlier French ROM revision is inferred from these snapshots.

The production TI-83 Plus 1.00 ROM already contains the token and localization hook registration/dispatch APIs used by these apps, including font selectors 75/76. This establishes the required ABI, without claiming that app 1.02 originally shipped with OS 1.00. TI-83 Plus and TI-84 Plus localizers are different binaries, so the former do not carry into the latter's unsupported early range.

The TI-83 Plus 1.00 ROM used for this check has SHA-256 `53280fd9e4167bfaa667ee377ef776fc66fd2aea645a504f8636b7d4b371d270`. Its `_SetLocalizeHook` BCALL at `4F93` reaches code at ROM offset `6F899`, and `_SetTokenHook` at `4F99` reaches `6F88E`; both store the supplied hook pointer and enable its flag. The native token and font paths dispatch those hooks.

The TI-84 Plus app's actual startup gate compares `_GetBaseVer`'s binary major/minor values against `0x021F` and accepts NC: **2.31**. Its upgrade message asks for 2.40, but that text is not the machine check. A real 2.55 ROM returns `A=2, B=55`, confirming the minor-version encoding.

CE release descriptions explicitly associate the 5.1, 5.2, and 5.3 apps with those OS generations. All recovered production token tables from app 5.3.0.0037 through 5.7.0.0017 are identical, so those later apps add no XML ranges. App build components are never copied into OS boundaries automatically.

Independently installable older app versions cannot be selected using the existing OS-only format. For example, Swedish app 1.01 spells `$7C` and `$7D` `BeroendeAutom` and `BeroendeFr`; app 1.02 uses `BeroendeAuto` and `BeroendeFråga`. Both tables remain in the extraction evidence; the XML chooses 1.02 for that compatible model. Prototype CE apps likewise remain in the extracted history rather than receiving invented OS boundaries.

## Native data and Unicode

The ROM extractor executes each image's bounded native `GetTokString` dispatch and follows its contiguous pointer tables. It records the pointer entries, internal record IDs, length-prefixed native strings, instruction traces, version strings, and the large-font raster rows used by the tokens. The two English images provide an independent 551-token comparison with the existing English sheet. The French images use the same large-font character meanings; their small-font accents differ without changing the program-editor representation.

Reserved null pointers and shared question-mark placeholder records are kept as evidence, rather than becoming XML tokens. The real `?` token has a distinct record and is retained. The TI-76.fr reserved-BB clamp and the French contextual error-dialog Goto spelling are followed explicitly. This proves native table spellings, not execution support for every command.

The extractors follow the actual lookup tables. A null pointer means English fallback, and contextual error-dialog Goto labels are recorded separately from BASIC token spellings. Reserved/prefix pseudo-token `?` records do not become XML token definitions. CE pointer relocations are applied before following length-prefixed strings; Z80/CSE Intel HEX records are checked before resolving page-local pointers.

Localization font callbacks affect English fallback strings as well as translated strings. Each app therefore supplies its own complete font mapping. Replacement glyph labels are checked against exact native small/large bitmap records; a changed bitmap fails extraction instead of silently retaining an old label. The canonical XML display follows the large program-editor font. Small-font-only differences remain in the extraction output. Notable differences include:

- Polish/Hungarian characters and Danish/Norwegian `æ/ø/å`.
- Older German apps replace byte `BA` with `ß`.
- Portuguese replaces `91/8D/A9/A5` with `ã/Ã/õ/Õ`.
- Swedish CSE replaces `90/8C` with `å/Å`; CE also replaces `A8/A4` with `ø/Ø`.

Native app mistakes are preserved. Some Z80/CSE string lengths include the next record's length byte, producing an extra glyph. For example, CSE French `$6303` is `5973636C04` and displays `Ysclw`. Those overlaps are flagged in the extraction output instead of being silently corrected. For this CSE token, a scoped accessible override accepts `Yscl`; the native `Ysclw` spelling is retained as its display and an input variant. The older French archive 75207 also has an invalid outer checksum but valid Intel HEX records; it is preserved in the full input catalog and is not selected for the XML.

Unchanged locale entries are omitted: the sheet's documented English fallback applies. Existing English bytes, display, accessible names, variants, token introductions, and availability are retained. Only effective locale changes split ranges. The merger rejects an unreviewed or non-English baseline.

Accessible names retain Latin-1 accents and use ASCII transliterations for other letters and math notation. Unique Unicode spellings are included as variants. ANOVA variables follow the English bracketed factor/error distinction. The few other ambiguous native spellings have explicit accessible overrides in the timeline; their native bytes and displays remain unchanged.

## Verification and provenance

`verify.py` checks every emitted boundary, representative actual OS releases, and all canonical model-latest states. It independently compares effective native-byte/display maps with app records plus English fallback, checks English preservation and original introductions, and rejects overlapping ranges and ambiguous accessible/variant names.

The scripts do not claim interactive installation tests or cryptographic signature validation. Native pointers, glyphs, and OS hook/version checks were verified statically against the supplied apps and ROMs.

The CE extractor extends this repository's original MIT-licensed `extractor.py` and Brandon Wilson's [Analyze8EX](https://brandonw.net/calcstuff/Analyze8EX.zip) relocation layout. Its `5Exx` index mapping is corrected using native OS lookup control flow. The retained MIT notice is in `locales/LICENSE.extractor`. Each source app has its download page/URL, hash, and any ISO-member offset in `locales/sources.json`.
