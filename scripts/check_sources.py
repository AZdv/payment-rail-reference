#!/usr/bin/env python3
"""
Re-fetch every source URL in rails/*.yaml and report which ones changed.

The problem this solves is that payment rail parameters go stale quietly. A
limit changes, every page on the web that quotes it is wrong from that morning,
and nobody finds out until an integration breaks. Keeping a reference current by
remembering to check it does not work.

So this stores a content hash per source URL and compares on each run. It does
not try to understand what changed. It tells you which page moved, and a human
reads that page and updates the YAML. That is the only part that needs judgment
and it is the part that stays manual on purpose.

Exit codes: 0 nothing changed, 1 something changed, 2 nothing changed but a
source could not be fetched. Only 1 is worth waking someone for. Some of these
hosts, nacha.org among them, refuse requests from CI runners while serving a
browser or a laptop normally, so 2 is routine rather than a fault.

usage: python3 scripts/check_sources.py [--update]
"""
import argparse
import concurrent.futures
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

STATE = os.path.join(os.path.dirname(__file__), "..", "source-hashes.json")
UA = "payment-rail-reference source checker (+https://github.com/AZdv/payment-rail-reference)"
TIMEOUT = 20
WORKERS = 8


def sources():
    """Pull every source URL out of the YAML without needing a yaml dependency."""
    found = {}
    for path in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "rails", "*.yaml"))):
        rail = os.path.basename(path).replace(".yaml", "")
        for line in open(path, encoding="utf-8"):
            m = re.search(r"^\s*(?:source|searched):\s*(https?://\S+)\s*$", line)
            if m:
                found.setdefault(m.group(1), set()).add(rail)
    return {u: sorted(r) for u, r in found.items()}


def fingerprint(url):
    """
    Hash the visible text, not the raw bytes. Rendered pages carry rotating
    CSRF tokens, build ids and timestamps, and hashing those means every run
    reports every page as changed, which trains you to ignore the alert.
    """
    # curl rather than urllib: urllib hangs against several of these hosts even
    # with a socket timeout set, and curl is present on every CI image we care
    # about. --fail turns an HTTP error into a non-zero exit rather than a page
    # of error HTML that would hash as a legitimate change.
    out = subprocess.run(
        ["curl", "-sSL", "--fail", "--max-time", str(TIMEOUT), "-A", UA, url],
        capture_output=True, timeout=TIMEOUT + 10,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip() or f"curl exit {out.returncode}")
    raw = out.stdout.decode("utf-8", "replace")
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
    text = re.sub(r"\b[0-9a-f]{16,}\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), len(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true",
                    help="write current hashes as the new baseline")
    args = ap.parse_args()

    prior = {}
    if os.path.exists(STATE):
        prior = json.load(open(STATE, encoding="utf-8"))

    src = sources()
    current, changed, unreachable = {}, [], []

    def one(item):
        url, rails = item
        try:
            digest, size = fingerprint(url)
            return url, rails, digest, size, None
        except Exception as e:
            return url, rails, None, 0, str(e)[:90]

    # Serial fetching took minutes against ten sources, some of which throttle.
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for url, rails, digest, size, err in pool.map(one, sorted(src.items())):
            if err is not None:
                unreachable.append((url, rails, err))
                # keep the old hash so one flaky fetch does not look like a change
                if url in prior:
                    current[url] = prior[url]
                continue
            current[url] = {"sha256": digest, "chars": size, "rails": rails}
            was = prior.get(url)
            if was and was.get("sha256") != digest:
                changed.append((url, rails, was.get("chars", 0), size))

    for url, rails, err in unreachable:
        print(f"UNREACHABLE  [{','.join(rails)}]  {url}\n             {err}")
    for url, rails, before, after in changed:
        print(f"CHANGED      [{','.join(rails)}]  {url}\n             {before} -> {after} chars of text")

    if args.update or not prior:
        json.dump(current, open(STATE, "w", encoding="utf-8"), indent=1, sort_keys=True)
        print(f"baseline written: {len(current)} sources")

    if not changed and not unreachable:
        print(f"{len(current)} sources checked, none changed")

    # A real change outranks an unreachable source. Getting this backwards meant
    # one bot-blocked host masked every genuine change behind exit 2.
    if changed:
        return 1
    return 2 if unreachable else 0


if __name__ == "__main__":
    sys.exit(main())
