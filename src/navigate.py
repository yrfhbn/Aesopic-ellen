#!/usr/bin/env python3
"""
navigate.py
===========
Command-line entrypoint for the GitHub vision navigator.

Examples
--------
# Natural-language prompt (bonus): the model interprets the intent.
python navigate.py --url "https://github.com" \
    --prompt "search for openclaw and get the current release and related tags"

# Convenience flag for any repo (bonus):
python navigate.py --repo "openclaw/openclaw"

# Save artifacts:
python navigate.py --repo "openclaw/openclaw" \
    --out sample_output.json --screenshots ./shots --show

Environment
-----------
ANTHROPIC_API_KEY   required — your Anthropic key
VISION_MODEL        optional — overrides the default model id
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

from browser import BrowserSession
from vision import VisionClient
from agent import NavigatorAgent


DEFAULT_GOAL_TEMPLATE = (
    "Search GitHub for the repository '{repo}', open it, navigate to its Releases, "
    "and extract the latest release information. Return data with this shape: "
    '{{"repository": "<owner/name>", "latest_release": '
    '{{"version": "<tag/version>", "tag": "<short commit or tag>", '
    '"author": "<release author username>"}}}}.'
)


def build_goal(args: argparse.Namespace) -> tuple[str, str]:
    """Return (start_url, goal_text)."""
    if args.prompt:
        # Natural-language mode: pass the prompt through, but append the output
        # contract so the model knows the JSON shape we expect back.
        goal = (
            f"{args.prompt}\n\n"
            "When finished, return data as JSON shaped like: "
            '{"repository": "<owner/name>", "latest_release": '
            '{"version": "...", "tag": "...", "author": "..."}}.'
        )
        return args.url, goal

    repo = args.repo or "openclaw/openclaw"
    return args.url, DEFAULT_GOAL_TEMPLATE.format(repo=repo)


def normalize_output(data: dict[str, Any], fallback_repo: Optional[str]) -> dict[str, Any]:
    """
    Coerce whatever the model returned into the required output schema, being
    forgiving about where it put each field.
    """
    if not isinstance(data, dict):
        data = {}

    # The release sub-object may be nested or flattened.
    release = data.get("latest_release")
    if not isinstance(release, dict):
        release = {
            k: data.get(k)
            for k in ("version", "tag", "author")
            if k in data
        }

    out = {
        "repository": data.get("repository") or fallback_repo,
        "latest_release": {
            "version": release.get("version"),
            "tag": release.get("tag"),
            "author": release.get("author"),
        },
    }

    # Carry through any bonus metadata the model gathered.
    for extra in ("release_notes", "published_at", "download_links", "publish_date"):
        if extra in release:
            out["latest_release"][extra] = release[extra]
        elif extra in data:
            out["latest_release"][extra] = data[extra]

    # Phase-2 results live in their own top-level block, kept separate from the
    # required schema so core vs. extra is unambiguous.
    if isinstance(data.get("additional_info"), dict) and data["additional_info"]:
        out["additional_info"] = data["additional_info"]

    return out


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Autonomously navigate GitHub with a vision model and extract "
        "the latest release info."
    )
    p.add_argument("--url", default="https://github.com",
                   help="Start URL (default: https://github.com)")
    p.add_argument("--prompt",
                   help="Natural-language instruction (overrides --repo).")
    p.add_argument("--repo",
                   help="Repository as owner/name, e.g. openclaw/openclaw.")
    p.add_argument("--out",
                   help="Write the resulting JSON to this file.")
    p.add_argument("--screenshots",
                   help="Directory to save per-step screenshots into.")
    p.add_argument("--max-steps", type=int, default=15,
                   help="Safety cap on navigation steps (default: 15).")
    p.add_argument("--show", action="store_true",
                   help="Run the browser headed (visible) instead of headless.")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    start_url, goal = build_goal(args)
    fallback_repo = args.repo if args.repo else None

    print(f"Goal: {goal}\n", file=sys.stderr)

    try:
        vision = VisionClient()
    except Exception as e:
        print(
            "ERROR: could not initialize the vision client. "
            "Is ANTHROPIC_API_KEY set?\n"
            f"  detail: {e}",
            file=sys.stderr,
        )
        return 2

    with BrowserSession(headless=not args.show) as browser:
        agent = NavigatorAgent(
            browser=browser,
            vision=vision,
            max_steps=args.max_steps,
            screenshot_dir=args.screenshots,
        )
        # With a free-form prompt we may need to gather info beyond the three
        # standard fields (and possibly from other pages), so don't stop at the
        # standard extraction — let the single loop run until the model says "done".
        result = agent.run(
            start_url=start_url,
            goal=goal,
            extract_only=not bool(args.prompt),
        )

    output = normalize_output(result.data, fallback_repo)
    rendered = json.dumps(output, indent=2)

    if not result.success:
        print(f"\nWARNING: run did not complete cleanly: {result.error}",
              file=sys.stderr)

    print(rendered)

    if args.out:
        with open(args.out, "w") as f:
            f.write(rendered + "\n")
        print(f"\nWrote {args.out}", file=sys.stderr)

    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())