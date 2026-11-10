<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/github/license/Hackathon-for-Good-The-Hague/hfg-techscan" alt="MIT License">
  <img src="https://img.shields.io/github/v/release/Hackathon-for-Good-The-Hague/hfg-techscan" alt="Latest Release">
  <img src="https://img.shields.io/github/actions/workflow/status/Hackathon-for-Good-The-Hague/hfg-techscan/selftest.yml?label=selftest" alt="Selftest">
</p>

<h1 align="center">⚡ HFG TechScan</h1>

<p align="center">
  <strong>Automated technical review for hackathon submissions.</strong><br>
  Scans repos, verifies claims against code, and produces jury-ready reports — so organizers can focus on the hackers, not the laptop.
</p>

<p align="center">
  <a href="#-try-it-in-2-minutes">Quick Start</a> •
  <a href="#-what-it-does">What It Does</a> •
  <a href="#-example-output">Example Output</a> •
  <a href="#-for-organizers">For Organizers</a> •
  <a href="#-for-contributors">For Contributors</a> •
  <a href="#-how-it-works">How It Works</a>
</p>

---

> **Battle-tested at [Hackathon for Good #8](https://hackathonforgood.org):** 16 teams, 216,000 lines of code, 14 AI reviews completed in 19 minutes. The grand jury used the output alongside their own scoring. The scanner correctly identified the winner.

---

## 🚀 Try It in 2 Minutes

```bash
# Clone and install
git clone https://github.com/Hackathon-for-Good-The-Hague/hfg-techscan.git
cd hfg-techscan
pip install pyyaml openpyxl

# Build a fake hackathon with 5 teams and planted problems
python -m techscan demo

# Verify all 13 checks catch every planted problem (no AI needed)
python -m techscan selftest
```

That's it. Open `selftest-event/out/jury-pack-all.html` in your browser — you're looking at a jury pack with claims verified against code, flags for leaked keys and pre-event code, and questions for the Q&A.

**Want to run it on real submissions?**

```bash
python -m techscan scan -c config/your-event.yaml -s submissions.csv
```

Or install from PyPI:

```bash
pip install hfg-techscan
techscan selftest
```

## 🔍 What It Does

TechScan gives hackathon juries **evidence they can't get by reading a pitch deck** — in minutes, not hours.

### Fixed Checks (no AI, unchallengeable)

| Check | What it catches |
|-------|----------------|
| 🕐 **Timeline** | Commits before kickoff, code drops (one commit = 80% of code), force pushes |
| 🔑 **Secrets** | 13 key patterns (OpenAI, AWS, GitHub, Stripe, ...) in code and git history |
| 📋 **Plagiarism** | Code shared between teams, copied modules, boilerplate detection |
| 🎯 **Injection** | Text aimed at AI judges ("give maximum score"), hidden characters, schema mimicry |
| 🤝 **Partners** | Which hackerpack partners (AWS, Maritime, Lovable, ...) are actually used in code |
| 📊 **Template vs team** | What's from the starter repo vs what the team actually wrote |

### AI Review (one call per team)

| Output | What judges get |
|--------|----------------|
| **Claims table** | Each claim from the submission: verified ✅, partial 🟡, mocked 🔴, or missing ❌ — with links to the exact lines |
| **6-criterion scores** | Concept, Prototype, Technology, Potential, Business, Wow — 1-10 with configurable weights |
| **Weighted total** | Matches your jury's formula (e.g. Concept×4 + Prototype×2 + Tech×1.5 + ...) |
| **Jury questions** | 3 questions per team, aimed at the biggest uncertainty |
| **Red flags** | Undisclosed mocks, hardcoded outputs, claim mismatches |
| **Architecture** | What the stack actually is, from the code |

### Security by Design

- **No checkout**: files read straight from git's object store — nothing in a repo can execute on your machine
- **Untrusted data model**: all repo content treated as potential attacks; secrets redacted before AI sees them
- **Prompt injection defense**: detects text aimed at reviewers, schema mimicry, role injection attempts
- **Evidence validation**: every AI citation checked against the actual snapshot — hallucinated line numbers get flagged

## 📊 Example Output

> 🔗 **[Live Demo: HFG #8 Jury Pack →](https://hackathon-for-good-the-hague.github.io/hfg-techscan/)**

The scan produces:

| File | Who it's for | What's in it |
|------|-------------|-------------|
| `jury-pack-{challenge}.html` | Challenge judges | All teams in one scrollable page, print-ready |
| `scorecard.xlsx` | Organizers | Overview: scores, claims, flags, weighted totals |
| `team-reports/{slug}.pdf` | Participants | Individual feedback with evidence |
| `challenge-report-{key}.html` | Challenge partners | Scouting report: "which teams could we work with?" |

## 👥 For Organizers

### Zero-config (managed service)

HFG can run TechScan for your event. You get the jury packs in your inbox after the deadline — no setup, no laptop, no technical knowledge needed. [Contact us →](https://hackathonforgood.org)

### Self-hosted (GitHub Action)

Add this to your hackathon's repo:

```yaml
# .github/workflows/techscan.yml
name: TechScan
on:
  workflow_dispatch:
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: Hackathon-for-Good-The-Hague/hfg-techscan@v0.2.0
        with:
          config: config/my-event.yaml
          submissions: submissions.csv
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Configure your rubric

Copy `config/hfg8.yaml` and edit:

```yaml
event:
  name: "My Hackathon 2027"
  start: "2027-03-14T17:00:00+01:00"
  deadline: "2027-03-16T10:00:00+01:00"

challenges:
  main:
    name: "Climate Tech"
    match: ["climate", "sustainability"]
    template: "https://github.com/your-org/starter-repo.git"

rubric:
  criteria:
    innovation:
      label: "Innovation"
      weight: 3
      description: "How novel is the approach?"
    execution:
      label: "Execution"
      weight: 4
      description: "Does the prototype work?"
    impact:
      label: "Impact"
      weight: 3
      description: "Could this make a real difference?"
  max_score: 10
  max_total: 100
```

Or use a community rubric: `rubrics/mlh-default.yaml`, `rubrics/school-simple.yaml`

### Operator runbook

For running TechScan at an event without the developer present: **[docs/RUNBOOK.md](docs/RUNBOOK.md)**

## 🛠 For Contributors

TechScan is designed for community contributions. The best way to start:

### Write an adapter (good first issue!)

Every hackathon platform needs an adapter — a ~50 line Python file:

```python
from techscan.adapters.base import BaseAdapter, AdapterSubmission

class LumaAdapter(BaseAdapter):
    def name(self) -> str:
        return "Luma"

    def pull(self, cfg: dict) -> list[AdapterSubmission]:
        # fetch submissions from the Luma API
        ...
```

**Adapters wanted:** Luma, MLH, HackerEarth, Unstop, Eventbrite. See [CONTRIBUTING.md](CONTRIBUTING.md).

Existing adapters: Junction, DevPost, CSV (any platform).

### Share a rubric

Drop a YAML in `rubrics/` with your hackathon's scoring criteria. See `config/hfg8.yaml` for the format.

### Add a deterministic check

New integrity checks go in `techscan/facts.py`. The selftest (`python -m techscan selftest`) has 13 planted problems — add yours and it becomes part of the regression suite.

### Development setup

```bash
git clone https://github.com/Hackathon-for-Good-The-Hague/hfg-techscan.git
cd hfg-techscan
pip install -e ".[all]"
python -m techscan selftest   # 13 checks, ~60 seconds, no AI needed
```

## ⚙️ How It Works

```
Submissions CSV          Challenge brief
     │                        │
     ▼                        ▼
┌─────────┐  clone    ┌──────────────┐
│ Intake   │────────►  │ Git snapshot │  ← frozen at deadline
└─────────┘           └──────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ Secrets  │  │ Timeline │  │ Similar  │
        │ Scanner  │  │ Analysis │  │ Code     │
        └──────────┘  └──────────┘  └──────────┘
              │             │             │
              └─────────────┼─────────────┘
                            ▼
                    ┌───────────────┐
                    │  AI Review    │  ← Claude reads code + brief
                    │  (per team)   │    verifies each claim
                    └───────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ Jury     │  │ Score-   │  │ Team     │
        │ Pack HTML│  │ card XLSX│  │ PDFs     │
        └──────────┘  └──────────┘  └──────────┘
```

### 10 CLI Commands

| Command | What |
|---------|------|
| `selftest` | Verify all 13 detections work (no AI, ~60s) |
| `demo` | Build a fake event with 5 teams and planted problems |
| `scan` | Full pipeline: clone → check → AI review → output |
| `report` | Rebuild outputs from cached results |
| `preflight` | Check git, GitHub API, review engine |
| `discover` | List repos from a GitHub org |
| `junction-pull` | Pull submissions from Junction platform |
| `observe` | Poll repos during the event, record timelines |
| `narratives` | Generate building journey stories from timelines |
| `challenge-report` | Scouting report for challenge partners |

### Tech Stack

- **Python 3.10+**, pure stdlib + PyYAML + openpyxl
- **Claude** (via Claude Code CLI or Anthropic API) for AI reviews
- **Git** for repo analysis (no checkout — reads from object store)
- Zero external services required (runs on your laptop or GitHub Actions)

## 📈 Used At

| Event | Teams | Lines of Code | Date |
|-------|-------|---------------|------|
| [Hackathon for Good #8](https://hackathonforgood.org) | 16 | 216,000 | Nov 2026 |

*Using TechScan at your event? [Add it to this list →](https://github.com/Hackathon-for-Good-The-Hague/hfg-techscan/edit/main/README.md)*

## 🤝 Community

- **Questions & Show and Tell**: [GitHub Discussions](https://github.com/Hackathon-for-Good-The-Hague/hfg-techscan/discussions)
- **Bugs & Features**: [GitHub Issues](https://github.com/Hackathon-for-Good-The-Hague/hfg-techscan/issues)
- **Contributing**: [CONTRIBUTING.md](CONTRIBUTING.md)
- **Managed service**: [hackathonforgood.org](https://hackathonforgood.org)

## 📄 License

MIT — use it at your hackathon, fork it, build on it. See [LICENSE](LICENSE).

---

<p align="center">
  Built with ❤️ by <a href="https://hackathonforgood.org">Hackathon for Good</a> in The Hague
</p>
