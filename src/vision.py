"""
vision.py
=========
Wraps the Anthropic Messages API (Claude vision) behind a single function:
`decide_action(screenshot, goal, history)`.

The model is shown the current screenshot and the high-level goal, and is asked to
return ONE next action as strict JSON. We deliberately keep the action vocabulary
small and generic (click / type / press / scroll / extract / done / fail) so the
agent loop stays simple and the model does the "understanding" work.

Provider abstraction: everything Anthropic-specific lives in this file. Swapping to
another vision provider (or a local Ollama VLM) means re-implementing only
`decide_action` with the same return contract.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

import anthropic

from browser import Screenshot


# Model is overridable via env so you can trade cost/latency for accuracy.
DEFAULT_MODEL = os.environ.get("VISION_MODEL", "claude-sonnet-4-20250514")

# The full set of actions the agent knows how to execute. Keep in sync with agent.py.
ACTION_SCHEMA = """
Return STRICT JSON (no markdown, no prose) with this shape:

{
  "reasoning": "<one short sentence: what you see and why this action>",
  "action": "click" | "type" | "press" | "scroll" | "extract" | "done" | "fail",

  // for "click": pixel coordinates in the screenshot (top-left origin)
  "x": <int>,
  "y": <int>,

  // for "type": the text to type into the currently focused field
  "text": "<string>",

  // for "press": a single key name, e.g. "Enter"
  "key": "<string>",

  // for "scroll": pixels to scroll vertically, positive = down
  "dy": <int>,

  // for "extract" or "done": the structured result you have gathered so far
  "data": { ... }
}

Only include the fields relevant to the chosen action.
"""

SYSTEM_PROMPT = f"""You are a web-navigation agent that controls a browser purely by
looking at screenshots. You CANNOT read HTML or use selectors. You decide the single
next action that best advances toward the goal, based only on the screenshot.

The viewport is 1280x900 pixels. When you click, give the pixel coordinate of the
CENTER of the target element as you see it in the image.

General rules:
- Take one step at a time. After each action you will receive a fresh screenshot.
- If you appear stuck or the target is off-screen, "scroll" to reveal more.
- Use "fail" only if the goal is genuinely unreachable.

=== RELEASE SKILL (follow this exactly) ===
Your primary job is to reach a repository's latest release and extract it in a precise
format. Work through these stages IN ORDER and do not skip ahead:

STAGE 1 — Get to the releases page, no matter what:
  1. From github.com, click the search box, type the repository name, press Enter.
  2. In the results, click the EXACT-MATCH repository (owner/name), not a fork or a
     similarly-named repo.
  3. On the repo page, open the releases by clicking the "Releases" link in
     the right-hand sidebar, not a specific release.

STAGE 2 — Extract the standard fields of the latest release in the EXACT format:
  Use the "extract" action with a "data" object shaped EXACTLY like this:
    {{"repository": "<owner/name>",
      "latest_release": {{"version": "<...>", "tag": "<...>", "author": "<...>"}}}}
  Read each field precisely from the release page:
  - version: the release's version/title as shown, normalized to the tag form when
    obvious (e.g. prefer "v2026.5.28" over a decorated heading like "OpenClaw 2026.5.28").
  - tag: the commit hash of the release
  - author: the USERNAME of the person who PUBLISHED the release — shown next to the
    release's avatar/timestamp near the top of the release entry. This is a person, NOT
    the repository owner or organization name. If the repo is "openclaw/openclaw" but
    the release was published by "steipete", the author is "steipete".

STAGE 3 — Additional info (only if the goal explicitly asks for more):
  If, and only if, the goal text requests information beyond the three standard fields
  (e.g. release notes, download links, publish date, key features), put those under a
  separate "additional_info" object inside "data" — never inside "latest_release". When 
  you have captured everything the goal asks for, use the "done" action with the
  complete "data". 

{ACTION_SCHEMA}
"""


@dataclass
class Decision:
    raw: dict[str, Any]

    @property
    def action(self) -> str:
        return self.raw.get("action", "fail")

    @property
    def reasoning(self) -> str:
        return self.raw.get("reasoning", "")

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)


class VisionClient:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: Optional[str] = None):
        # The SDK reads ANTHROPIC_API_KEY from the env automatically if not passed.
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self._model = model

    def decide_action(
        self,
        screenshot: Screenshot,
        goal: str,
        history: list[str],
    ) -> Decision:
        """Ask the model for the next single action given the current screenshot."""
        history_text = "\n".join(f"- {h}" for h in history[-8:]) or "- (none yet)"

        user_text = (
            f"GOAL: {goal}\n\n"
            f"Actions taken so far:\n{history_text}\n\n"
            "Here is the current screenshot. Decide the single next action."
        )

        message = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": screenshot.to_base64(),
                            },
                        },
                        {"type": "text", "text": user_text},
                    ],
                }
            ],
        )

        text = "".join(
            block.text for block in message.content if block.type == "text"
        ).strip()

        return Decision(raw=_parse_json_lenient(text))


def _parse_json_lenient(text: str) -> dict[str, Any]:
    """
    Models occasionally wrap JSON in ```json fences or add stray prose. Strip the
    common cases and parse; on total failure, return a 'fail' action so the loop can
    handle it gracefully rather than crashing.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # remove leading ```json / ``` and trailing ```
        cleaned = cleaned.split("```", 2)
        cleaned = cleaned[1] if len(cleaned) >= 2 else text
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
        cleaned = cleaned.strip().rstrip("`").strip()

    # If there is surrounding prose, grab the outermost {...}.
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {
            "action": "fail",
            "reasoning": f"Could not parse model response as JSON: {text[:200]}",
        }