"""Offline checks for the board clock and the workflow freshness gate.

Run:  python scripts/test_board_date.py
No network, no database. Every clock is pinned — see the lesson in
scripts/test_signals.py about fixtures that rot the day after you write them.
"""
import datetime as dt
import importlib.util
import json

import yaml
import subprocess
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kproj import util  # noqa: E402

UTC = dt.timezone.utc
ET = ZoneInfo("America/New_York")

# (UTC instant, expected board date, note)
CASES = [
    ("2026-08-02T07:00:00Z", "2026-08-02", "12:00 AM AZ — still today's board"),
    ("2026-08-02T15:00:00Z", "2026-08-02", "8:00 AM AZ slot"),
    ("2026-08-02T22:00:00Z", "2026-08-02", "3:00 PM AZ slot"),
    ("2026-08-03T02:59:00Z", "2026-08-02", "7:59 PM AZ — one minute before rollover"),
    ("2026-08-03T03:00:00Z", "2026-08-03", "8:00 PM AZ slot — rolls to tomorrow"),
    ("2026-08-03T04:10:00Z", "2026-08-03", "9:10 PM AZ — the old cron, same answer"),
    ("2026-08-03T06:37:00Z", "2026-08-03", "11:37 PM AZ — a badly delayed run"),
    ("2026-08-03T08:30:00Z", "2026-08-03", "1:30 AM AZ — after local midnight"),
]


def check_board_dates() -> int:
    bad = 0
    for iso, want, note in CASES:
        now = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        got = util.iso(util.board_date(now))
        prev = util.iso(util.board_prev_date(now))
        ok = got == want
        bad += not ok
        print(f"  {'ok ' if ok else 'FAIL'} {iso}  board={got} settles={prev}  {note}")
        if ok:
            assert (util.board_date(now) - util.board_prev_date(now)).days == 1
    return bad


def check_no_regression() -> int:
    """At the historical 04:10 UTC slot the old today_et() and the new
    board_date() must agree — the change moves the rollover earlier, it does not
    move an existing run's answer."""
    bad = 0
    for day in range(1, 29):
        now = dt.datetime(2026, 8, day, 4, 10, tzinfo=UTC)
        old = now.astimezone(ET).date()
        new = util.board_date(now)
        if old != new:
            print(f"  FAIL 2026-08-{day:02d} 04:10Z old={old} new={new}")
            bad += 1
    print(f"  {'ok ' if not bad else 'FAIL'} 04:10 UTC slot unchanged across 28 days")
    return bad


def check_rollover_is_configurable() -> int:
    """The hour is config-driven, so 'move it to 9 PM' is a one-line change."""
    import importlib
    import os
    os.environ["KPROJ_BOARD_ROLLOVER_HOUR"] = "21"
    from kproj import config
    importlib.reload(config)
    importlib.reload(util)
    now = dt.datetime(2026, 8, 3, 3, 30, tzinfo=UTC)      # 8:30 PM AZ
    got = util.board_date(now)
    ok = util.iso(got) == "2026-08-02"                     # 9 PM rollover: not yet
    print(f"  {'ok ' if ok else 'FAIL'} rollover hour honours KPROJ_BOARD_ROLLOVER_HOUR")
    del os.environ["KPROJ_BOARD_ROLLOVER_HOUR"]
    importlib.reload(config)
    importlib.reload(util)
    return 0 if ok else 1


def check_odds_window() -> int:
    """The odds fetch window must be anchored to the board day, not the bare UTC
    hour — otherwise a run that slips past midnight UTC skips the day's pull."""
    OPEN = 15                       # min(config.ODDS_GAMELINE_HOURS_UTC)
    cases = [
        ("2026-08-02T03:00:00Z", "2026-08-03", False, "rollover run: tomorrow's window is not open"),
        ("2026-08-02T06:40:00Z", "2026-08-03", False, "rollover 3.5h late: still must not spend"),
        ("2026-08-03T14:59:00Z", "2026-08-03", False, "one minute early"),
        ("2026-08-03T15:00:00Z", "2026-08-03", True,  "8:00 AM AZ: fetch"),
        ("2026-08-03T18:30:00Z", "2026-08-03", True,  "delayed morning run self-heals"),
        ("2026-08-03T22:00:00Z", "2026-08-03", True,  "3:00 PM AZ run, morning was missed"),
        ("2026-08-04T00:30:00Z", "2026-08-03", True,  "past midnight UTC, board is still Aug 3"),
        ("2026-08-04T02:55:00Z", "2026-08-03", True,  "7:55 PM AZ, minutes before rollover"),
    ]
    bad = 0
    for iso, board, want, note in cases:
        now = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        got = util.window_open(dt.date.fromisoformat(board), OPEN, now)
        ok = got == want
        bad += not ok
        print(f"  {'ok ' if ok else 'FAIL'} {iso} board={board} -> fetch={got}  {note}")
    # and the board that a run at that instant would actually be working on
    for iso in ("2026-08-04T00:30:00Z", "2026-08-04T02:55:00Z"):
        now = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        b = util.board_date(now)
        ok = util.iso(b) == "2026-08-03"
        bad += not ok
        print(f"  {'ok ' if ok else 'FAIL'} {iso} board_date()={b} (the day those cases assume)")
    return bad


GATE = Path(__file__).resolve().parents[1] / ".github/scripts/gate.py"


def check_gate_wired() -> int:
    """The gate step must actually run the script, and the sparse checkout must
    include it — a checkout that omits gate.py fails every scheduled run."""
    root = Path(__file__).resolve().parents[1]
    wf = yaml.safe_load((root / ".github/workflows/daily.yml").read_text())
    triggers = wf.get("on", wf.get(True))    # YAML 1.1 parses a bare `on:` as True
    gate_steps = wf["jobs"]["gate"]["steps"]
    checkout = next(s for s in gate_steps if str(s.get("uses", "")).startswith("actions/checkout"))
    sparse = checkout["with"]["sparse-checkout"]
    bad = 0
    checks = [
        (any("gate.py" in str(s.get("run", "")) for s in gate_steps),
         "gate step runs the script"),
        (".github/scripts/gate.py" in sparse,
         "gate.py is in the sparse checkout (else every scheduled run fails)"),
        ("docs/data/meta.json" in sparse, "meta.json is in the sparse checkout"),
        ((root / ".github/scripts/gate.py").exists(), "gate.py exists on disk"),
        ("!=" in wf["jobs"]["daily"]["if"] and "'false'" in wf["jobs"]["daily"]["if"],
         "daily job fails OPEN on a broken gate"),
        (sorted(c["cron"] for c in triggers["schedule"]) ==
         ["7 15 * * *", "7 19 * * *", "7 22 * * *", "7 3 * * *"],
         "backstop crons sit just after each target slot"),
    ]
    for ok, note in checks:
        bad += not ok
        print(f"  {'ok ' if ok else 'FAIL'} {note}")
    return bad


def check_gate() -> int:
    spec = importlib.util.spec_from_file_location("gate", GATE)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    bad = 0
    # (event, force, minutes_old, expect_run, note)
    cases = [
        ("schedule", False, 5, False, "backstop right after the scheduler ran"),
        ("schedule", False, 119, False, "backstop 2h later — still fresh"),
        ("schedule", False, 121, True, "just past the threshold"),
        ("schedule", False, 300, True, "5h gap — scheduler is down, do the work"),
        ("schedule", True, 5, True, "forced schedule run"),
        ("workflow_dispatch", False, 2, True, "Worker dispatch is never gated"),
        ("workflow_dispatch", False, None, True, "dispatch with no meta.json"),
        ("schedule", False, None, True, "meta.json unreadable — fail open"),
    ]
    for event, force, age, want, note in cases:
        got = gate.decide(event, force, age)
        ok = got == want
        bad += not ok
        print(f"  {'ok ' if ok else 'FAIL'} {event:<18} age={str(age):<5} force={force!s:<5}"
              f" -> run={got}  {note}")

    # End-to-end: the real script, a real meta.json, a real $GITHUB_OUTPUT file.
    for age_min, want in ((5, "run=false"), (400, "run=true")):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / "docs/data"
            data.mkdir(parents=True)
            gen = dt.datetime.now(UTC) - dt.timedelta(minutes=age_min)
            (data / "meta.json").write_text(json.dumps(
                {"generated_at": gen.strftime("%Y-%m-%dT%H:%M:%SZ")}))
            outfile = Path(d) / "gh_output"
            outfile.touch()
            r = subprocess.run([sys.executable, str(GATE)], cwd=d, capture_output=True,
                               text=True, env={"EVENT": "schedule", "FORCE": "false",
                                               "FRESH_MINUTES": "120",
                                               "GITHUB_OUTPUT": str(outfile),
                                               "PATH": "/usr/bin:/bin"})
            body = outfile.read_text()
            ok = r.returncode == 0 and want in body and "::" not in body
            bad += not ok
            print(f"  {'ok ' if ok else 'FAIL'} end-to-end age={age_min:<4} "
                  f"$GITHUB_OUTPUT={body.strip().replace(chr(10), ' ')!r}")
            if not ok:
                print(f"       rc={r.returncode} stderr={r.stderr.strip()}")
    return bad


if __name__ == "__main__":
    print("board_date:")
    fails = check_board_dates()
    print("no regression at the old slot:")
    fails += check_no_regression()
    print("configurability:")
    fails += check_rollover_is_configurable()
    print("odds fetch window:")
    fails += check_odds_window()
    print("workflow wiring:")
    fails += check_gate_wired()
    print("workflow freshness gate:")
    fails += check_gate()
    print("\n" + ("ALL PASS" if not fails else f"{fails} FAILURE(S)"))
    sys.exit(1 if fails else 0)
