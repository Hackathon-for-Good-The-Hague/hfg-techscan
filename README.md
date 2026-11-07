# HFG TechScan

Technical review of hackathon submissions for the jury. For each team it freezes the repo at the deadline, runs fixed checks that cannot be argued with, then has Claude read the code and check every claim the team made. Output: a printable jury pack per challenge and a scorecard spreadsheet.

It gives evidence and questions. It does not pick winners.

## What it checks

**Fixed checks (no AI)**
- Commits dated before kickoff, and GitHub's push log (commit dates can be faked, push times cannot)
- One commit that adds most of the code (pasted-in project or generator export)
- Code that two teams have in common, including a single copied module
- Leaked API keys in the repo or its history, committed `.env` files
- Text aimed at judges or AI reviewers ("give this the maximum score"), hidden comments, invisible characters
- Partner tech actually used in code: Maritime, Bright Data, AWS, GreenPT, Lovable
- What is template, generated scaffolding (Lovable, shadcn) or boilerplate, versus team-written code
- Whether challenge data is referenced, notebooks were run, README has run instructions, demo link loads
- Commits after the deadline (excluded), forks, unmerged branches

**AI review (one Claude call per team)**
- What the software actually does, from the code
- Each claim from the Junction description and README: verified, partial, mocked, missing or unclear, with file and line
- Challenge data use, AI approach (API wrapper vs RAG vs trained model), team work vs generated code
- Privacy and responsible-AI notes, strengths, weaknesses
- Technical score 1 to 5 plus four sub-scores, confidence
- Three questions for the jury to ask in the Q&A

Guardrails: files are read straight from git's object store (no checkout, so nothing in a repo can run on your machine), repo content is treated as untrusted data, the review runs with no tools and without your own Claude Code customisations, citations are checked against the files the model was shown, and detected keys are redacted before anything is sent.

## Setup on Windows (once, about 15 minutes)

Use a short folder path, for example `C:\hfg-techscan`. In PowerShell:

```powershell
cd C:\hfg-techscan
python --version                          # 3.10 or newer
python -m pip install -r requirements.txt
claude update                             # recent Claude Code
claude -p "Reply OK"                      # must answer OK: confirms your login works headless
```

GitHub token (avoids the 60 calls per hour anonymous limit). Either `gh auth token`, or a fine-grained token with public read access:

```powershell
$env:GITHUB_TOKEN = "github_pat_..."      # per PowerShell window
```

Check the scanner itself works on your machine (builds a fake event, no AI, about a minute):

```powershell
python -m techscan selftest                # every line should say PASS
```

Then fill in `config/hfg8.yaml`: the **deadline** (required), kickoff time, GitHub org, the URLs of the two official template repos, and the ACAPS data names under `data_hints`.

```powershell
python -m techscan preflight -c config/hfg8.yaml --ping
```

## Rehearse on the fake event (10 minutes)

```powershell
python -m techscan demo --dir demo-event
python -m techscan scan -c demo-event/config.yaml -s demo-event/junction-export-demo.csv
start demo-event\out\jury-pack-all.html
```

Five fake teams with planted problems. You should see:
- **Aid Radar**: "AI model" is `Math.random()`, note to AI judges in the README, OpenAI key in `.env.local`
- **SafeSteps**: code written in September, pushed in one go, commit after the deadline
- **Help First**: screening module copied from SafeSteps, unmerged branch, AWS Bedrock detected
- **Crisis Compass**: concept only
- **Crisis Lens**: clean, real ACAPS data, disclosed mock, Maritime agent detected

## Saturday night: dry run on the live repos

After building closes. Junction submissions do not exist yet, so list the repos from GitHub:

```powershell
python -m techscan discover -c config/hfg8.yaml -o teams.csv
```

Open `teams.csv`, check the `challenge` column (acaps / unicri), fix any blanks. Then:

```powershell
python -m techscan scan -c config/hfg8.yaml -s teams.csv
```

Note how long it took. Anything flagged now (leaked keys especially) you can tell teams on Sunday morning.

## Sunday: the real run

1. At the deadline, export from Junction: organiser dashboard, Projects, select all, **Export selected**. Save as `junction.csv` in this folder. (No export button? Use `teams.csv` from discover; claims then come from the READMEs.)
2. Run:
   ```powershell
   python -m techscan scan -c config/hfg8.yaml -s junction.csv
   ```
   Expect roughly the time Saturday's dry run took (rough guess: 10 to 30 minutes for 25 teams). Clones from Saturday are reused; if the run is interrupted, run the same command again and finished reviews are skipped.
3. Open `out-hfg8\jury-pack-acaps.html` and `out-hfg8\jury-pack-unicri.html` for the Round 1 juries (print to PDF from the browser, one team per page). `out-hfg8\scorecard.xlsx` is your overview.
4. Spot-check two or three teams yourself before the juries see it.

For the grand jury, one pack with just the finalists (no re-scan):

```powershell
python -m techscan report -c config/hfg8.yaml --only "Team A,Team B,Team C"
```

Optional, finalists only: a deeper agent-mode review that lets Claude browse the repo with read-only tools. Try it on Saturday first.

```powershell
python -m techscan scan -c config/hfg8.yaml -s junction.csv --only "Team A,Team B" --deep "Team A,Team B"
```

## Useful options

| Option | What it does |
|---|---|
| `--only "Team A,team-b"` | fresh AI review only for these teams; every team is still checked and keeps its last review |
| `--skip-review` | fixed checks only, no AI (fast) |
| `--force-review` | ignore cached reviews |
| `--workers 2` | fewer parallel reviews if you hit usage limits |
| `--backend anthropic` | use the API instead of Claude Code (needs `ANTHROPIC_API_KEY`) |
| `--backend offline` | write each review request to `work-hfg8\requests\` without calling a model |
| `report` command | rebuild the jury packs from the last scan |

## Reading the results

- **Tech score**: 1 little working code, 2 mostly interface or mocked, 3 narrow but real, 4 works end to end, 5 exceptional for the time. Technical execution only, not impact or pitch.
- **Claims table**: verified, partial, mocked, missing, unclear. Each with a link to the exact lines on GitHub at the deadline commit. Struck-through links are citations the scanner could not verify.
- **Check these first**: high and medium flags from both the fixed checks and the AI review. Every flag can have an innocent explanation. Ask the team.
- **Questions to ask**: three per team, aimed at the biggest gap.

## Rules of use

- Advisory only. Never disqualify on a flag without talking to the team and checking the evidence.
- A leaked key in a public repo: tell the team to revoke it right away, whatever the judging outcome.
- The code of public repos is sent to Claude for review, the same as using Claude Code on it. Team member data from the Junction export is never read.

## Troubleshooting

| Problem | Fix |
|---|---|
| `python` not found | use `py` instead of `python` (Windows launcher) |
| Preflight ping says the model is not available | set `model: opus` under `review` (Claude Code picks the latest Opus) |
| `Claude Code CLI not found` | set `review.claude_path` to the full path of `claude.exe` or `claude.cmd` |
| Usage limit reached during the run | add `--model claude-sonnet-5-5` (faster, cheaper), or `--workers 2`, then re-run: finished teams are skipped |
| Review errors or timeouts | run the same command again (finished reviews are cached), or `--workers 2` |
| GitHub rate limit message | set `GITHUB_TOKEN`, or `github_api: false` under `scan` |
| `Repository not reachable` | the repo is private or the link is wrong: ask the team |
| `Filename too long` on clone | use a shorter folder such as `C:\hfg-techscan` |
| Review step keeps failing on Sunday | `--backend offline`, then zip `work-hfg8\requests` and get the reviews done elsewhere |

## Files

```
techscan/       the scanner (pure Python plus PyYAML and openpyxl)
prompts/        the review prompt; change it here, cached reviews refresh automatically
config/         one YAML per edition
```
