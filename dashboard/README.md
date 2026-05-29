# PropSniper Live Dashboard

A self-contained, regenerable version of the "+EV Operations Command Center."
Unlike the Manus-deployed snapshot, this one reflects **whatever your configs say
right now** — every `/audit` run can refresh it.

## How it works

```
configs (live/ or sample_live/)
        │
        ▼
audit/audit_configs.py --format json     ← the machine-readable bridge (new)
        │
        ▼
dashboard/build_data.py                   ← runs the audit, writes the feed
        │
        ├── dashboard/data.json           ← for CI / orchestration / scripts
        └── dashboard/data.js             ← window.DASHBOARD_DATA, for index.html
        │
        ▼
dashboard/index.html                      ← vanilla JS, no build step
```

## Run it (demo with sample data)

```bash
python dashboard/make_sample_configs.py            # writes configs/sample_live/
python dashboard/build_data.py --dir configs/sample_live
python -m http.server 8731 --bind 127.0.0.1        # from inside dashboard/
# open http://127.0.0.1:8731/index.html
```

## Run it against your real configs

```bash
python dashboard/build_data.py --dir live/         # live/ is gitignored — stays local
```

`live/` and `configs/sample_live/` both stay out of git (see `.gitignore`).

## Why a tiny HTTP server instead of opening the file?

`index.html` loads `data.js` via a `<script>` tag, so it works from `file://`
in most browsers. The HTTP server is only needed if your browser blocks
local script loads. The data is plain JSON either way.

## Refreshing on a schedule

`build_data.py` is the unit of automation. Wrap it in cron / a scheduled task /
the orchestration layer (see `../ORCHESTRATION.md`) to keep the feed warm:

```bash
*/15 * * * *  cd /path/to/propsniper-toolkit && python dashboard/build_data.py --dir live/
```

## Extending the feed

`build_data.py` currently consumes the audit JSON. To add P&L or walk-forward
panels, give those tools a `--format json` flag (same pattern as the audit
engine — add a `to_dict()` and a json branch in `main()`), then merge their
payloads into `build_payload()`.
