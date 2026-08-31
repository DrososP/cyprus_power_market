# Deploying the dashboard

Two routes, and they solve different problems.

| | **A — Streamlit Community Cloud** | **B — Your own server** |
|---|---|---|
| Cost | free | ~€4/month |
| Works with your PC off | yes | yes |
| Permanent URL | yes | yes |
| Privacy | Google sign-in inside the app | Tailscale, or Cloudflare Access with a domain |
| Data refresh | GitHub Actions, daily | container scheduler, daily |
| Setup | ~30 minutes | ~an hour |

Route A is the one to start with. Route B is worth it if TSOC blocks GitHub's
IP ranges, or if you want the raw archive on a disk you control.

There is also a **quick tunnel** at the end of this file for showing someone the
copy running on your own machine, right now, with no deployment at all.

---

# Route A — Streamlit Community Cloud

## A1. Build the data and push it

Community Cloud has no persistent disk, so the app reads Parquet committed to
the repo. `.gitignore` is already set up to allow exactly that: the seven files
the dashboard needs, and nothing else.

```bash
python tsoc_bm.py parse
python tsoc_build.py
git add -A && git commit -m "parquet build" && git push
```

That is about 2.4 MB. `data/raw/`, `data/series/`, `data/tidy/` and the DuckDB
catalog all stay out of the repo, as does `panel_30min.parquet` — the largest
file, rewritten in full every day, and read by nothing in the dashboard.

The repo can be private. The **app URL is public regardless** — Community Cloud
has no private tier — which is what the sign-in below is for.

## A2. Deploy

Go to [share.streamlit.io](https://share.streamlit.io), sign in with GitHub,
**Create app**, and point it at your repo with `dashboard.py` as the main file.
First build takes a few minutes while it installs the requirements.

At this point the app is live and **open to anyone with the link**. If that is
genuinely fine — this is public TSOC data — you can stop here. Otherwise:

## A3. Add the Google sign-in

The gate is already in `dashboard.py`. It switches itself on only when an
`[auth]` block exists in secrets, so local development stays open and needs no
credentials.

**Create the OAuth client:**

1. [console.cloud.google.com](https://console.cloud.google.com) → new project.
2. **APIs & Services → OAuth consent screen** → *External*. Leave it in
   **Testing** mode and add each viewer under *Test users*. Testing mode caps
   at 100 users and needs no Google verification review — and it is a second
   allowlist on top of the app's own.
3. **Credentials → Create credentials → OAuth client ID → Web application.**
4. Under *Authorised redirect URIs* add exactly:
   `https://YOUR-APP.streamlit.app/oauth2callback`
5. Copy the client ID and client secret.

**Then in Community Cloud → your app → Settings → Secrets**, paste:

```toml
allowed_emails = [
  "you@example.com",
  "colleague@example.com",
]

[auth]
redirect_uri = "https://YOUR-APP.streamlit.app/oauth2callback"
cookie_secret = "a-long-random-string"
client_id = "xxxxxxxx.apps.googleusercontent.com"
client_secret = "GOCSPX-xxxxxxxx"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

Generate the cookie secret with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

`.streamlit/secrets.toml.example` in the repo is the same thing as a template.
Never commit the real file — `.gitignore` excludes it.

**Both lists matter.** `allowed_emails` is what makes the app private; without
it, anyone with any Google account can sign in.

## A4. Turn on the daily refresh

`.github/workflows/refresh.yml` runs at 05:00 UTC: scrape → parse → build →
commit the Parquet, which redeploys the app. It needs no secrets or tokens.

Run it once by hand first — **Actions → refresh data → Run workflow** — and
watch what happens, because two things are genuinely uncertain until tried:

- **TSOC may throttle GitHub's IP ranges.** The scrape steps are
  `continue-on-error`, so a block shows up as a skipped step rather than a
  broken site, but you would see the data stop moving. That is the signal to
  switch to Route B.
- **The first run has a cold cache** and will try to fetch a lot. `data/raw/`
  is held in the Actions cache between runs, keyed to restore the most recent;
  the job is capped at 45 minutes so a cold start cannot run away.

Two things to know about the schedule: GitHub disables scheduled workflows in
repos with no activity for 60 days, and scheduled runs are queued rather than
punctual, so 05:00 means "shortly after".

## A5. Watch the git history

Committing Parquet daily grows the repo — roughly 1.5 MB a day, so a few
hundred MB a year. Excluding `panel_30min.parquet` already removes the worst of
it. If it becomes a nuisance, squash the history once a year, or move the data
to an orphan `data` branch force-pushed each run so it keeps no history at all.

---

# Route B — Your own server

Two containers off one image sharing a volume: the app, and a scheduler that
runs the pipeline daily. The app binds to localhost only, so the host never
opens a port.

## B1. A host

| Option | Rough cost | Notes |
|---|---|---|
| Hetzner CX22 (2 vCPU / 4 GB) | ~€4/month | More than enough; the dataset fits in RAM |
| Any always-on machine | free | A home server works — the tunnel means no port forwarding |
| Render / Railway | ~$7–10/month | Managed, but disks and cron are paid add-ons |

Cloudflare is not on this list on purpose: Pages serves static files only,
Workers runs short-lived JavaScript with 10 ms of CPU per request, and
Containers has no free tier and no persistent volume. Cloudflare is the front
door here, not the host.

```bash
curl -fsSL https://get.docker.com | sh
git clone <your-repo> /opt/tsoc && cd /opt/tsoc
docker compose up -d --build
```

## B2. Seed the archive

Copy `data/` up rather than re-scraping it — you already hold the files, and
re-downloading hundreds of them is slow and discourteous to a rate-limited
server.

```bash
scp -r "path/to/Cyprus power market/data" root@YOUR_HOST:/tmp/seed
ssh root@YOUR_HOST '
  cd /opt/tsoc &&
  docker compose cp /tmp/seed/. scheduler:/app/data/ &&
  rm -rf /tmp/seed &&
  docker compose restart app
'
```

Then set `RUN_ON_START: "0"` in `docker-compose.yml` so a restart doesn't kick
off a scrape you didn't ask for, and `docker compose up -d`.

## B3. Private access

**Which option applies depends on whether you own a domain.** Cloudflare Access
attaches its policy to a hostname in your own Cloudflare account, so it is
unavailable without one — free-subdomain services don't give you the nameserver
control it needs.

### Tailscale — free, no domain, 6 users

Nothing is ever publicly reachable; there is no URL for a stranger to find.

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
```

Change the compose port binding to `"0.0.0.0:8501:8501"` and reach it at
`http://<machine-name>:8501` from any device on the tailnet. Invite people from
the Tailscale admin console. The cost is that every viewer installs a client.

### Cloudflare Tunnel + Access — needs a domain

Free for up to 50 users. Viewers get a normal HTTPS URL and an emailed code,
with nothing to install — better for non-technical people, if you have a domain
(£8–12/year).

1. Add your domain to Cloudflare.
2. Zero Trust → **Networks → Tunnels → Create a tunnel**, run the install
   command it gives you on the host.
3. Public Hostname: `power.yourdomain.com` → `HTTP` → `localhost:8501`.
4. Zero Trust → **Access → Applications → Add a self-hosted application** on
   that hostname, policy *Allow* → include **Emails**.

Step 4 is what makes it private. A tunnel without an Access policy publishes
the app to the whole internet.

## B4. Running it

```bash
docker compose logs -f scheduler
docker compose exec scheduler ./refresh.sh    # force one now
docker compose up -d --build                  # deploy new code
```

The scheduler defaults to 05:00 UTC, for the same reason as the Action.
`refresh.sh` re-fetches a trailing 10-day window, because TSOC revises recent
days. If a build's checks fail it says so loudly and **leaves the previous
build in place** — the site stays up on the last known-good data rather than
going dark or quietly serving something wrong.

### Backups

`data/raw/` is the part you cannot regenerate if TSOC reorganises its archive:

```bash
docker run --rm -v tsoc_tsoc-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/tsoc-data-$(date +%F).tar.gz -C /data raw manifest.csv
```

---

# Showing someone the dashboard right now

No server, no domain, no account — share the copy running on your own machine.

Leave Streamlit running in one terminal, then in a second:

```powershell
winget install --id Cloudflare.cloudflared
# close and reopen the terminal so PATH picks it up
cloudflared tunnel --url http://localhost:8501
```

It prints a random `https://….trycloudflare.com` URL. Send that; Ctrl+C kills
it.

This is an outbound connection only — no port is opened on your machine and
your home IP stays hidden. What it has no equivalent of is an Access policy, so
**anyone with the URL is in**, and the URL changes on every restart. Your PC
must stay awake with the app running.

---

# Before making it public

If access ever widens beyond a handful of people, check TSOC's site terms and
label the site clearly as unofficial with attribution to tsoc.org.cy. The data
is published publicly and there is no API or stated licence either way — which
is a reason to be visibly courteous about it, not a reason to assume permission.
