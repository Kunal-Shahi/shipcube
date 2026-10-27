"""Calibration suite: small real apps with a known intended workload class.

The traces are a different domain from apps ShipCube profiles (different units,
different missing features). These apps are run through the real profiler
(scripts/calibrate.py) to produce feature rows in the *target* domain. They're used to
1. measure how well the trace-trained model transfers to profiled apps,
2. calibrate confidence (temperature scaling), and pick the low-confidence threshold,
3. (cross-validated) add target-domain rows to training.

Each app is parameterised so the suite covers a range within each class. The labels
are the design intent of each app, checked against the same physical thresholds as the
trace labels (weak_labels.THRESHOLDS).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class App:
    name: str
    label: str
    files: dict[str, str]
    hints: dict = field(default_factory=dict)

    def source(self) -> dict:
        return {"kind": "inline", "files": [{"path": p, "content": c} for p, c in self.files.items()]}


BATCH = """
import csv, os, random, time
from collections import defaultdict
rng = random.Random({seed})
acc = defaultdict(float)
for i in range({rows}):
    acc[f"k{{rng.randrange({keys})}}"] += rng.random()
out = os.path.join(os.environ.get("TMPDIR", "."), "out.csv")
with open(out, "w") as f:
    w = csv.writer(f)
    for k, v in sorted(acc.items()):
        w.writerow([k, round(v, 3)])
time.sleep({pause})
"""

COMPUTE = """
import math
from multiprocessing import Pool

def burn(n):
    s = 0.0
    for i in range(n):
        s += math.sqrt(i) * math.sin(i)
    return s

if __name__ == "__main__":
    with Pool({workers}) as p:
        p.map(burn, [{per_task}] * {tasks})
"""

MEMORY = """
import time
blocks = []
for _ in range({gib4}):           # quarter-GiB blocks, touched so they're resident
    b = bytearray(256 * 2**20)
    b[::4096] = b"x" * len(b[::4096])
    blocks.append(b)
time.sleep({hold})                # keeps it resident while mostly idle (a cache, a model)
total = sum(len(b) for b in blocks)
"""

IO = """
import os, time
path = os.path.join(os.environ.get("TMPDIR", "."), "blob.bin")
chunk = os.urandom(4 * 2**20)
for r in range({rounds}):
    with open(path, "wb") as f:
        for _ in range({chunks}):
            f.write(chunk)
        f.flush()
        os.fsync(f.fileno())
    with open(path, "rb") as f:
        while f.read(8 * 2**20):
            pass
    time.sleep({pause})
os.remove(path)
"""

WEB = """
import os, random
from flask import Flask, jsonify
app = Flask(__name__)
rng = random.Random(1)
DATA = [rng.random() for _ in range({rows})]
{cache}

@app.route("{root}")
def index():
    return jsonify(n=len(DATA), mean=sum(DATA[:{work}]) / {work})

@app.route("{second}")
def second():
    return jsonify(top=sorted(DATA[:{work}])[-5:])
{blob_route}
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ["PORT"]), threaded=True)
"""

BLOB_ROUTE = """
BLOB = os.urandom({mb} * 2**20)

@app.route("{prefix}/blob")
def blob():
    return BLOB
"""

MEM_CACHE = "CACHE = [bytearray(256 * 2**20) for _ in range({gib4})]\nfor b in CACHE: b[::4096] = b'x' * len(b[::4096])"


def _web(name, label, users, rows=20_000, work=2_000, api=False, cache_gib4=0, blob_mb=0, traffic=None):
    prefix = "/api" if api else ""
    code = WEB.format(
        rows=rows, work=work, root=f"{prefix}/" if api else "/", second=f"{prefix}/top" if api else "/top",
        cache=MEM_CACHE.format(gib4=cache_gib4) if cache_gib4 else "",
        blob_route=BLOB_ROUTE.format(mb=blob_mb, prefix=prefix) if blob_mb else "")
    if api:
        code = code.replace('@app.route("/api/")', '@app.route("/api/stats")')
    hints = {"expected_users": users}
    if traffic:
        hints["traffic"] = traffic
    return App(name, label, {"app.py": code, "requirements.txt": "flask>=3.0\n"}, hints)


def apps() -> list[App]:
    out: list[App] = []
    for i, (rows, sched, pause) in enumerate([(200_000, "0 2 * * *", 0.2), (800_000, "0 * * * *", 0.0),
                                              (1_500_000, "*/15 * * * *", 0.0), (400_000, "0 6 * * 1", 0.5),
                                              (1_000_000, "30 3 * * *", 1.0)]):
        out.append(App(f"batch-{i}", "batch_cron",
                       {"job.py": BATCH.format(seed=i, rows=rows, keys=1000 + 500 * i, pause=pause)},
                       {"schedule": sched}))
    for i, (workers, per_task, tasks) in enumerate([(2, 3_000_000, 4), (3, 2_500_000, 6), (4, 2_000_000, 8),
                                                    (4, 3_000_000, 8)]):
        out.append(App(f"compute-{i}", "compute_heavy",
                       {"main.py": COMPUTE.format(workers=workers, per_task=per_task, tasks=tasks)},
                       {"schedule": "0 1 * * *"}))
    for i, (gib4, hold) in enumerate([(5, 2.5), (6, 3.0), (8, 3.0), (10, 2.5)]):
        out.append(App(f"memory-{i}", "memory_heavy", {"main.py": MEMORY.format(gib4=gib4, hold=hold)},
                       {"schedule": "0 4 * * *"}))
    for i, (rounds, chunks, pause) in enumerate([(3, 40, 0.1), (4, 60, 0.0), (2, 100, 0.2), (5, 30, 0.1)]):
        out.append(App(f"io-{i}", "io_bound", {"main.py": IO.format(rounds=rounds, chunks=chunks, pause=pause)},
                       {"schedule": "0 5 * * *"}))
    for i, users in enumerate([5, 20, 60, 150]):
        out.append(_web(f"bursty-{i}", "bursty_interactive", users, work=1_000 + 1_000 * i))
    for i, users in enumerate([300, 800, 2000, 5000]):
        out.append(_web(f"steady-{i}", "steady_service", users, api=True, work=2_000))
    out.append(_web("memory-web", "memory_heavy", 20, cache_gib4=6))
    # No I/O-bound web app: a Python server pushing large responses keeps its CPU busy,
    # so by the shared thresholds it isn't I/O-bound. The scripts above cover io_bound.
    return out


# --- held-out suite -------------------------------------------------------------------
# Written separately from the dev suite above, with different programs, and never used
# to tune features, labels, thresholds or calibration. scripts/train.py reports on it
# as the final, untouched test.

H_BATCH_SORT = """
import json, os, random
rng = random.Random({seed})
records = [{{"id": i, "score": rng.gauss(0, 1), "tag": rng.choice("xyz")}} for i in range({n})]
records.sort(key=lambda r: (r["tag"], -r["score"]))
with open(os.path.join(os.environ.get("TMPDIR", "."), "ranked.json"), "w") as f:
    json.dump(records[:1000], f)
"""

H_BATCH_TEXT = """
import re, random, collections
rng = random.Random(3)
words = ["alpha", "beta", "gamma", "delta", "error", "warn", "info"]
lines = [" ".join(rng.choice(words) for _ in range(12)) for _ in range({n})]
counts = collections.Counter(w for l in lines for w in re.findall(r"[a-z]+", l))
print(counts.most_common(3))
"""

H_COMPUTE_PRIMES = """
from concurrent.futures import ProcessPoolExecutor

def count_primes(lo_hi):
    lo, hi = lo_hi
    n = 0
    for k in range(max(lo, 2), hi):
        if all(k % d for d in range(2, int(k ** 0.5) + 1)):
            n += 1
    return n

if __name__ == "__main__":
    step = {step}
    with ProcessPoolExecutor({workers}) as ex:
        print(sum(ex.map(count_primes, [(i, i + step) for i in range(0, step * {chunks}, step)])))
"""

H_MEMORY_DICT = """
import time
cache = {{}}
for i in range({n}):
    cache[i] = i.to_bytes(8, "little") * 128   # distinct ~1 KiB values: a lookup table held in memory
time.sleep({hold})
print(len(cache))
"""

H_IO_COPY = """
import os, shutil, time
tmp = os.environ.get("TMPDIR", ".")
src = os.path.join(tmp, "src.bin")
with open(src, "wb") as f:
    f.write(os.urandom({mb} * 2**20))
    f.flush(); os.fsync(f.fileno())
for i in range({copies}):
    dst = os.path.join(tmp, f"copy{{i}}.bin")
    shutil.copyfile(src, dst)
    with open(dst, "rb+") as f:
        os.fsync(f.fileno())
    os.remove(dst)
    time.sleep(0.05)
"""

H_WEB_HTML = """
import os
from flask import Flask
app = Flask(__name__)
ITEMS = [f"item {{i}}" for i in range(500)]

@app.route("/")
def home():
    return "<ul>" + "".join(f"<li>{{x}}</li>" for x in ITEMS[:{k}]) + "</ul>"

@app.route("/about")
def about():
    return "<p>internal tool</p>"

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ["PORT"]), threaded=True)
"""

H_WEB_API = """
import os, hashlib
from flask import Flask, jsonify, request
app = Flask(__name__)

@app.route("/v1/hash")
def h():
    return jsonify(d=hashlib.sha256(request.args.get("q", "x").encode() * {rep}).hexdigest())

@app.route("/v1/health")
def health():
    return jsonify(ok=True)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ["PORT"]), threaded=True)
"""


def holdout_apps() -> list[App]:
    req = {"requirements.txt": "flask>=3.0\n"}
    return [
        App("h-sort", "batch_cron", {"main.py": H_BATCH_SORT.format(seed=1, n=600_000)}, {"schedule": "0 3 * * 0"}),
        App("h-text", "batch_cron", {"main.py": H_BATCH_TEXT.format(n=150_000)}, {"schedule": "15 * * * *"}),
        App("h-primes2", "compute_heavy", {"main.py": H_COMPUTE_PRIMES.format(step=60_000, workers=3, chunks=6)},
            {"schedule": "0 0 * * *"}),
        App("h-primes4", "compute_heavy", {"main.py": H_COMPUTE_PRIMES.format(step=50_000, workers=4, chunks=12)},
            {"schedule": "0 0 * * *"}),
        App("h-dict", "memory_heavy", {"main.py": H_MEMORY_DICT.format(n=1_400_000, hold=4)}, {"schedule": "0 7 * * *"}),
        App("h-copy", "io_bound", {"main.py": H_IO_COPY.format(mb=200, copies=4)}, {"schedule": "0 8 * * *"}),
        App("h-html-10", "bursty_interactive", {"app.py": H_WEB_HTML.format(k=200), **req}, {"expected_users": 10}),
        App("h-html-40", "bursty_interactive", {"app.py": H_WEB_HTML.format(k=500), **req}, {"expected_users": 40}),
        App("h-api-1k", "steady_service", {"app.py": H_WEB_API.format(rep=50), **req}, {"expected_users": 1000}),
        App("h-api-3k", "steady_service", {"app.py": H_WEB_API.format(rep=200), **req}, {"expected_users": 3000}),
    ]
