#!/usr/bin/env python3
"""Validate one CI body matrix and print median-throughput Markdown."""

import argparse
import json
import math
from pathlib import Path


BODIES = {
    "0B": ("0 B", 0),
    "1KiB": ("1 KiB", 1024),
    "10KiB": ("10 KiB", 10 * 1024),
    "100KiB": ("100 KiB", 100 * 1024),
}
CONCURRENCY = (1, 10, 50, 100)
HEADERS = ("none", "request", "response", "both")
QPACK = ("none", "request", "response", "both")
CLIENTS = ("http3", "h3", "nghttp3")


def expected_cases(body, suite):
    label, _ = BODIES[body]
    headers = ("both",) if suite == "comparison" else HEADERS
    modes = ("none", "both") if suite == "comparison" else QPACK
    return {
        f"{client}/{label}/server-nghttp3-native-2/requests-1000/"
        f"concurrency-{concurrency}/headers-{header}/qpack-{mode}":
        (client, concurrency, header, mode)
        for header in headers
        for concurrency in CONCURRENCY
        for mode in modes
        for client in CLIENTS
        if client != "h3" or mode == "none"
    }


def read_results(root, body, suite):
    if not root.is_dir():
        raise ValueError(f"Criterion root is not a directory: {root}")
    label, body_bytes = BODIES[body]
    expected = expected_cases(body, suite)
    throughput = {"Bytes": 102400000} if body_bytes == 100 * 1024 else {"Elements": 1000}
    numerator = 1000 * 1e9 * body_bytes / 2**20 if body_bytes == 100 * 1024 else 1e9
    values = {}
    for path in sorted(root.rglob("new/benchmark.json")):
        metadata = json.loads(path.read_text(encoding="utf-8"))
        full_id = metadata["full_id"]
        if not isinstance(full_id, str) or len(full_id.split("/")) < 2:
            raise ValueError(f"invalid benchmark full_id in {path}")
        if full_id.split("/")[1] != label:
            continue
        if full_id not in expected:
            raise ValueError(f"unexpected {body} case: {full_id}")
        key = expected[full_id]
        if key in values:
            raise ValueError(f"duplicate case: {full_id}")
        if metadata.get("throughput") != throughput:
            raise ValueError(f"wrong throughput metadata for {full_id}: expected {throughput}")
        estimates = json.loads(path.with_name("estimates.json").read_text(encoding="utf-8"))
        ns = estimates["median"]["point_estimate"]
        if isinstance(ns, bool) or not isinstance(ns, (int, float)) or not math.isfinite(ns) or ns <= 0:
            raise ValueError(f"invalid median batch nanoseconds for {full_id}: {ns!r}")
        # Criterion has already divided sample times by iteration counts. One
        # iteration is 1000 requests; these are kreq/s or body-only MiB/s.
        rate = numerator / ns
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError(f"invalid throughput for {full_id}")
        values[key] = rate
    missing = [full_id for full_id, key in expected.items() if key not in values]
    if missing:
        raise ValueError(f"missing {len(missing)} {body} cases; first: {missing[0]}")
    return values


def render(values, body, suite):
    label, body_bytes = BODIES[body]
    unit = "MiB/s" if body_bytes == 100 * 1024 else "kreq/s"
    control = " — headers/scheduling diagnostic" if body_bytes == 0 else ""
    lines = [f"## {label}: {suite}{control}", "",
             f"Median batch throughput ({unit}); higher is better. Full Client stacks against the native Server.",
             "Includes connection establishment and requests; excludes Client initialization and teardown.",
             "TLS: AES-128-GCM with X25519 on every Client and the Server.",
             "Each 1000-request batch starts with a fresh QPACK table; h3 supports static QPACK only.", ""]

    def value(client, concurrency, header, mode):
        if client == "h3" and mode != "none":
            return "—"
        return f"{values[client, concurrency, header, mode]:.2f}"

    if suite == "comparison":
        lines += ["Headers: both. Static = qpack none; dynamic = qpack both.", "",
                  "| Concurrency | http3 static | http3 dynamic | h3 static | nghttp3 static | nghttp3 dynamic |",
                  "|---:|---:|---:|---:|---:|---:|"]
        for concurrency in CONCURRENCY:
            cells = [value(client, concurrency, "both", mode)
                     for client, mode in (("http3", "none"), ("http3", "both"),
                                          ("h3", "none"), ("nghttp3", "none"), ("nghttp3", "both"))]
            lines.append(f"| {concurrency} | " + " | ".join(cells) + " |")
    else:
        lines += ["QPACK: none = static; request/response/both select dynamic directions.", ""]
        for header in HEADERS:
            lines += [f"### Headers: {header}", "",
                      "| Concurrency | QPACK | http3 | h3 | nghttp3 |", "|---:|---|---:|---:|---:|"]
            for concurrency in CONCURRENCY:
                for mode in QPACK:
                    cells = [value(client, concurrency, header, mode) for client in CLIENTS]
                    lines.append(f"| {concurrency} | {mode} | " + " | ".join(cells) + " |")
            lines.append("")
    return "\n".join(lines).rstrip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("criterion_root", type=Path)
    parser.add_argument("body", choices=BODIES)
    parser.add_argument("suite", choices=("comparison", "diagnostic"))
    args = parser.parse_args()
    try:
        values = read_results(args.criterion_root, args.body, args.suite)
        markdown = render(values, args.body, args.suite)
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        parser.error(str(error))
    print(markdown + "\n")


if __name__ == "__main__":
    main()
