import sys, types

# Stub playwright so browser.py imports without the real package.
fake_pw = types.ModuleType("playwright")
fake_sync = types.ModuleType("playwright.sync_api")
for name in ("sync_playwright", "Browser", "Page", "Playwright"):
    setattr(fake_sync, name, object)
fake_pw.sync_api = fake_sync
sys.modules["playwright"] = fake_pw
sys.modules["playwright.sync_api"] = fake_sync

from vision import _parse_json_lenient
from navigate import normalize_output
from agent import _looks_complete

passed = 0; failed = 0
def check(name, cond):
    global passed, failed
    if cond: passed += 1; print(f"  PASS: {name}")
    else: failed += 1; print(f"  FAIL: {name}")

print("== lenient JSON parser ==")
check("plain json", _parse_json_lenient('{"action":"click","x":1,"y":2}')["action"]=="click")
check("fenced json", _parse_json_lenient('```json\n{"action":"done"}\n```')["action"]=="done")
check("prose-wrapped", _parse_json_lenient('Sure! {"action":"scroll","dy":300} ok')["action"]=="scroll")
check("garbage -> fail", _parse_json_lenient('not json at all')["action"]=="fail")

print("== output normalizer ==")
nested = normalize_output({"repository":"openclaw/openclaw","latest_release":{"version":"v2026.1.29","tag":"77e703c","author":"steipete"}}, None)
check("nested repo", nested["repository"]=="openclaw/openclaw")
check("nested version", nested["latest_release"]["version"]=="v2026.1.29")
flat = normalize_output({"version":"v1.0","tag":"abc123","author":"jane"}, "foo/bar")
check("flat->nested version", flat["latest_release"]["version"]=="v1.0")
check("fallback repo used", flat["repository"]=="foo/bar")
bonus = normalize_output({"repository":"a/b","latest_release":{"version":"v1","published_at":"2026-01-29"}}, None)
check("bonus metadata carried", bonus["latest_release"].get("published_at")=="2026-01-29")
empty = normalize_output({}, "x/y")
check("empty safe", empty["latest_release"]["version"] is None and empty["repository"]=="x/y")

print("== completeness heuristic (now requires all three fields) ==")
full = {"latest_release":{"version":"v1","tag":"abc","author":"jane"}}
check("all three -> complete", _looks_complete(full))
check("only version -> incomplete", not _looks_complete({"latest_release":{"version":"v1"}}))
check("only tag -> incomplete", not _looks_complete({"tag":"abc"}))
check("missing author -> incomplete", not _looks_complete({"latest_release":{"version":"v1","tag":"abc"}}))
check("empty -> incomplete", not _looks_complete({}))

print("== deep merge (phase 2 must not clobber phase 1) ==")
from agent import _deep_merge
merged = _deep_merge(
    {"repository":"a/b","latest_release":{"version":"v1","tag":"t","author":"u"}},
    {"additional_info":{"download_links":["x"]}},
)
check("phase1 latest_release preserved", merged["latest_release"]["version"]=="v1")
check("phase2 additional_info added", merged["additional_info"]["download_links"]==["x"])
nested = _deep_merge({"latest_release":{"version":"v1"}}, {"latest_release":{"author":"u"}})
check("nested dict merged not replaced", nested["latest_release"]=={"version":"v1","author":"u"})

print("== additional_info passthrough in normalize_output ==")
ai = normalize_output({"repository":"a/b","latest_release":{"version":"v1","tag":"t","author":"u"},"additional_info":{"notes":"hi"}}, None)
check("additional_info carried", ai.get("additional_info")=={"notes":"hi"})
no_ai = normalize_output({"repository":"a/b","latest_release":{"version":"v1"}}, None)
check("no additional_info when absent", "additional_info" not in no_ai)

print(f"\nTOTAL: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
