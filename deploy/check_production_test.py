#!/usr/bin/env python3
"""Self-test for check_production.py (CI's deploy-scripts job). Standard library only."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_production import check  # noqa: E402

BAD = [
    '            - {name: SHIPCUBE_DEV_MODE, value: "1"}',
    '            - {name: DEV_MODE, value: "true"}',
    '  --update-env-vars SHIPCUBE_DEV_MODE=1',
    '  --set-env-vars "^|^SHIPCUBE_BACKEND=mock|SHIPCUBE_DB_SET_ROLE=0"',
    '            - {name: SHIPCUBE_SECRET_STORE, value: "local"}',
    '            - {name: SHIPCUBE_EMBEDDED_WORKER, value: "1"}',
    '            - {name: SHIPCUBE_ESTIMATOR, value: "dev"}',
    '            - {name: SHIPCUBE_ENV, value: "local"}',
    '            - {name: SHIPCUBE_CONSOLE_URL, value: "http://localhost:3000"}',
    'GATEWAY_URL="http://127.0.0.1:8090"',
    '            - {name: SHIPCUBE_AI_GATEWAY_URL, value: "http://host.docker.internal:8090"}',
    'CONSOLE_URL=https://web.localhost',
    'SHIPCUBE_TOKEN_PEPPER=sclocal_abcdef',
    '            - {name: SHIPCUBE_PROFILER, value: "local"}',
    '            - {name: SHIPCUBE_MONITOR, value: mock}',
    '  --set-env-vars "^|^SHIPCUBE_MONITOR=local"',
]
GOOD = [
    '            - {name: SHIPCUBE_EMBEDDED_WORKER, value: "0"}',
    '            - {name: SHIPCUBE_CONSOLE_URL, value: "${CONSOLE_URL}"}',
    '  --set-env-vars "^|^SHIPCUBE_BACKEND=gcp|SHIPCUBE_DB_IAM_AUTH=1"',
    '# SHIPCUBE_DEV_MODE=1 is for local development only',
    'CONSOLE_URL="https://console.example.com"',
    '            - {name: SHIPCUBE_MONITOR, value: gcp}',
    '            - {name: SHIPCUBE_PROFILER, value: "gcp"}',
]


class CheckProduction(unittest.TestCase):
    def run_on(self, line: str) -> list[str]:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(line + "\n")
        try:
            return check(Path(f.name))
        finally:
            Path(f.name).unlink()

    def test_bad_lines_fail(self):
        for line in BAD:
            with self.subTest(line=line):
                self.assertTrue(self.run_on(line), line)

    def test_good_lines_pass(self):
        for line in GOOD:
            with self.subTest(line=line):
                self.assertEqual(self.run_on(line), [], line)


if __name__ == "__main__":
    unittest.main()
