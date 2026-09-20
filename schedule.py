"""
When things happen, relative to the wall clock.

Everywhere else in Exodus a time is minutes since midnight on the travel day
and nothing knows what day it is. Two features need the clock: telling a
student how long until the next release, and deciding whether a confirmed pair
leaves late enough that a third rider can still be found for it. This module is
the one place that ties a release's unix time to a departure's minute-of-day.

The link is the travel day: the departure is `departure_time` minutes after
midnight (in the college's timezone) on `travel_date`, or on the day of the
release itself when no travel date is set. Set EXODUS_TRAVEL_DATE=YYYY-MM-DD
(or config["travel_date"]) when releases run the day before, or when demoing
at an hour after the departures in the pool.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

from config import CONFIG

RELEASE_INTERVAL_SECONDS = int(os.environ.get("RELEASE_INTERVAL_SECONDS", "120"))


def next_release_at(repo, now: int) -> int | None:
    """Unix time of the next scheduled release, or None before any has run.

    Approximate, like the board's countdown: releases fire one interval apart,
    so the next lands one interval after the last. A release that is overdue is
    "now", never in the past.
    """
    last = repo.latest_release("ALL")
    return max(last["ran_at"] + RELEASE_INTERVAL_SECONDS, now) if last else None


def travel_day_start(at: int, config: dict = CONFIG) -> int:
    """Unix time of midnight, on the travel day, in the college's timezone."""
    tz = timezone(timedelta(minutes=config["tz_offset_minutes"]))
    day = os.environ.get("EXODUS_TRAVEL_DATE") or config.get("travel_date")
    d = date.fromisoformat(day) if day else datetime.fromtimestamp(at, tz).date()
    return int(datetime(d.year, d.month, d.day, tzinfo=tz).timestamp())


def minutes_until(departure_time: int, at: int, config: dict = CONFIG) -> int:
    """Minutes from `at` (unix seconds) until the cab leaves. Negative once it has."""
    return (travel_day_start(at, config) + departure_time * 60 - at) // 60


def seat_may_open(departure_time: int, next_release: int, config: dict = CONFIG) -> bool:
    """Is the cab far enough off that a third rider is worth looking for?"""
    return minutes_until(departure_time, next_release, config) >= config["backfill_open_lead_minutes"]


def seat_fillable(departure_time: int, release_at: int, config: dict = CONFIG) -> bool:
    """May a release at `release_at` still offer this cab's open seat?"""
    return minutes_until(departure_time, release_at, config) >= config["backfill_close_lead_minutes"]
