# Remote access to the private dashboard (password-gated)

Goal: reach your real-data dashboard from anywhere, behind a password, without
putting the data on any public host. `serve_local.py` already enforces the
password server-side (it gates the page **and** the feed). You just need a way to
reach your machine.

Pick one.

---

## Path 2a — Tunnel in front of the local server (recommended, lightest)

Your machine serves the data; a tunnel gives you a public HTTPS URL that still
hits your password. Data is served live from your machine and is never stored on a
third-party host.

**1. Start the gated dashboard with your password:**
```bat
set PROPSNIPER_DASH_PW=your-password
python serve_local.py --configs live/ --bets data/data-panel-export-LATEST.csv
```
(username is `propsniper`)

**2. In a second terminal, start a tunnel to port 8799.** Either tool works:

Cloudflare (no account needed for a quick tunnel):
```bat
cloudflared tunnel --url http://127.0.0.1:8799
```
…or ngrok (free account):
```bat
ngrok http 8799
```

Both print an `https://…` URL. Open it from any device → browser prompts for the
password → real data renders. The tunnel terminates HTTPS, so the password travels
encrypted.

**Trade-off:** access works only while your machine + `serve_local.py` + the tunnel
are running. Good enough for "check it when I want to."

---

## Path 2b — Always-on hosted (for 24/7 access)

If you want it reachable when your machine is off, deploy the static dashboard +
feed to a host with built-in access protection, so **both** the page and the feed
sit behind auth (never on public GitHub).

Options that gate the whole deployment:
- **Cloudflare Access** — free for a handful of users; gates by email/OTP or a
  service token.
- **Netlify / Vercel password protection** — site-wide password (paid tiers).

Flow: a scheduled `run_machine.py` writes `feed.json` into the deployment's private
storage; the host serves dashboard + feed only to authenticated visitors.

**What's yours to set up** (I can't do these): create the host account, enable the
password/access protection, and provide the deploy target. Once that exists I'll
wire the publish step so each machine pulse updates the private feed.

---

## What NOT to do
- Don't put real `feed.json` on the public GitHub repo / raw URL — it's readable by
  anyone regardless of any front-end password.
- Don't rely on a client-side (JavaScript) password on a static site — it's
  bypassable. Auth must be server-side (Path 2a) or platform-level (Path 2b).
