#!/usr/bin/env python3
"""Validate one CI concurrency matrix and print median-throughput Markdown."""

import argparse
import json
import math
from pathlib import Path


BODIES = {
    "1KiB": ("1 KiB", 1024),
    "10KiB": ("10 KiB", 10 * 1024),
    "100KiB": ("100 KiB", 100 * 1024),
    "0B": ("0 B", 0),
}
CONCURRENCY = (1, 10, 50, 100)
HEADERS = ("none", "request", "response", "both")
QPACK = ("none", "request", "response", "both")
CLIENTS = ("http3", "h3", "nghttp3")


def expected_cases(body, suite, concurrency=None):
    label, _ = BODIES[body]
    headers = ("both",) if suite == "comparison" else HEADERS
    modes = ("none", "both") if suite == "comparison" else QPACK
    levels = CONCURRENCY if concurrency is None else (concurrency,)
    return {
        f"{client}/{label}/server-nghttp3-native-2/requests-1000/"
        f"concurrency-{concurrency}/headers-{header}/qpack-{mode}":
        (client, concurrency, header, mode)
        for header in headers
        for concurrency in levels
        for mode in modes
        for client in CLIENTS
        if client != "h3" or mode == "none"
    }


def read_results(root, body, suite, concurrency=None):
    if not root.is_dir():
        raise ValueError(f"Criterion root is not a directory: {root}")
    label, body_bytes = BODIES[body]
    expected = expected_cases(body, suite, concurrency)
    throughput = {"Bytes": 102400000} if body_bytes == 100 * 1024 else {"Elements": 1000}
    numerator = 1000 * 1e9 * body_bytes / 2**20 if body_bytes == 100 * 1024 else 1e9
    values = {}
    for path in sorted(root.rglob("new/benchmark.json")):
        metadata = json.loads(path.read_text(encoding="utf-8"))
        full_id = metadata["full_id"]
        if not isinstance(full_id, str) or len(full_id.split("/")) < 2:
            raise ValueError(f"invalid benchmark full_id in {path}")
        if full_id.split("/")[1] not in {label for label, _ in BODIES.values()}:
            raise ValueError(f"unexpected body in case: {full_id}")
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


def render(values, concurrency, suite):
    lines = [f"## Concurrency {concurrency}: {suite}", "",
             "Median batch throughput; higher is better. Full Client stacks against the native Server.",
             "Includes connection establishment and requests; excludes Client initialization and teardown.",
             "TLS: AES-128-GCM with X25519 on every Client and the Server.",
             "Each 1000-request batch starts with a fresh QPACK table; h3 supports static QPACK only.", ""]

    def value(body, client, header, mode):
        if client == "h3" and mode != "none":
            return "—"
        return f"{values[body][client, concurrency, header, mode]:.2f}"

    def row_label(body):
        label, body_bytes = BODIES[body]
        if body_bytes == 0:
            label += " (headers/scheduling diagnostic)"
        unit = "MiB/s" if body_bytes == 100 * 1024 else "kreq/s"
        return f"{label} | {unit}"

    if suite == "comparison":
        lines += ["Headers: both. Static = qpack none; dynamic = qpack both.", "",
                  "| Body | Unit | http3 static | http3 dynamic | h3 static | nghttp3 static | nghttp3 dynamic |",
                  "|---|---|---:|---:|---:|---:|---:|"]
        for body in BODIES:
            cells = [value(body, client, "both", mode)
                     for client, mode in (("http3", "none"), ("http3", "both"),
                                          ("h3", "none"), ("nghttp3", "none"), ("nghttp3", "both"))]
            lines.append(f"| {row_label(body)} | " + " | ".join(cells) + " |")
    else:
        lines += ["QPACK: none = static; request/response/both select dynamic directions.", ""]
        for header in HEADERS:
            lines += [f"### Headers: {header}", "",
                      "| Body | Unit | QPACK | http3 | h3 | nghttp3 |", "|---|---|---|---:|---:|---:|"]
            for body in BODIES:
                for mode in QPACK:
                    cells = [value(body, client, header, mode) for client in CLIENTS]
                    lines.append(f"| {row_label(body)} | {mode} | " + " | ".join(cells) + " |")
            lines.append("")
    return "\n".join(lines).rstrip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("criterion_root", type=Path)
    parser.add_argument("concurrency", type=int, choices=CONCURRENCY)
    parser.add_argument("suite", choices=("comparison", "diagnostic"))
    args = parser.parse_args()
    try:
        values = {
            body: read_results(args.criterion_root, body, args.suite, args.concurrency)
            for body in BODIES
        }
        markdown = render(values, args.concurrency, args.suite)
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        parser.error(str(error))
    print(markdown + "\n")


if __name__ == "__main__":
    main()
