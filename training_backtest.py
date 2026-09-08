"""CLI runner for deterministic, read-only historical training replay."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path

from training_backtest_metrics import aggregate
from training_replay import ReplayContext, replay_day


def run(start: date, end: date, cutoff_hour=7, include_details=False):
    if end < start:
        raise ValueError("end date must not precede start date")
    days = []
    current = start
    while current <= end:
        days.append(replay_day(ReplayContext.morning(current, cutoff_hour), include_details))
        current += timedelta(days=1)
    return {"summary": aggregate(days), "days": days}


def _write_json(result, destination):
    payload = json.dumps(result, indent=2, sort_keys=True)
    if destination:
        Path(destination).write_text(payload + "\n")
    else:
        print(payload)


def _write_csv(result, destination):
    if not destination:
        raise ValueError("CSV output requires --output")
    fields = ("replay_date", "as_of", "data_quality", "training_status", "session_type",
              "selected_muscles", "target_sets", "target_volume_lb", "actual_sets",
              "actual_volume_lb", "step_target", "actual_steps")
    with Path(destination).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for day in result["days"]:
            recommendation = day["recommendation"]
            comparison = day["comparison"]
            writer.writerow({"replay_date": day["replay_date"], "as_of": day["as_of"],
                             "data_quality": day["data_quality"]["status"],
                             "training_status": recommendation.get("training_status"),
                             "session_type": recommendation.get("session_type"),
                             "selected_muscles": "|".join(recommendation.get("selected_muscles") or []),
                             "target_sets": recommendation.get("target_sets"),
                             "target_volume_lb": recommendation.get("target_volume_lb"),
                             "actual_sets": comparison.get("actual_sets"),
                             "actual_volume_lb": comparison.get("actual_volume_lb"),
                             "step_target": comparison.get("step_target"),
                             "actual_steps": comparison.get("actual_steps")})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    dates = parser.add_mutually_exclusive_group(required=True)
    dates.add_argument("--date", type=date.fromisoformat)
    dates.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--cutoff-hour", type=int, default=7)
    parser.add_argument("--output")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--include-details", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    start = args.date or args.start_date
    end = args.date or args.end_date
    if end is None:
        parser.error("--end-date is required with --start-date")
    if not 0 <= args.cutoff_hour <= 23:
        parser.error("--cutoff-hour must be between 0 and 23")
    result = run(start, end, args.cutoff_hour, args.include_details or args.verbose)
    (_write_csv if args.format == "csv" else _write_json)(result, args.output)


if __name__ == "__main__":
    main()
