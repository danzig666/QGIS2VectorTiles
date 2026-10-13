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
      ;;
  esac
done
# One annotation with every failed test and its first error line (the
# annotations stay readable through the API; the log is long).
report="$(python3 - "$@" <<'PY'
import glob, sys
import xml.etree.ElementTree as ET
lines = []
for path in sorted(glob.glob("test-reports/*.xml")):
    for case in ET.parse(path).getroot().iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is not None:
                first = (node.get("message") or node.text or "").strip().splitlines()
                lines.append(f"{case.get('classname')}::{case.get('name')}: {first[0][:160] if first else kind}")
print("\n".join(lines))
PY
)"
counts="$(python3 - <<'PY'
import glob
import xml.etree.ElementTree as ET
total = {"tests": 0, "skipped": 0, "failures": 0, "errors": 0}
for path in glob.glob("test-reports/*.xml"):
    for suite in ET.parse(path).getroot().iter("testsuite"):
        for key in total:
            total[key] += int(suite.get(key, 0))
passed = total["tests"] - total["skipped"] - total["failures"] - total["errors"]
print(f"{passed} passed, {total['skipped']} skipped, {total['failures'] + total['errors']} failed")
PY
)"
echo "::notice title=${title}::${counts} (${passed} of $# files passed)"
{
  echo "### ${title}"
  echo "${passed} of $# test files passed: ${counts}."
  for item in "${failed[@]}"; do echo "- ❌ ${item}"; done
  [ -z "${report}" ] || { echo '```'; echo "${report}"; echo '```'; }
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
if [ ${#failed[@]} -gt 0 ]; then
  message="$(printf '%s\n' "${failed[@]}"; [ -z "${report}" ] || printf 'Failed tests:\n%s\n' "${report}")"
  message="${message:0:60000}"
  message="${message//'%'/'%25'}"
  message="${message//$'\n'/'%0A'}"
  echo "::error title=${title}: ${#failed[@]} of $# test files failed::${message}"
fi
[ ${#failed[@]} -eq 0 ]
