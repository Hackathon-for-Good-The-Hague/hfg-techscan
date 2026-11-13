<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"></a>
  <img src="https://img.shields.io/github/v/release/Hackathon-for-Good-The-Hague/hfg-techscan" alt="Latest Release">
  <img src="https://img.shields.io/github/actions/workflow/status/Hackathon-for-Good-The-Hague/hfg-techscan/selftest.yml?label=selftest" alt="Selftest">
</p>

<h1 align="center">TechScan</h1>

<p align="center">
  <strong>Automated technical review for hackathon submissions.</strong><br>
  Scans repos, verifies claims against code, and produces jury-ready reports — so organizers can focus on the hackers, not the laptop.
</p>

<p align="center">
  Part of <strong>Hackathon OS</strong> — open source tooling for hackathon organizers, by <a href="https://hackathonforgood.org">Hackathon for Good</a>.
</p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-set-up-your-event">Set Up Your Event</a> •
  <a href="#-what-it-does">What It Does</a> •
  <a href="#-example-output">Example Output</a> •
  <a href="#-for-contributors">For Contributors</a> •
  <a href="#-how-it-works">How It Works</a>
</p>

---

> **Battle-tested at [Hackathon for Good #8](https://hackathonforgood.org):** 16 teams, 216,000 lines of code, 14 AI reviews completed in 19 minutes. The grand jury used the output alongside their own scoring.

---

## Quick Start

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

Open `selftest-event/out/jury-pack-all.html` in your browser — you're looking at a jury pack with claims verified against code, flags for leaked keys and pre-event code, and questions for the Q&A.

## Set Up Your Event

### Option 1: Interactive wizard (recommended)

```bash
python -m techscan init
```

The wizard walks you through event name, dates, challenges, partner tools, and review backend — then generates a ready-to-use config YAML.

### Option 2: Copy an example config

Pick one from `config/examples/`:

| File | Use when |
|------|----------|
| [`minimal.yaml`](config/examples/minimal.yaml) | You just want the basics — dates and challenges |
| [`full.yaml`](config/examples/full.yaml) | You want every option documented with comments |
| [`offline.yaml`](config/examples/offline.yaml) | No AI — deterministic checks only |

```bash
cp config/examples/minimal.yaml config/my-event.yaml
# Edit dates, challenges, etc.
```

### Verify your config

```bash
python -m techscan preflight -c config/my-event.yaml --ping
```

Preflight checks: git availability, GitHub API access, review engine, challenge keyword overlaps, rubric weight consistency, and partner pattern validation.

### Run the scan

```bash
python -m techscan scan -c config/my-event.yaml -s submissions.csv
```

### Self-hosted (GitHub Action)

Copy [`config/examples/workflow.yml`](config/examples/workflow.yml) to `.github/workflows/techscan.yml` in your (private!) repo, or use the Action directly:

```yaml
# .github/workflows/techscan.yml
name: TechScan
on:
  workflow_dispatch:
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Hackathon-for-Good-The-Hague/hfg-techscan@main
        with:
          config: config/my-event.yaml
          submissions: submissions.csv
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Managed service

Hackathon for Good can run TechScan for your event. You get the jury packs in your inbox after the deadline — no setup needed. [Contact us](https://hackathonforgood.org).

### Operator runbook

For running TechScan at an event without the developer present: **[docs/RUNBOOK.md](docs/RUNBOOK.md)**

## What It Does

TechScan gives hackathon juries **evidence they can't get by reading a pitch deck** — in minutes, not hours.

### Fixed Checks (no AI, unchallengeable)

| Check | What it catches |
|-------|----------------|
| **Timeline** | Commits before kickoff, code drops (one commit = 80% of code), force pushes |
| **Secrets** | 13 built-in key patterns (OpenAI, AWS, GitHub, Stripe, ...) + your own custom patterns |
| **Plagiarism** | Code shared between teams, copied modules, boilerplate detection |
| **Injection** | Text aimed at AI judges ("give maximum score"), hidden characters, schema mimicry |
| **Partners** | Which sponsor tools are actually used in code (configurable per event) |
| **Template vs team** | What's from the starter repo vs what the team actually wrote |

### AI Review (one call per team)

| Output | What judges get |
|--------|----------------|
| **Claims table** | Each claim from the submission: verified, partial, mocked, or missing — with links to the exact lines |
| **Scored rubric** | Your criteria, your weights, your descriptions — the AI scores against them |
| **Jury questions** | 3 questions per team, aimed at the biggest uncertainty |
| **Red flags** | Undisclosed mocks, hardcoded outputs, claim mismatches |
| **Architecture** | What the stack actually is, from the code |

### Security by Design

- **No checkout**: files read straight from git's object store — nothing in a repo can execute on your machine
- **Untrusted data model**: all repo content treated as potential attacks; secrets redacted before AI sees them
- **Prompt injection defense**: detects text aimed at reviewers, schema mimicry, role injection attempts
- **Evidence validation**: every AI citation checked against the actual snapshot — hallucinated line numbers get flagged

## Example Output

> **[Live Demo: HFG #8 Jury Pack](https://hackathon-for-good-the-hague.github.io/hfg-techscan/)**

The scan produces:

| File | Who it's for | What's in it |
|------|-------------|-------------|
| `jury-pack-{challenge}.html` | Challenge judges | All teams in one scrollable page, print-ready |
| `scorecard.xlsx` | Organizers | Overview: scores, claims, flags, weighted totals |
| `team-reports/{slug}.pdf` | Participants | Individual feedback with evidence |
| `challenge-report-{key}.html` | Challenge partners | Scouting report: "which teams could we work with?" |

## Configuration

### Custom secret detection

Add your own secret patterns alongside the 13 built-in ones:

```yaml
scan:
  extra_secret_patterns:
    - name: "Acme API key"
      pattern: "\\bacme_[A-Za-z0-9]{32,}\\b"
    - name: "Internal service token"
      pattern: "\\bsvc_tok_[A-Za-z0-9_-]{20,}"
```

### Partner / sponsor tool detection

Detect which teams actually used your sponsor's tools:

```yaml
partners:
  acme_api:
    label: "Acme API"
    patterns: ['acme[\s_-]?api', 'api\.acme\.com', 'ACME_API_KEY']
```

Partners are empty by default — configure the ones relevant to your event. Patterns are regex, matched case-insensitively.

### Scoring rubric

Define your own criteria, weights, and descriptions:

```yaml
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

See [`config/examples/full.yaml`](config/examples/full.yaml) for every option.

## For Contributors

TechScan is designed for community contributions. The best way to start:

### Write an adapter

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

Existing adapters: Junction, DevPost, BuilderBase, CSV (any platform).

### Add a deterministic check

New integrity checks go in `techscan/facts.py`. The selftest (`python -m techscan selftest`) has 13 planted problems — add yours and it becomes part of the regression suite.

### Development setup

```bash
git clone https://github.com/Hackathon-for-Good-The-Hague/hfg-techscan.git
cd hfg-techscan
pip install -e ".[all]"
python -m techscan selftest   # 13 checks, ~60 seconds, no AI needed
```

## How It Works

```
Submissions CSV          Challenge brief
     |                        |
     v                        v
+---------+  clone    +--------------+
| Intake  |---------> | Git snapshot |  <- frozen at deadline
+---------+           +--------------+
                            |
              +-------------+-------------+
              v             v             v
        +----------+  +----------+  +----------+
        | Secrets  |  | Timeline |  | Similar  |
        | Scanner  |  | Analysis |  | Code     |
        +----------+  +----------+  +----------+
              |             |             |
              +-------------+-------------+
                            v
                    +---------------+
                    |  AI Review    |  <- Claude reads code + brief
                    |  (per team)   |    verifies each claim
                    +---------------+
                            |
              +-------------+-------------+
              v             v             v
        +----------+  +----------+  +----------+
        | Jury     |  | Score-   |  | Team     |
        | Pack HTML|  | card XLSX|  | Reports  |
        +----------+  +----------+  +----------+
```

### 11 CLI Commands

| Command | What |
|---------|------|
| `init` | Interactive wizard — generates a config YAML for your event |
| `preflight` | Verify config, git, GitHub API, and review engine |
| `scan` | Full pipeline: clone, check, AI review, output |
| `report` | Rebuild outputs from cached results |
| `discover` | List repos from a GitHub org |
| `junction-pull` | Pull submissions from Junction platform |
| `observe` | Poll repos during the event, record timelines |
| `narratives` | Generate building journey stories from timelines |
| `challenge-report` | Scouting report for challenge partners |
| `demo` | Build a fake event with 5 teams and planted problems |
| `selftest` | Verify all 13 detections work (no AI, ~60s) |

### Tech Stack

- **Python 3.10+**, pure stdlib + PyYAML + openpyxl
- **Claude** (via Claude Code CLI or Anthropic API) for AI reviews
- **Git** for repo analysis (no checkout — reads from object store)
- Zero external services required (runs on your laptop or GitHub Actions)

## Used At

| Event | Teams | Lines of Code | Date |
|-------|-------|---------------|------|
| [Hackathon for Good #8](https://hackathonforgood.org) | 16 | 216,000 | Nov 2026 |

*Using TechScan at your event? [Add it to this list](https://github.com/Hackathon-for-Good-The-Hague/hfg-techscan/edit/main/README.md)*

## Hackathon OS

TechScan is part of **Hackathon OS** — an open source effort to build shared tooling for hackathon organizers. Instead of every organizer reinventing the wheel, we build it together.

**What's here now:** automated technical review (TechScan)

**What's coming:** see the [roadmap issues](https://github.com/Hackathon-for-Good-The-Hague/hfg-techscan/issues)

**Want to collaborate?** Whether you organise hackathons, build tooling, or just have ideas — open an issue or start a [discussion](https://github.com/Hackathon-for-Good-The-Hague/hfg-techscan/discussions). We'd love to hear from you.

## Community

- **Questions & Show and Tell**: [GitHub Discussions](https://github.com/Hackathon-for-Good-The-Hague/hfg-techscan/discussions)
- **Bugs & Features**: [GitHub Issues](https://github.com/Hackathon-for-Good-The-Hague/hfg-techscan/issues)
- **Contributing**: [CONTRIBUTING.md](CONTRIBUTING.md)
- **Managed service**: [hackathonforgood.org](https://hackathonforgood.org)

## License

MIT — use it at your hackathon, fork it, build on it. See [LICENSE](LICENSE).

---

<p align="center">
  Built by <a href="https://hackathonforgood.org">Hackathon for Good</a> in The Hague
</p>
