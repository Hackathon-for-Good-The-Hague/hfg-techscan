# Running TechScan for HFG (without Martin)

This is the step-by-step guide for running the technical scan at a Hackathon for Good event. You don't need to understand the code — just follow these steps.

## Before the event (1 hour, one-time setup)

### 1. Edit the config

Copy `config/hfg8.yaml` to `config/hfg9.yaml` (or whatever edition). Edit:

```yaml
event:
  name: "Hackathon for Good #9"
  start: "2027-05-14T17:00:00+02:00"    # Friday kickoff time + UTC offset
  deadline: "2027-05-16T10:00:00+02:00"  # Sunday submission deadline

challenges:
  challenge1:
    name: "Challenge Name"
    match: ["keyword1", "keyword2"]       # words that identify this challenge
    data_hints: ["dataset-name"]          # datasets teams were given
```

### 2. Set up GitHub secrets

In the repo Settings → Secrets and variables → Actions, add:

| Secret | What | Where to get it |
|--------|------|-----------------|
| `JUNCTION_CLIENT` | Clerk `__client` cookie | Browser DevTools → Cookies → `.clerk.hackjunction.app` |
| `SCAN_GITHUB_TOKEN` | GitHub PAT with `public_repo` scope | github.com/settings/tokens |
| `ANTHROPIC_API_KEY` | Anthropic API key | console.anthropic.com |
| `SMTP_HOST` | Email server (e.g. `smtp.gmail.com`) | Your email provider |
| `SMTP_USER` | Email address to send from | — |
| `SMTP_PASS` | Email password or app password | — |
| `DISCORD_WEBHOOK` | Discord webhook URL | Discord channel → Edit → Integrations → Webhooks |

Also set the **variable** (not secret):

| Variable | What | Example |
|----------|------|---------|
| `NOTIFY_EMAILS` | Comma-separated recipient emails | `jury@hfg.org, acaps@acaps.org` |

### 3. Update the Junction event ID

In the config, set the numeric event ID from Junction's admin dashboard:

```yaml
junction:
  event_slug: "hackathon-for-good-9"
  event_id: 21  # check getEventById response in browser DevTools
```

### 4. Refresh the Junction token

The `JUNCTION_CLIENT` cookie lasts days but may expire. On the morning of the event:

1. Open `www.junctionorganize.com` (logged in as organizer)
2. F12 → Application → Cookies → `.clerk.hackjunction.app` → `__client`
3. Copy the value, update the `JUNCTION_CLIENT` secret in GitHub

## During the event (nothing to do)

The GitHub Action runs every 5 minutes. After the deadline + 30 minutes, it will:

1. Pull submissions from Junction
2. Clone all repos
3. Run deterministic checks (secrets, timeline, plagiarism, etc.)
4. AI-review each team with Claude
5. Generate jury packs, scorecard, and per-team reports
6. Email the ZIP to the addresses in `NOTIFY_EMAILS`
7. Post a summary to Discord

**You don't need to be at a laptop.**

## If something goes wrong

### The scan didn't run

Check the Actions tab in GitHub. Look for the "Scheduled Scan" workflow. Common issues:

- **"Too early"**: The deadline hasn't passed yet (+ 30 min grace). Wait or use "Run workflow" with `force: true`.
- **"JUNCTION_CLIENT not set"**: The secret expired. Refresh it (step 4 above).
- **"Repository not reachable"**: A team's repo is private. They need to make it public before the scan.

### Re-running the scan

Go to Actions → "Scheduled Scan" → "Run workflow". Set `force: true` to override the lockfile. Optionally provide a submissions CSV path.

### A team's repo was private

The scan marks them as "unreachable" and continues. When they make it public, re-run the scan. Their results will appear in the next run.

### The AI review failed for one team

The scan continues for everyone else. The team shows up in the output without a review (deterministic checks still apply). To retry just that team:

```bash
python -m techscan scan -c config/hfg9.yaml -s submissions.csv --only "team-slug" --force-review
```

### Email didn't arrive

Check the Actions log for the "Send email notification" step. The results ZIP is always available as a GitHub Actions artifact (downloadable from the workflow run page).

## Manual run (fallback)

If the automation doesn't work, you can always run it by hand:

```bash
# Set tokens
export GITHUB_TOKEN="ghp_..."
export JUNCTION_CLIENT="eyJ..."

# Option A: pull from Junction
python -m techscan scan -c config/hfg9.yaml --junction

# Option B: manual CSV from Junction dashboard
# (Junction → All Projects tab → download CSV)
python -m techscan scan -c config/hfg9.yaml -s submissions.csv

# Results appear in out-hfg9/
```

## What the outputs mean

| File | Who it's for | What's in it |
|------|-------------|-------------|
| `scorecard.xlsx` | Jury, organizer | All teams: scores, claims, flags, questions |
| `jury-pack-{challenge}.html` | Challenge judges | Teams in that challenge, sorted alphabetically |
| `jury-pack-all.html` | Grand jury | Everyone in one page |
| `teams/{slug}.html` | Individual participants | That team's full review |

### Reading a team's review

- **Tech score (1-5)**: How much working code vs mocked/UI-only
- **"In short"**: Plain-language summary for non-technical judges
- **Claims table**: Each claim from the submission, verified against actual code
- **Flags**: Things to check — high (ask the team), medium (worth a look), low/info (FYI)
- **Jury questions**: Three questions suggested for the Q&A

### Scores explained

| Score | Meaning |
|-------|---------|
| 1 | Very little working code |
| 2 | Mostly UI or mocked functionality |
| 3 | Narrow but real working feature |
| 4 | Working end to end |
| 5 | Exceptional for the time available |
