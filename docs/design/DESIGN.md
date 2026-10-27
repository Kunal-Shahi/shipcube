# ShipCube design system

The console is a tool people operate, not a page they read. It sits next to a coding agent: most work happens in the agent, and people open the console to check what ShipCube decided, change a limit, share an app or see why something broke. Every screen should answer "what state is this in, and what can I change?" within a glance.

Reference points: Linear (density, keyboard-first restraint), Vercel (black/white neutrality, monospace for machine values), Hex (data tables and charts that look like part of the product).

Implementation: `apps/web/src/app/globals.css` holds every token. Components live in `apps/web/src/components/ui`.

## Principles

1. **Decisions are reviewable.** When ShipCube (or Muse Spark) decides something, such as a tier, a cap or a re-placement, show the decision, its reasoning and an override next to each other.
2. **State in form, not only in numbers.** Health, freshness and drift each get a status pill with an icon and a word. Colour never carries meaning alone.
3. **Machine values look like machine values.** IDs, URLs, commands, regions and SQL use the mono face. Everything else uses the sans.
4. **Dense, not cramped.** 13px body, 32px rows, 4px grid. Whitespace separates groups; borders separate objects.
5. **Nothing decorative.** No gradients, glows, emoji, illustrations or "AI-powered" badges. Muse is named where it does work ("Estimated by Muse Spark"), the way a build log names the compiler.

## Colour

One cool neutral ramp (slightly blue-biased so it doesn't read as default grey) and one accent. Status colours are separate from the accent and are never used for anything else.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f7f8fa` | `#0d0e11` | App background |
| `--surface` | `#ffffff` | `#14161a` | Panels, tables, inputs |
| `--surface-2` | `#f1f2f5` | `#1b1d22` | Hover rows, code blocks, segmented controls |
| `--border` | `#e3e5ea` | `#262930` | Dividers, panel edges |
| `--border-strong` | `#cfd2d9` | `#363a43` | Input borders, focused panels |
| `--fg` | `#15171c` | `#eceef2` | Primary text |
| `--fg-muted` | `#5d626d` | `#9a9fab` | Secondary text, labels |
| `--fg-subtle` | `#8a8f9a` | `#6b707c` | Placeholders, axis ticks, timestamps |
| `--accent` | `#3355e0` | `#6b8aff` | Primary buttons, links, focus ring, selected nav, chart series 1 |
| `--accent-fg` | `#ffffff` | `#0d0e11` | Text on accent |
| `--accent-soft` | `#eceffd` | `#1a2140` | Selected row, accent badges |

Status (icon + label always accompany the colour):

| Token | Light | Dark | Meaning |
|---|---|---|---|
| `--good` | `#12844a` | `#3fbf7f` | Healthy, fresh, within cap |
| `--warning` | `#a96800` | `#e0a43a` | Degraded, stale, near cap (≥ 80%) |
| `--critical` | `#c93636` | `#f06a6a` | Down, failed, cap reached |

Each has a `-soft` background step for pills. Status text colours are stepped to clear 4.5:1 on `--surface` in both themes.

Charts use `--accent` for the primary series and `--fg-subtle` for the comparison series (for example p50 under p95). There are never more than two series on a console chart; anything more becomes a table.

## Type

| Role | Face | Size / line height | Weight |
|---|---|---|---|
| Page title | Geist | 20 / 28 | 600 |
| Section title | Geist | 14 / 20 | 600 |
| Body, table cells, inputs | Geist | 13 / 20 | 400 |
| Label, caption, column header | Geist | 12 / 16 | 500 |
| Metric value | Geist | 22 / 28 | 600, tabular |
| Code, IDs, commands | Geist Mono | 12.5 / 20 | 400 |

- Numbers that line up in columns use `tabular-nums` (the `.num` utility).
- Sentence case everywhere: "Load estimate", not "Load Estimate". Column headers are sentence case, not uppercase.
- Headings use `text-wrap: balance`.

## Spacing and layout

- 4px grid. Common steps: 4, 8, 12, 16, 24, 32.
- App shell: 224px sidebar, content max-width 1120px, 24px page padding (16px under 640px).
- Rows: 32px (dense tables), 36px (inputs and buttons default), 28px (small buttons).
- Radius: 6px for controls, 8px for panels. Pills are fully rounded. Nothing else gets a radius.
- Shadows: none in the shell. Menus and dialogs get one soft shadow so they read as above the page.

## Components

| Component | Notes |
|---|---|
| **Button** | `primary` (accent fill), `secondary` (surface + border), `ghost` (text only), `danger`. Sizes `sm` 28px, `md` 32px. Label is the verb ("Create token", "Grant access"). |
| **Input / Select** | 32px, `--border-strong`, accent focus ring (2px, offset 1px). Labels above, help text below in `--fg-muted`. Errors replace help text in `--critical`. |
| **Panel** | `--surface`, 1px `--border`, 8px radius. Header row with section title left, actions right. The only "card". |
| **Table** | Full-width inside a panel, 32px rows, hairline row dividers, header in `--fg-muted` 12px. Numeric columns right-aligned and tabular. Whole row is the link when a row opens something. |
| **Status pill** | Icon + word, soft background. `Healthy`, `Degraded`, `Down`, `Fresh`, `Stale`, `Deploying`. |
| **Tier badge** | Mono, bordered: `T1 · Serverless`. |
| **Code block** | `--surface-2`, mono, copy button top-right that changes to "Copied" for 1.5s. Commands never wrap mid-token; long lines scroll horizontally inside the block. |
| **Meter** | 6px track. Fill is accent under 80%, warning 80–99%, critical at 100%. Always paired with "$12.40 of $25.00". |
| **Stat** | Label (12px muted), value (22px tabular), optional delta and a short note. Used in rows of 4 on monitoring and billing, never as a page hero. |
| **Tabs** | Underline style, 13px, accent underline on the active tab. Used for app detail sections and client choice on the MCP guide. |
| **Empty state** | One sentence saying what will appear here, one sentence saying how to make it appear (usually an agent prompt or an MCP tool), one action. No illustration. |
| **Error state** | What failed, the likely cause, and a "Try again" button. Shows the request ID in mono so it can be reported. |
| **Restore window** | A deleted app shows a critical-soft banner with the date it can be restored until and the days left, plus a Restore button. Deleted apps leave the main table and appear under "Recently deleted". |
| **Loading state** | Skeleton rows that match the final layout (same row heights, same column count). No spinners for page loads; a 14px spinner only inside a pressed button. |

## Decision pages

Approvals (`/approvals/<id>`) and data model changes (`/data/migrations/<id>`) are where a person signs off on something an agent asked for. Agents link straight to them (`approval_url`), so they have to make sense with no other context. They share one layout:

- **Left, the record:** what was asked (the person's own prompt, quoted, when the agent passed it on), what changes (a now → if-approved table, with the risky value in warning or critical colour), why it needs a person, what happens next as numbered steps, Muse Spark's assessment when it has one, and the exact tool call or migration JSON.
- **Right, the decision:** a sticky panel with the expiry, an optional note to the agent, and two buttons. The approve button names the action ("Approve deploy", "Apply to the model", "Delete app"). Destructive actions use the danger style; breaking migrations need an extra acknowledgement checkbox that names what breaks.
- After deciding, the panel says what the agent does next. It doesn't redirect anywhere.
- Migration kinds use the status colours: additive is good, change is warning, breaking is critical. Catalog diffs use `+` / `~` / `−` in the same colours, always with the sign so colour isn't the only signal.

## Charts

- One y-axis, starting at zero for bars. Grid lines are hairlines in `--border`; axis text 11px in `--fg-subtle`.
- Lines 2px, bars with 4px rounded tops and 2px gaps.
- Hover shows a crosshair and a tooltip with the exact values. A legend appears when there are two series.
- Every chart has a caption saying what it measures and over what window ("p95 latency, last 24 hours").

## Voice and tone

ShipCube talks like a careful infrastructure engineer: specific, calm and brief.

- **Name things by what people manage.** "Who can open this app", not "IAM bindings". "Spending limit", not "budget policy".
- **Say what happens.** Buttons are verbs that describe the result: "Deploy", "Grant access", "Revoke", "Create token". The toast confirms in the past tense: "Access granted to finance@acme.com".
- **Show the reasoning, then the number.** "Traffic arrives in short bursts during work hours and idles overnight, so scale-to-zero Cloud Run fits. Estimated $6.80/month."
- **Errors say what went wrong and what to do.** "Couldn't reach your GCP project. The ShipCube service account lost the Cloud Run Admin role. Re-link the project or grant the role again." No apologies, no "Oops".
- **Plain words.** No "seamless", "effortless", "magic", "supercharge" or "AI-powered".
- **Numbers carry units and windows.** "$4.12 this month", "p95 312 ms over 24 h", "updated 14 min ago".
