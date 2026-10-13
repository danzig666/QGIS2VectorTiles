#!/usr/bin/env bash
# Runs each test file given in its own pytest process (used by
# .github/workflows/tests.yml): a crash of QGIS in one file fails that file
# only, the others still run. JUnit reports go to test-reports/, a summary to
# the job summary. Exit 1 if any file failed.
set -u
title="$1"
shift
mkdir -p test-reports
failed=()
passed=0
for file in "$@"; do
  name="$(echo "${file%.py}" | tr '/' '_')"
  echo "::group::${file}"
  start=$SECONDS
  python3 -m pytest -q -rfE -p no:cacheprovider --junitxml="test-reports/${name}.xml" "${file}"
  code=$?
  echo "::endgroup::"
  echo "${file}: exit ${code} in $((SECONDS - start)) s"
  case "${code}" in
    0|5) passed=$((passed + 1)) ;;  # 5: no tests collected (all skipped by conftest)
    *)
      failed+=("${file} (exit ${code}$([ "${code}" -ge 128 ] && echo ", crashed: signal $((code - 128))"))")
      echo "::error title=${title}::${file} failed (exit ${code})"
      ;;
  esac
done
{
  echo "### ${title}"
  echo "${passed} of $# test files passed."
  for item in "${failed[@]}"; do echo "- ❌ ${item}"; done
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
[ ${#failed[@]} -eq 0 ]
