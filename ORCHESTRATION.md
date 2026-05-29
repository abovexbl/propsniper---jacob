# PropSniper Orchestration Layer — Design

**Goal (your words):** *"CEO-level action across platforms — Manus, browser, and deploying parallel Claude agents."*

This doc specifies how the three control surfaces fit together, what the data
contract between them is, and an incremental build order. It is deliberately
honest about what exists today vs. what is still design.

---

## 1. The mental model: one spine, three arms

The thing that makes "CEO-level" coordination possible is **a single
machine-readable state feed** that every surface reads from and writes to.
Without it, the three arms can't agree on reality. We now have that spine:

```
                ┌──────────────────────────────────────────┐
                │   STATE FEED  (docs/feed.json)        │
                │   verdict · cards · findings · counts      │  ← the spine
                │   produced by run_machine.py from /audit    │
                └──────────────────────────────────────────┘
                  ▲              ▲                    ▲
        ┌─────────┘              │                    └──────────┐
        │                        │                               │
 ┌──────────────┐      ┌──────────────────┐         ┌─────────────────────┐
 │  TIER 1       │      │  TIER 2           │         │  TIER 3              │
 │  Claude       │      │  Manus            │         │  Parallel Claude     │
 │  (analyst)    │      │  (agent + site)   │         │  agents (fleet)      │
 └──────────────┘      └──────────────────┘         └─────────────────────┘
   reads code,           hosts the dashboard,          fan out one agent per
   audits, decides       runs long Manus tasks,         config file / venue /
   what runs next        renders the feed               sport, write back to feed
```

The contract is small and stable: anything that can read JSON can participate.
That is the whole trick.

---

## 2. The three tiers

### Tier 1 — Claude as analyst (exists today)
The reasoning layer. Reads the repo, runs the tools, interprets `verdict`/`findings`,
and decides what to do next. This is what produced `AUDIT_REPORT.md` and the
dashboard. It is the *only* tier allowed to make judgment calls (deploy / don't
deploy, which fix to apply). The other two tiers are muscle.

### Tier 2 — Manus (agent + hosting)
Two distinct capabilities, don't conflate them:
- **Manus site** = the public dashboard surface (`*.manus.space`). Good for a
  shareable, always-on view. Today it's a *static snapshot*; the live feed
  (`data.json`) is what replaces that.
- **Manus agent** = long-running autonomous tasks via `https://api.manus.im`.
  Good for jobs that take minutes and don't need your laptop: "re-pull OddsPapi
  exports, run walk-forward across all sports, summarize." Triggered by API key.

> ⚠️ The API key you pasted in chat is compromised — **rotate it in Manus before
> wiring anything to the API.** Then store it in a local `.env` (gitignored),
> never in the repo or a config. The orchestrator reads it from the environment.

### Tier 3 — Parallel Claude agents (fan-out)
For work that decomposes cleanly across N independent units, spawn one agent per
unit and run them concurrently. Natural decompositions here:
- **per config file** — audit + propose fixes for each `devig_*.json` in parallel
- **per venue** — jitter/differentiation analysis isolated by book
- **per sport** — walk-forward stability (`/walk-forward mlb`, `nba`, `wnba`…) in parallel
- **per bleeding rule** — one agent investigates each negative-ROI rule from `/pnl-check`

Each agent returns structured findings; a synthesis step merges them back into
the feed. (If you opt into multi-agent **workflows**, this is exactly the
fan-out → verify → synthesize shape — say the word and it gets built as a
runnable workflow script.)

---

## 3. Data contract (the spine, concretely)

`docs/feed.json` — already emitted by `run_machine.py`:

```jsonc
{
  "generated_at": "2026-05-29T18:19:13Z",
  "source_dir":   "live/",
  "verdict":      "FAIL (blocking violations)",
  "exit_code":    3,                       // 0 clean · 1 warn · 2 schema · 3 blocking
  "summary":      { "files_checked": 9, "rules_checked": 73, "errors": 14, ... },
  "cards":        { "Self-Ref Violations": 14, "Stack Collisions": 29, ... },
  "counts_by_check": { "self_reference": 14, "j_mo_no_differentiation": 29, ... },
  "findings":     [ { "severity", "check", "file", "rule_id", "message" }, ... ]
}
```

Rule: **every tool that wants to participate emits this shape** (or a superset
with its own `tool` key). Walk-forward, P&L, and the devig comparator each get a
`--format json` flag (same 5-line pattern already added to the audit engine),
and `run_machine.py` merges them into one feed with a `panels` array.

---

## 4. Control flow (how an action actually happens)

A "CEO-level action" is a decision that triggers coordinated work. Example —
*"a rule started bleeding overnight":*

```
1. SCHEDULED   run_machine.py runs /audit + /pnl-check → feed updates, exit_code flips
2. DETECT      orchestrator diffs new feed vs last; sees a new BLEEDING rule
3. FAN OUT     Tier 3: spawn agents — one re-runs walk-forward for that market,
               one checks if the rule was recently edited (audit diff),
               one compares to community Brier
4. SYNTHESIZE  merge agent verdicts → "comp stack drifted, Pinnacle now UNSTABLE"
5. PROPOSE     Tier 1 drafts the fix (swap the unstable book) — does NOT deploy
6. SURFACE     push to the dashboard + notify you
7. GATE        you approve in chat → only then is the config rewritten + re-audited
```

Steps 1–6 are automatable. **Step 7 stays human** — config deploys, anything that
moves money, and anything that publishes externally require explicit approval.
The orchestrator proposes; it never deploys a config that hasn't passed `/audit`
(this is already `CLAUDE.md`'s #1 rule — the orchestration layer enforces it).

---

## 5. Incremental build order

**Crawl (done):**
- [x] Machine-readable audit (`--format json`)
- [x] State feed builder (`run_machine.py` → `docs/feed.json` / `feed.js`)
- [x] Live, regenerable dashboard (`docs/index.html`)

**Walk (done):**
- [x] Windowed optimizer (7d/14d/30d/90d/all) + per-window actions (`optimizer/optimize.py`)
- [x] `run_machine.py` merges audit + optimizer + community into one feed (schema v2)
- [x] Community-vs-personal join (`build_community`) with beating/inline/below verdicts
- [x] Live Manus showcase polling the published feed; private gated dashboard for real data
- [ ] `--format json` on `walk_forward.py`, `feedback_loop.py` (fold their panels in too)
- [ ] Scheduled run (cron / Windows Task) keeps the feed warm
- [ ] Feed diffing → detect new BLOCKERS / bleeders between runs

**Run (needs your go-ahead per surface):**
- [ ] Tier-3 fan-out as a workflow: per-sport walk-forward, per-rule bleed investigation
- [ ] Tier-2 Manus API jobs for heavy data pulls
- [ ] Approval-gated fix proposals surfaced in the dashboard
- [ ] Champion/challenger shadow deploy with accumulated posterior evidence

---

## 6. Safety & secrets (non-negotiable)

- **Rotate the Manus API key** (exposed in chat). Store in gitignored `.env`; the
  orchestrator reads from env, never from a committed file.
- `live/` and `data/` stay gitignored — they hold real bet history and per-account
  detail. The orchestrator must refuse to push if they're present.
- **Human gates** on: config deploys, money movement, external publishing, account
  changes. Automate detection and proposal; never automate the irreversible step.
- Parallel agents are read-mostly. The one that writes (applies a fix) writes to a
  `*_new.json`, re-audits, and stops — it does not overwrite `live/` in place.

---

## 7. What exists vs. what's design

| Piece | Status |
|-------|--------|
| Audit JSON feed | **built + verified** |
| `run_machine.py` + dashboard | **built + verified** (renders sample data) |
| JSON output for other 3 tools | designed (pattern proven on audit) |
| Feed diffing / scheduling | designed |
| Manus API job-runner | designed (blocked on key rotation) |
| Tier-3 parallel fan-out | designed (1-step from a workflow script) |
| Approval-gated auto-fix | designed |

The spine is real. Each arm is now an additive, independently-shippable step.
