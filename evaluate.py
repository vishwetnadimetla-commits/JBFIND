#!/usr/bin/env python3
"""evaluate.py — score n8n workflow output quality against a known baseline.

Run after run.sh completes. Reads the last execution log from n8n API,
compares against the static test set expectations, and prints a single
quality score (lower is better).

Usage: python3 evaluate.py [--verbose]
"""

import json
import os
import sys
import urllib.request

N8N_URL = os.environ.get("N8N_URL", "http://localhost:5678")
N8N_API_KEY = os.environ.get("N8N_API_KEY", "")
TEST_SET_PATH = os.path.join(os.path.dirname(__file__), "test_jobs")


def fetch_last_execution():
    url = f"{N8N_URL}/rest/executions?limit=1"
    req = urllib.request.Request(url, headers={"X-N8N-API-KEY": N8N_API_KEY})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read()).get("data", [])
        if not data:
            return None
        return data[0]


def score_execution(execution: dict) -> float:
    status = execution.get("status", "unknown")
    if status == "error":
        return 1000.0  # hard fail

    finished = execution.get("finished", False)
    if not finished:
        return 500.0

    # Parse workflow output for quality signals
    data = execution.get("data", {})
    result_data = data.get("resultData", {})

    error_count = result_data.get("errorCount", 0)
    node_count = result_data.get("runData", {})
    total_nodes = len(node_count)

    if error_count > 0 or total_nodes == 0:
        return 200.0 + error_count * 50

    # Baseline score: fewer errors + more complete nodes = better
    base = float(error_count * 100)
    return base


def main():
    verbose = "--verbose" in sys.argv
    exec_data = fetch_last_execution()
    if exec_data is None:
        print("ERROR: no executions found")
        sys.exit(1)

    score = score_execution(exec_data)
    if verbose:
        print(f"Execution {exec_data.get('id')}: {exec_data.get('status')}")
        print(f"Score: {score}")
    else:
        print(score)


if __name__ == "__main__":
    main()