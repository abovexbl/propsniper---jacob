# PropSniper ↔ Machine ↔ Website — Data Plane

How PropSniper data flows into the learning machine (the optimizer) and back out,
and how the website reflects it live. The real PropSniper feed is still being wired,
so every connector degrades gracefully — point it at real exports when ready, no
code change.

```
   PropSniper                  integrations/propsniper.py            machine                 website
 ┌────────────┐   INBOUND   ┌───────────────────────────┐   ┌──────────────────┐   ┌──────────────┐
 │ bet history │ ─────────► │ normalize_bets             │ ─►│ optimizer        │ ─►│ docs/feed.json│─► dashboards
 │ (data-panel)│            │  (flexible column map)     │   │ (Bayesian bandit)│   │  + data_sources│   (live poll)
 ├────────────┤            ├───────────────────────────┤   ├──────────────────┤   └──────────────┘
 │ community   │ ─────────► │ normalize_community        │ ─►│ community join   │
 │ panel       │            │  (JSON or CSV)             │   │ (vs personal)    │
 └────────────┘            └───────────────────────────┘   └──────────────────┘
        ▲                                                            │
        │                         OUTBOUND                          ▼
        └───────────────  docs/actions.csv  ◄──────────  export_action_list
            (operator applies recommended actions/stakes back in PropSniper)
```

## Where it's wired (single source of truth)
`integrations/sources.json` declares where each input lives. Defaults point at the
**sample** files so the demo runs. To go live, edit the paths (or create
`integrations/sources.local.json`, gitignored):

```jsonc
{
  "propsniper_bets":      { "path": "data/data-panel-export-LATEST.csv" },
  "propsniper_community": { "path": "data/propsniper_community_panel.csv" },
  "oddspapi":             { "path": "data/oddspapi_mlb_summary_main.csv.gz", "enabled": true }
}
```

`run_machine.py` reads this, runs the machine, and writes a `data_sources` block in
the feed so the dashboard shows each source's state: **live / sample / not_connected**,
row count, and freshness.

## INBOUND — formats the connector accepts
- **Bet history** (`normalize_bets`, via `pslib.load_bets`): any CSV with rule-id /
  stake / profit / date columns. Common header variants are auto-detected
  (`ruleId`/`rule_id`/`Rule`, `stake`/`wager`, `profit`/`netProfit`, `datePlaced`/`createdAt`…).
- **Community/consensus** (`normalize_community`): either JSON already in our shape
  (`{markets:[{league,market,community_edge,community_brier,community_n,hit_rate}]}`)
  or a flexible CSV (aliases: `edge`/`avgEV`/`community_roi`, `brier`, `n`/`sample`,
  `hit_rate`/`winRate`). Per-market, keyed on (league, market).

## OUTBOUND — recommendations back to PropSniper
`export_action_list` writes `actions.csv` every run: `rule_id, label, action,
recommended_stake_abs, edge, p_bleeding` for the default window. This is the
operator's apply-list (PROMOTE/HOLD/EXPLORE/SHRINK/DISABLE). It is **advisory** and
config changes still pass `/audit` before deploy (CLAUDE.md #1 rule). `actions.csv`
is gitignored (it mirrors real performance).

## Status surfacing
- **Dashboards**: a "data sources" badge row shows PropSniper bets / Community /
  OddsPapi state + rows + latest date, updated every 60s poll.
- **Connector self-check**: `python integrations/propsniper.py` prints the live
  sync status as JSON.

## In progress / next
- [ ] Point `sources.json` at the real PropSniper data-panel + community exports.
- [ ] OddsPapi connector: fold `walk_forward.py` Brier output into the feed so
      stability gates book swaps automatically.
- [ ] Direct API pull (if PropSniper exposes one) instead of file drops, with the
      token read from env — never committed.
- [ ] Close the outbound loop: turn `actions.csv` into pre-validated challenger
      configs (via `randomize_stack.py`) that pass `/audit` before the operator deploys.

Keep real data on the private path (`serve_local.py` + gitignored `data/`/`live/`);
the public site stays on sample.
