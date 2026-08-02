#!/usr/bin/env python3
"""Decide whether a scheduled backstop run of daily.yml is worth doing.

The three real update slots are fired by the Cloudflare Worker in
infra/cloudflare-worker/. The crons in daily.yml exist only for the day that
Worker stops, and GitHub delivers them 1-3 hours late, so most of the time they
arrive to find the work already done. This exits those early.

Never gates workflow_dispatch (the Worker and manual taps always run), and
fails open if meta.json can't be read — a broken gate must not stop the board
from updating.

Writes `run=` and `age=` to $GITHUB_OUTPUT. Human notes go to stderr so they
land in the log instead of corrupting the output file.
"""
import datetime as dt
import json
import os
import sys

FRESH_MINUTES = int(os.environ.get("FRESH_MINUTES", "120"))
META = os.environ.get("KPROJ_META", "docs/data/meta.json")


def age_minutes(path=META):
    with open(path) as fh:
        stamp = json.load(fh)["generated_at"]
    ts = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return (dt.datetime.now(dt.timezone.utc) - ts).total_seconds() / 60.0


def decide(event, force, age):
    """True = do the work."""
    if event != "schedule" or force:
        return True
    if age is None:
        return True
    return age >= FRESH_MINUTES


def main() -> int:
    event = os.environ.get("EVENT", "")
    force = os.environ.get("FORCE", "") == "true"
    try:
        age = age_minutes()
    except Exception as exc:                                  # noqa: BLE001
        age = None
        print(f"::warning::gate could not read meta.json ({exc}) — running anyway",
              file=sys.stderr)

    run = decide(event, force, age)
    if run:
        why = "forced" if force else ("dispatch" if event != "schedule" else
                                      "data is stale" if age is not None else "no data")
        print(f"::notice::gate: running ({why}"
              + (f", {round(age)} min old)" if age is not None else ")"), file=sys.stderr)
    else:
        print(f"::notice::gate: skipping — export is {round(age)} min old "
              f"(threshold {FRESH_MINUTES}). The scheduler already did this slot.",
              file=sys.stderr)

    out = os.environ.get("GITHUB_OUTPUT")
    lines = [f"run={'true' if run else 'false'}",
             f"age={'' if age is None else round(age)}"]
    if out:
        with open(out, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
