from datetime import datetime, timedelta


def phase_aware_daily_mean(
    series,
    phase_start_date,
    phase_age_days,
):
    """Return the phase-aware Goal mean from daily values."""

    if not phase_start_date:
        return None, 0

    if isinstance(phase_start_date, str):
        phase_start_date = datetime.strptime(
            phase_start_date,
            "%Y-%m-%d",
        ).date()

    phase_series = [
        item
        for item in series
        if item["date"] >= phase_start_date
    ]

    if not phase_series:
        return None, 0

    latest_date = max(
        item["date"]
        for item in phase_series
    )

    if (
        phase_age_days is not None
        and phase_age_days >= 7
    ):
        window_start = latest_date - timedelta(days=6)
        phase_series = [
            item
            for item in phase_series
            if item["date"] >= window_start
        ]

    values = [
        float(item["value"])
        for item in phase_series
    ]

    return sum(values) / len(values), len(values)
