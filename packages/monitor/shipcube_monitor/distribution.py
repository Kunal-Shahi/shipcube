"""Percentiles from a Cloud Monitoring Distribution value.

Cloud Run reports latencies as distributions. We merge all series with REDUCE_SUM, then read the
percentile from the bucket counts, interpolating linearly inside the bucket. Bucket layouts:
https://cloud.google.com/monitoring/api/ref_v3/rest/v3/TypedValue#BucketOptions
"""

from __future__ import annotations

from typing import Any


def bucket_bounds(options: dict[str, Any]) -> list[float]:
    """Finite upper bounds of buckets 0..N (bucket N+1 is the overflow)."""
    if "exponentialBuckets" in options:
        b = options["exponentialBuckets"]
        n, g, s = int(b["numFiniteBuckets"]), float(b["growthFactor"]), float(b["scale"])
        return [s * g**i for i in range(n + 1)]
    if "linearBuckets" in options:
        b = options["linearBuckets"]
        n, w, o = int(b["numFiniteBuckets"]), float(b["width"]), float(b.get("offset", 0))
        return [o + w * i for i in range(n + 1)]
    if "explicitBuckets" in options:
        return [float(x) for x in options["explicitBuckets"]["bounds"]]
    raise ValueError("distribution has no bucket options")


def percentile(dist: dict[str, Any], q: float) -> float | None:
    """The q-th percentile (0 < q < 1), or None for an empty distribution."""
    counts = [int(c) for c in dist.get("bucketCounts", [])]
    total = sum(counts)
    if total == 0:
        return None
    bounds = bucket_bounds(dist.get("bucketOptions", {}))
    rank = q * total
    seen = 0
    for i, c in enumerate(counts):
        if c and seen + c >= rank:
            # Bucket 0 is (-inf, bounds[0]); bucket i is [bounds[i-1], bounds[i]); the last is overflow.
            lo = bounds[i - 1] if i > 0 else 0.0
            hi = bounds[i] if i < len(bounds) else lo
            return lo + (hi - lo) * (rank - seen) / c
        seen += c
    return bounds[-1]


def merge(dists: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Sum distributions that share bucket options (used when the API returns several points)."""
    dists = [d for d in dists if int(d.get("count", 0))]
    if not dists:
        return None
    width = max(len(d.get("bucketCounts", [])) for d in dists)
    counts = [0] * width
    total, weighted = 0, 0.0
    for d in dists:
        for i, c in enumerate(d.get("bucketCounts", [])):
            counts[i] += int(c)
        n = int(d["count"])
        total += n
        weighted += float(d.get("mean", 0)) * n
    return {
        "count": str(total),
        "mean": weighted / total if total else 0.0,
        "bucketOptions": dists[0].get("bucketOptions", {}),
        "bucketCounts": [str(c) for c in counts],
    }
