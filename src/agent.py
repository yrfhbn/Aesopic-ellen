"""
agent.py
========
The autonomous navigation loop. It ties together the BrowserSession (eyes + hands)
and the VisionClient (brain):

    loop:
        shot = browser.screenshot()
        decision = vision.decide_action(shot, goal, history)
        execute(decision)
        if decision is terminal -> stop

It is deliberately model-agnostic about WHAT the page looks like; all page
understanding is delegated to the vision model. The loop only knows how to execute a
small fixed vocabulary of actions and when to stop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from browser import BrowserSession
from vision import VisionClient, Decision


@dataclass
class AgentResult:
    success: bool
    data: dict[str, Any]
    history: list[str] = field(default_factory=list)
    error: Optional[str] = None


class NavigatorAgent:
    def __init__(
        self,
        browser: BrowserSession,
        vision: VisionClient,
        max_steps: int = 15,
        screenshot_dir: Optional[str] = None,
    ):
        self._browser = browser
        self._vision = vision
        self._max_steps = max_steps
        self._history: list[str] = []
        self._shot_counter: int = 0
        self._screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        if self._screenshot_dir:
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    def run(self, start_url: str, goal: str, extract_only: bool = True) -> AgentResult:
        """
        Navigate from start_url and pursue the goal.

        extract_only=True  (the --repo case): stop as soon as the three standard
            fields (version, tag, author) are extracted. Fast path.
        extract_only=False (the --prompt case): the standard fields are NOT the
            finish line. The model keeps looping — navigating wherever it needs to
            (e.g. into the README for "key features") — and the run ends only when
            the model emits "done". This lets a single loop gather off-page extras
            without any separate phase.
        """
        self._browser.goto(start_url)
        self._history.append(f"Navigated to {start_url}")
        return self._loop(goal, extract_only=extract_only)

    def _loop(self, goal: str, extract_only: bool) -> AgentResult:
        last_data: dict[str, Any] = {}

        for step in range(1, self._max_steps + 1):
            shot = self._browser.screenshot()
            if self._screenshot_dir:
                self._shot_counter += 1
                shot.save(self._screenshot_dir / f"step_{self._shot_counter:02d}.png")

            try:
                decision = self._vision.decide_action(shot, goal, self._history)
            except Exception as e:  # network/API errors shouldn't crash the run
                return AgentResult(
                    success=False,
                    data=last_data,
                    history=self._history,
                    error=f"Vision API error on step {step}: {e}",
                )

            action = decision.action
            self._log(step, decision)

            if action == "done":
                return AgentResult(
                    success=True,
                    data=_deep_merge(last_data, decision.get("data", {}) or {}),
                    history=self._history,
                )

            if action == "extract":
                extracted = decision.get("data", {}) or {}
                last_data = _deep_merge(last_data, extracted)
                self._history.append(f"Extracted: {extracted}")
                # Only treat the standard extraction as the finish line when we are
                # NOT chasing extra prompt-specific info. Otherwise keep looping and
                # wait for the model to emit "done".
                if extract_only and _looks_complete(last_data):
                    return AgentResult(
                        success=True, data=last_data, history=self._history
                    )
                continue

            if action == "fail":
                return AgentResult(
                    success=False,
                    data=last_data,
                    history=self._history,
                    error=decision.reasoning or "Model reported failure.",
                )

            # Non-terminal actions: execute and continue the loop.
            try:
                self._execute(decision)
            except Exception as e:
                # An action failing (e.g. bad coords) is recoverable: log and let the
                # model see the new screenshot and try again next iteration.
                self._history.append(f"Action {action} failed: {e}")
            time.sleep(0.3)

        return AgentResult(
            success=False,
            data=last_data,
            history=self._history,
            error=f"Reached max_steps ({self._max_steps}) without completing the goal.",
        )

    # -- action execution --------------------------------------------------
    def _execute(self, d: Decision) -> None:
        action = d.action
        if action == "click":
            self._browser.click(int(d.get("x")), int(d.get("y")))
            self._history.append(f"Clicked ({d.get('x')},{d.get('y')})")
        elif action == "type":
            self._browser.type_text(str(d.get("text", "")))
            self._history.append(f"Typed: {d.get('text')!r}")
        elif action == "press":
            self._browser.press(str(d.get("key", "Enter")))
            self._history.append(f"Pressed: {d.get('key')}")
        elif action == "scroll":
            self._browser.scroll(int(d.get("dy", 500)))
            self._history.append(f"Scrolled dy={d.get('dy', 500)}")
        else:
            self._history.append(f"Unknown action ignored: {action}")

    def _log(self, step: int, d: Decision) -> None:
        print(f"[step {step:02d}] {d.action:7s} | {d.reasoning}")


REQUIRED_FIELDS = ("version", "tag", "author")


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """
    Merge incoming into base, recursing into nested dicts so that, e.g., a phase-2
    'additional_info' block is added without wiping the phase-1 'latest_release'.
    """
    out = dict(base)
    for k, v in (incoming or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _looks_complete(data: dict[str, Any]) -> bool:
    """
    The standard extraction is complete only once ALL three required fields
    (version, tag, author) are present and non-empty. This is the explicit
    "standard extraction complete" signal that gates phase 2: we don't want to
    start hunting for extra info while the core schema is still half-filled.
    """
    if not isinstance(data, dict):
        return False
    release = data.get("latest_release", data)
    if not isinstance(release, dict):
        return False
    return all(release.get(f) for f in REQUIRED_FIELDS)