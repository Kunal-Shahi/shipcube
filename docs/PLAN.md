# Build plan (Oct 26 12:00 PM ET → Nov 4 9:00 PM ET)

Cut anything that does not appear in the demo.

| Day | Date | Goal | Done when |
|---|---|---|---|
| 1 | Oct 26–27 | Accounts, Meta API access (ask on Discord), repo, CI, DuckDB plus the Olist dataset | A Muse call works; the dataset loads |
| 2 | Oct 28 | Semantic DSL, validator, L0 compiler | L0 queries match hand-written SQL |
| 3 | Oct 29 | L1 operators and L2 stages, SQL eval suite | About 50 eval queries pass, with a score per level |
| 4 | Oct 30 | Trace ingestion and features; train the classifier | Held-out metrics recorded |
| 5 | Oct 31 | Sandbox profiler and placement on GCP | A notebook produces a profile, a tier and a cost |
| 6 | Nov 1 | Muse Code understanding plus Muse Spark semantic rewrite | Sample notebook → DSL diff → compiled SQL |
| 7 | Nov 2 | Deploy T0–T2; promotion across customer sets | App runs on sets A and B, with a comparison |
| 8 | Nov 3 | Monitoring, model-provider settings, sharing | Monitoring page live; BYOK key works |
| 9 | Nov 4 (AM) | UI polish, README numbers | Every screen has real loading, empty and error states |
| 10 | Nov 4 (PM) | 2-minute demo video, submit before 9:00 PM ET | Submitted |

## Demo script (2 minutes)

1. Upload a messy analyst notebook (Olist: regional revenue and share-of-orders tracker).
2. Show the detected app type, the DSL diff and the compiled SQL, including one query the old approach can't express (month-over-month change in share by region).
3. Show the profile, then the placement: T1 serverless, cost estimate, reasoning.
4. Deploy, run on customer sets A and B, and show the output comparison.
5. Open the monitoring page with health, freshness, cost and drift, plus a re-placement suggestion.
6. Share the app with a teammate's role and switch its AI provider to a BYOK key.
