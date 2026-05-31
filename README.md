# GitHub Vision Navigator

A command-line tool that autonomously navigates GitHub using a **vision model**
(Anthropic's Claude) and extracts the latest release information for a repository.

It works **without any hardcoded CSS selectors or XPath**. Every navigation decision
is made by showing the model a screenshot of the current page and asking it for the
single next action (click here, type this, scroll, extract). Because the agent reasons
about pixels rather than the DOM, it keeps working even if GitHub changes its HTML.

```
Start at github.com → search "openclaw" → open openclaw/openclaw
  → open Releases → read the latest release → emit JSON
```

---

## Output

```json
{
  "repository": "openclaw/openclaw",
  "latest_release": {
    "version": "v2026.1.29",
    "tag": "77e703c",
    "author": "steipete"
  }
}
```

---

## Architecture

The design separates "eyes and hands" from "brain" so each can be swapped or tested
independently.

| File | Responsibility |
|------|----------------|
| `src/browser.py` | Playwright wrapper exposing only selector-free primitives: `screenshot`, `click(x, y)`, `type_text`, `press`, `scroll`. Fixed 1280×900 viewport so coordinates are deterministic. |
| `src/vision.py`  | Claude vision client. Sends `(screenshot, goal, history)` and gets back **one** action as strict JSON. All provider-specific code lives here, so swapping to another VLM means re-implementing one function. |
| `src/agent.py`   | The loop: screenshot → ask model → execute action → repeat, until the model says `done`/`extract` or a step cap is hit. |
| `src/navigate.py`| CLI entrypoint. Builds the goal, runs the agent, and normalizes the result into the required JSON schema. |
| `tests/test_logic.py` | Unit tests for the pure logic (JSON parsing, output normalization, completeness heuristic) that need no network or API key. |

The action vocabulary is intentionally tiny: `click`, `type`, `press`, `scroll`,
`extract`, `done`, `fail`. The model does the page understanding; the loop just
executes.

---

## Setup

Requires Python 3.10+.

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Install the Chromium browser Playwright drives
playwright install chromium

# 3. Provide your Anthropic API key
export ANTHROPIC_API_KEY="sk-ant-..."
```

> **Cost note:** a full run is roughly 5–8 vision calls (one screenshot each), which is
> a few cents at most. You can point the tool at a cheaper/faster model with
> `export VISION_MODEL="claude-haiku-..."`.

---

## Run

The tool supports two ways to specify what to look up: a structured `--repo` flag, or
a free-form `--prompt` (with `--url` as the starting point). Either works.

```bash
# Natural-language prompt + start URL (matches the task brief's interface)
python src/navigate.py --url "https://github.com" \
  --prompt "search for openclaw and get the current release and related tags"

# Structured target (convenience flag for any repo)
python src/navigate.py --repo "openclaw/openclaw" --out sample_output.json

# Any repository
python src/navigate.py --repo "facebook/react"

# Watch it work + save screenshots for debugging
python src/navigate.py --repo "openclaw/openclaw" --show --screenshots ./shots
```

`--prompt` overrides `--repo` when both are given. `--url` only sets the page the agent
starts from (default `https://github.com`); the model still has to navigate from there.

### CLI flags

| Flag | Meaning |
|------|---------|
| `--url` | Start URL (default `https://github.com`) |
| `--repo owner/name` | Repository to inspect |
| `--prompt "..."` | Natural-language instruction (overrides `--repo`) |
| `--out FILE` | Write resulting JSON to a file |
| `--screenshots DIR` | Save a screenshot per step (great for debugging) |
| `--max-steps N` | Safety cap on navigation steps (default 15) |
| `--show` | Run the browser headed (visible) instead of headless |

Exit code is `0` on a clean completion, `1` if the run did not finish cleanly (a
best-effort partial JSON is still printed), and `2` on setup errors (e.g. missing key).

---

## Testing

Pure-logic unit tests (no network, no API key needed):

```bash
PYTHONPATH=src python tests/test_logic.py
```

These cover the lenient JSON parser (handling fenced/prose-wrapped model output),
the output normalizer (nested vs. flattened model results, fallback repo, bonus
metadata pass-through), and the completeness heuristic.

End-to-end behaviour against live GitHub requires the Chromium install and an API key.
use `--show --screenshots ./shots` to observe and audit each decision.

---

## Limitations

See `Observation Document` for a full discussion. 
