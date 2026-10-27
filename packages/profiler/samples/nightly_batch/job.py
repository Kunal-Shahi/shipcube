"""Sample app: a nightly scoring job.

Reads yesterday's events (synthetic here, or SHIPCUBE_SAMPLE_DIR/events.csv), scores
each account, writes a CSV report, then tries to post a summary to a webhook.
Runs to completion; meant to be scheduled once a night.
"""

import csv
import math
import os
import random
import urllib.request
from collections import defaultdict
from pathlib import Path


def load_events():
    sample = os.environ.get("SHIPCUBE_SAMPLE_DIR")
    if sample and (Path(sample) / "events.csv").exists():
        with open(Path(sample) / "events.csv") as f:
            yield from csv.DictReader(f)
        return
    rng = random.Random(7)
    for i in range(1_500_000):
        yield {"account": f"acct-{rng.randrange(5000)}", "kind": rng.choice("abcde"),
               "value": rng.expovariate(1 / 40)}


def score(stats):
    n, total, kinds = stats["n"], stats["total"], len(stats["kinds"])
    return round(math.log1p(total) * math.sqrt(n) / (1 + 5 - kinds), 4)


def main():
    acc = defaultdict(lambda: {"n": 0, "total": 0.0, "kinds": set()})
    for e in load_events():
        s = acc[e["account"]]
        s["n"] += 1
        s["total"] += float(e["value"])
        s["kinds"].add(e["kind"])
    out = Path(os.environ.get("OUTPUT_DIR", os.environ.get("TMPDIR", "."))) / "scores.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["account", "events", "score"])
        for k in sorted(acc):
            w.writerow([k, acc[k]["n"], score(acc[k])])
    try:
        urllib.request.urlopen("https://hooks.example.com/nightly-scores", data=b"done", timeout=3)
    except OSError as e:
        print(f"webhook skipped: {e}")
    print(f"scored {len(acc)} accounts -> {out}")


if __name__ == "__main__":
    main()
