# Budget kill switch

GCP budgets only alert; they never stop spend. This Cloud Run function turns a budget into a hard cap: when the budget's Pub/Sub update reports cost at or above `CAP`, it **unlinks the billing account from every project in `PROJECTS`**, which stops all paid usage.

Live setup (deployed Oct 27, 2026):

| Piece | Value |
|---|---|
| Budget | "Hard cap INR 24000 (kill switch)" on billing account `My Billing Account`, **credits excluded**, so it measures gross usage against the free-trial credit (about $300) |
| Topic | `projects/shipcube-control-ks/topics/budget-alerts` |
| Function | `budget-killswitch` in `shipcube-control-ks`, `asia-south1`, runs as `budget-killswitch@` |
| `CAP` | `24000` (INR, the billing account's currency) |
| `PROJECTS` | `shipcube-apps-ks,project-8b63b44b-44a3-4e22-9e4,shipcube-control-ks` (its own project last) |
| Service account roles | `roles/billing.projectManager` and `roles/browser` on each listed project; `roles/run.invoker` on the function (for the Pub/Sub trigger) |

**Limits:** Google's cost data lags by hours, so spend can overshoot the cap a little; the cap sits about ₹1,000–2,000 below the credit for that reason. Disabling billing stops services. Resources can be deleted if billing stays off, so re-link quickly if it fires.

**Check it without changing anything** (only verifies permissions):

```bash
gcloud pubsub topics publish budget-alerts --project shipcube-control-ks --message '{"costAmount": 1}' --attribute dryRun=true
```

```bash
gcloud logging read 'resource.labels.service_name="budget-killswitch" AND textPayload:"dry run"' --project shipcube-control-ks --freshness=10m --format="value(textPayload)"
```

**After it fires**, re-link a project:

```bash
gcloud billing projects link shipcube-control-ks --billing-account=<BILLING_ACCOUNT_ID>
```

**Adding a project to the billing account?** Add it to `PROJECTS` and grant the two roles, or it won't be covered.
