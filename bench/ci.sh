#!/usr/bin/env bash
# One Actions step owns one body size. Reuse the executable built
# by the workflow; compile/setup work must not run alongside Client samples.
# Usage: BENCH_EXE=/path/to/clients bash bench/ci.sh 1KiB [Criterion options]
# Append --list to inspect coverage or --test for a functional smoke check.
# Default comparison: fixed browser templates in both directions, static and
# bidirectional dynamic QPACK. HTTP3_BENCH_SUITE=diagnostic selects all directions.
# With GITHUB_STEP_SUMMARY set, append a table from measured Criterion JSON.
set -euo pipefail

export HTTP3_BENCH_BODY_SIZES=${1:?missing body size}
shift
export HTTP3_BENCH_REQUESTS=1000
suite=${HTTP3_BENCH_SUITE:-comparison}
case "$suite" in
  comparison) header_modes=(both); qpack_modes=(none both) ;;
  diagnostic) header_modes=(none request response both); qpack_modes=(none request response both) ;;
  *) printf 'Unknown benchmark suite: %s\n' "$suite" >&2; exit 1 ;;
esac

if [[ -n ${HTTP3_BENCH_RESULTS:-} ]]; then
  # Keep smoke runs and other suites out of the published measurement tables.
  export CRITERION_HOME="$HTTP3_BENCH_RESULTS/$suite/$HTTP3_BENCH_BODY_SIZES"
fi

summarize=true
for argument in "$@"; do
  case "$argument" in --list|--test) summarize=false ;; esac
done

for concurrency in 1 10 50 100; do
  export HTTP3_BENCH_CONCURRENCY=$concurrency
  for headers in "${header_modes[@]}"; do
    export HTTP3_BENCH_HEADERS=$headers
    for qpack in "${qpack_modes[@]}"; do
      export HTTP3_BENCH_QPACK=$qpack
      if [[ $qpack == none ]]; then
        label=static
      else
        label="dynamic $qpack"
      fi
      printf '::group::Concurrency: %s / Headers: %s / QPACK: %s\n' \
        "$concurrency" "$headers" "$label"
      # Without --bench, directly invoking Criterion selects its test mode.
      # The runner keeps the http3/h3/nghttp3 order and excludes unsupported h3
      # dynamic modes. All directions use the same batch and timing parameters.
      "${BENCH_EXE:?missing prebuilt benchmark executable}" --bench \
        --sample-size 10 --measurement-time 3 --warm-up-time 1 --noplot "$@"
      printf '::endgroup::\n'
    done
  done
done

if [[ $summarize == true && -n ${GITHUB_STEP_SUMMARY:-} ]]; then
  python3 "$(dirname "${BASH_SOURCE[0]}")/summary.py" \
    "${CRITERION_HOME:?missing Criterion output directory}" \
    "$HTTP3_BENCH_BODY_SIZES" "$suite" >> "$GITHUB_STEP_SUMMARY"
fi
