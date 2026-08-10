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

Exit codes: 0 nothing changed, 1 something changed or a source is GONE (404),
3 the state file is missing, empty or corrupt, 2 nothing changed but a
source could not be fetched. Only 1 is worth waking someone for. Exit 2 is not
necessarily a fault: on the CI run of 2026-08-07, nacha.org was UNREACHABLE from
a GitHub Actions runner while serving this machine normally the same hour. That
is one observation, not a rule, so treat a repeated exit 2 for the same host as
worth a look rather than as expected noise.

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

class Unreachable(Exception):
    """The source could not be fetched. Content unknown, not necessarily wrong."""


class Gone(Exception):
    """The source returned 404/410. The citation is dead and must be fixed."""


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
        ["curl", "-sSL", "--max-time", str(TIMEOUT), "-A", UA,
         "-w", "\n%{http_code} %{url_effective}", url],
        capture_output=True, timeout=TIMEOUT + 10,
    )
    if out.returncode != 0:
        raise Unreachable(out.stderr.decode("utf-8", "replace").strip() or f"curl exit {out.returncode}")

    body = out.stdout.decode("utf-8", "replace")
    body, _, tail = body.rpartition("\n")
    status, _, final_url = tail.partition(" ")

    # A source that 404s is the single most important staleness event there is,
    # and the earlier version classified it as merely unreachable, which never
    # opens an issue. The citation would sit dead in the YAML forever while the
    # build stayed green. Gone is a change, not an outage.
    if status in ("404", "410"):
        raise Gone(f"HTTP {status}: the page this fact is sourced from is gone")
    if not status.startswith("2"):
        raise Unreachable(f"HTTP {status}")
    raw = body
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
                    help="accept the current state as the new baseline")
    ap.add_argument("--init", action="store_true",
                    help="create a baseline from nothing (required on first run)")
    args = ap.parse_args()

    prior = {}
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as fh:
                prior = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            # Previously this propagated, and an uncaught exception exits 1,
            # which is the workflow's "a source changed" code. A truncated state
            # file therefore opened an issue titled "Source pages changed" whose
            # body was a traceback.
            print(f"STATE CORRUPT  {STATE}: {e}")
            print("Re-create it deliberately with --init after checking what happened.")
            return 3
        if not isinstance(prior, dict) or not prior:
            # An empty baseline silently re-adopted whatever the pages said that
            # day, including changes it should have caught, then reported
            # "none changed" and exited 0. Monitoring off, build green.
            print(f"STATE EMPTY    {STATE} holds no baseline.")
            print("Re-create it deliberately with --init.")
            return 3

    src = sources()
    current, changed, unreachable, gone = {}, [], [], []

    def one(item):
        url, rails = item
        try:
            digest, size = fingerprint(url)
            return url, rails, digest, size, None, None
        except Gone as e:
            return url, rails, None, 0, str(e)[:90], "gone"
        except Exception as e:
            return url, rails, None, 0, str(e)[:90], "unreachable"

    # Serial fetching took minutes against ten sources, some of which throttle.
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for url, rails, digest, size, err, kind in pool.map(one, sorted(src.items())):
            if kind == "gone":
                gone.append((url, rails, err))
                if url in prior:
                    current[url] = prior[url]
                continue
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

    for url, rails, err in gone:
        print(f"GONE         [{','.join(rails)}]  {url}\n             {err}")
    for url, rails, err in unreachable:
        print(f"UNREACHABLE  [{','.join(rails)}]  {url}\n             {err}")
    for url, rails, before, after in changed:
        print(f"CHANGED      [{','.join(rails)}]  {url}\n             {before} -> {after} chars of text")

    if args.update or args.init:
        # Atomic: a kill mid-write previously truncated the file, which fed
        # straight back into the corrupt-state path above.
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, STATE)
        print(f"baseline written: {len(current)} sources")

    if not prior and not (args.init or args.update):
        print("NO BASELINE    nothing to compare against. Run with --init first.")
        return 3

    if not changed and not unreachable and not gone:
        print(f"{len(current)} sources checked, none changed")

    # A real change outranks an unreachable source. Getting this backwards meant
    # one bot-blocked host masked every genuine change behind exit 2.
    # A dead citation is as actionable as a changed one, so both wake someone.
    if changed or gone:
        return 1
    return 2 if unreachable else 0


if __name__ == "__main__":
    sys.exit(main())
