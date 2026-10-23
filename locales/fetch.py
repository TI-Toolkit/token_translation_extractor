"""Download hash-pinned localization inputs, optionally only the XML timeline.

No calculator binaries are part of the repository. CD members are downloaded
using HTTP byte ranges rather than fetching entire disc images.
"""

import argparse
import concurrent.futures
import hashlib
import json
import urllib.request
from pathlib import Path


def fetch_app(app, directory):
    path = directory / app["file"]
    digest = app["sha256"]
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
        return app, "cached"
    failures = []
    for source in app["sources"]:
        headers = {"User-Agent": "TI-Toolkit locale input collector"}
        start = source.get("iso_offset")
        if start is not None:
            headers["Range"] = f"bytes={start}-{start + app['size'] - 1}"
        try:
            request = urllib.request.Request(source["url"], headers=headers)
            with urllib.request.urlopen(request, timeout=45) as response:
                if start is not None:
                    expected = f"bytes {start}-{start + app['size'] - 1}/"
                    if response.status != 206 or not response.headers.get(
                        "Content-Range", ""
                    ).startswith(expected):
                        raise ValueError(
                            "Server did not honor the requested ISO byte range"
                        )
                data = response.read(app["size"] + 1)
            if len(data) != app["size"] or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(
                    "Downloaded size or SHA-256 differs from the pinned input"
                )
            if not data.startswith(b"**TIFL**"):
                raise ValueError("Input is not a TI Flash container")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return app, "downloaded"
        except (OSError, ValueError) as error:
            failures.append(f"{source['url']}: {error}")
    return app, "unavailable: " + ("; ".join(failures) or "no public source URL")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sources", type=Path, default=Path(__file__).with_name("sources.json")
    )
    parser.add_argument(
        "--timeline", type=Path, help="Fetch only apps selected by this timeline"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    data = json.loads(args.sources.read_text(encoding="utf-8"))
    apps = data["apps"]
    if args.timeline:
        plan = json.loads(args.timeline.read_text(encoding="utf-8"))
        selected = {
            sha for profile in plan["profiles"] for sha in profile["apps"].values()
        }
        apps = [app for app in apps if app["sha256"] in selected]
        if {app["sha256"] for app in apps} != selected:
            parser.error("Timeline references an app missing from the source catalog")
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        for app, status in executor.map(
            lambda item: fetch_app(item, args.output), apps
        ):
            print(f"{app['language']} {app['sha256'][:12]}: {status}", flush=True)
            results.append({"sha256": app["sha256"], "status": status})
    (args.output / "catalog.json").write_text(
        json.dumps({"apps": apps}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output / "fetch-results.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    if any(row["status"].startswith("unavailable:") for row in results):
        raise SystemExit("Some inputs could not be recovered; see fetch-results.json")


if __name__ == "__main__":
    main()
