"""Sample app: a small sales dashboard a team opens a few times a day.

Bursty and interactive: idle most of the time, a handful of quick requests when
someone opens the page.
"""

import os
import random
from collections import defaultdict

from flask import Flask, jsonify

app = Flask(__name__)

rng = random.Random(42)
REGIONS = ["north", "south", "east", "west"]
ORDERS = [
    {"id": i, "region": rng.choice(REGIONS), "amount": round(rng.uniform(5, 500), 2)}
    for i in range(20_000)
]


def by_region():
    totals = defaultdict(float)
    for o in ORDERS:
        totals[o["region"]] += o["amount"]
    return {k: round(v, 2) for k, v in sorted(totals.items())}


@app.route("/")
def index():
    rows = "".join(f"<tr><td>{k}</td><td>{v:,.2f}</td></tr>" for k, v in by_region().items())
    return f"<h1>Revenue by region</h1><table>{rows}</table>"


@app.route("/api/summary")
def summary():
    amounts = [o["amount"] for o in ORDERS]
    return jsonify(orders=len(amounts), revenue=round(sum(amounts), 2),
                   aov=round(sum(amounts) / len(amounts), 2))


@app.route("/api/by-region")
def api_by_region():
    return jsonify(by_region())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8080")), threaded=True)
