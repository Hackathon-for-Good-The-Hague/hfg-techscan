# Contributing to HFG TechScan

## Development setup

```bash
git clone https://github.com/Hackathon-for-Good-The-Hague/hfg-techscan.git
cd hfg-techscan
python -m pip install -e ".[all]"
```

You need Git on PATH. For AI-powered reviews, set `ANTHROPIC_API_KEY`.

## Running the selftest

The selftest creates a fake event with planted problems and verifies every
deterministic check catches them (no AI calls required):

```bash
python -m techscan selftest
```

Run this before submitting any PR.

## Writing a platform adapter

Adapters live in `techscan/adapters/`. Each adapter pulls submissions from a
hackathon platform and returns a list of `AdapterSubmission` objects.

1. Create `techscan/adapters/myplatform.py`.
2. Subclass `BaseAdapter` from `techscan.adapters.base`.
3. Implement `name()` (human-readable label) and `pull(cfg)` (returns
   `list[AdapterSubmission]`).
4. See `csv_file.py` for a minimal example, or `junction.py` for one that
   talks to a remote API.

```python
from techscan.adapters.base import BaseAdapter, AdapterSubmission

class MyAdapter(BaseAdapter):
    def name(self) -> str:
        return "My Platform"

    def pull(self, cfg: dict) -> list[AdapterSubmission]:
        # fetch submissions, return AdapterSubmission list
        ...
```

## Adding a deterministic check

Deterministic checks live in `techscan/facts.py`. Each check inspects cloned
repo contents and returns structured findings (no LLM needed).

1. Add your check function in `facts.py`.
2. Make sure it returns results in the same format as existing checks.
3. Add a corresponding planted problem in `techscan/fixtures.py` so the
   selftest can verify it.
4. Run `python -m techscan selftest` to confirm it passes.
