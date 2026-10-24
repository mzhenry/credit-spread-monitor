#!/usr/bin/env python3
"""FRED credit spread monitor; only Python standard library required."""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
from datetime import date as Date, datetime, timezone, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SERIES = ("BAMLH0A0HYM2", "BAMLC0A4CBBB")
FIELDS = ("series", "date", "value", "first_seen_utc", "last_changed_utc")


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_text(url, timeout=15):
    try:
        with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout) as response:
            return response.read().decode("utf-8-sig", "ignore")
    except (URLError, TimeoutError) as first_error:
        # curl uses a different HTTP stack and can reach FRED when urllib stalls
        # on hosted runners. Keep the total wait bounded so failures are visible.
        try:
            result = subprocess.run(
                ["curl", "--http1.1", "--fail", "--location", "--silent", "--show-error",
                 "--connect-timeout", "10", "--max-time", "25", "--retry", "1",
                 "--user-agent", "Mozilla/5.0", url],
                capture_output=True, check=True, timeout=55,
            )
            return result.stdout.decode("utf-8-sig", "ignore")
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as curl_error:
            raise RuntimeError(f"FRED request failed (urllib: {first_error}; curl: {curl_error})") from curl_error


def read_csv(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def fetch(series, start=None):
    params = {"id": series}
    if start:
        params["cosd"] = start
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urlencode(params)
    body = get_text(url)
    parsed = {}
    for row in csv.DictReader(io.StringIO(body)):
        date = row.get("observation_date") or row.get("DATE")
        raw = row.get(series)
        if not date or raw in (None, "", "."):
            continue
        datetime.strptime(date, "%Y-%m-%d")
        parsed[date] = float(raw)
    if not parsed:
        raise ValueError(f"FRED returned no valid observations for {series}")
    return parsed


def fetch_page_update(series):
    """Best-effort FRED series-page update time, not a per-observation timestamp."""
    url = f"https://fred.stlouisfed.org/series/{series}"
    html = get_text(url, timeout=30)
    match = re.search(r'Updated:\s*<span title="([^"]+)"', html)
    if not match:
        return None
    raw = match.group(1)
    zone = raw.rsplit(" ", 1)[-1]
    if zone not in ("CST", "CDT"):
        return dict(display=raw, utc=None)
    local = datetime.strptime(raw[:-4], "%b %d, %Y %I:%M %p")
    offset = timezone(timedelta(hours=-6 if zone == "CST" else -5))
    return dict(display=raw, utc=local.replace(tzinfo=offset).astimezone(timezone.utc).isoformat(timespec="seconds"))


def merge(existing, incoming, seen_at):
    by_key = {(r["series"], r["date"]): dict(r) for r in existing}
    revisions = []
    added = changed = 0
    for series, dates in incoming.items():
        for date, value in dates.items():
            key = (series, date)
            value = f"{value:.6f}".rstrip("0").rstrip(".")
            old = by_key.get(key)
            if old is None:
                by_key[key] = dict(series=series, date=date, value=value,
                                   first_seen_utc=seen_at, last_changed_utc=seen_at)
                added += 1
            elif old["value"] != value:
                revisions.append(dict(series=series, date=date, previous=old["value"],
                                      revised=value, detected_utc=seen_at))
                old["value"] = value
                old["last_changed_utc"] = seen_at
                changed += 1
    rows = sorted(by_key.values(), key=lambda r: (r["series"], r["date"]))
    return rows, revisions, added, changed


def _nth_weekday(year, month, weekday, n):
    first = Date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    first_next = Date(year + (month == 12), month % 12 + 1, 1)
    last = first_next - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year):
    a = year % 19; b = year // 100; c = year % 100
    d = b // 4; e = b % 4; f = (b + 8) // 25
    g = (b - f + 1) // 3; h = (19 * a + b - d - g + 15) % 30
    i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return Date(year, month, day)


def _observed(day, saturday_friday=True):
    if day.weekday() == 6:
        return day + timedelta(days=1)
    if day.weekday() == 5 and saturday_friday:
        return day - timedelta(days=1)
    return day


def regular_nyse_holidays(year):
    days = {_observed(Date(year, 1, 1), False),
            _nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3),
            _easter(year) - timedelta(days=2), _last_weekday(year, 5, 0),
            _observed(Date(year, 7, 4)), _nth_weekday(year, 9, 0, 1),
            _nth_weekday(year, 11, 3, 4), _observed(Date(year, 12, 25))}
    if year >= 2022:
        days.add(_observed(Date(year, 6, 19)))
    return days


def is_trading_day(value):
    day = Date.fromisoformat(value)
    overrides = {r["date"] for r in read_csv(DATA / "non_trading_dates.csv")}
    return day.weekday() < 5 and day not in regular_nyse_holidays(day.year) and value not in overrides


def calculate(rows):
    """Historical as-of signals. Each decision uses only observations seen by that date."""
    output = []
    for series in SERIES:
        obs = sorted((r for r in rows if r["series"] == series and is_trading_day(r["date"])), key=lambda r: r["date"])
        active = None
        falling = 0
        pending = []
        for i, row in enumerate(obs):
            value = float(row["value"])
            events = []
            # Evaluate earlier candidates before creating today's candidate.
            for peak in pending[:]:
                elapsed = i - peak["i"]
                if value >= peak["value"]:
                    events.append(("峰值失效", peak["date"], ""))
                    pending.remove(peak)
                    if active is peak:
                        active, falling = None, 0
                elif elapsed == 20:
                    events.append(("正式历史峰值", peak["date"], row["date"]))
                    pending.remove(peak)
            if active is not None and i > active["i"]:
                prior = float(obs[i - 1]["value"])
                falling = falling + 1 if value < prior and value < active["value"] else 0
                if falling in (3, 5):
                    events.append((f"连续{falling}日下行", active["date"], ""))
            if i >= 20 and all(value > float(r["value"]) for r in obs[i - 20:i]):
                active = dict(i=i, date=row["date"], value=value)
                pending.append(active)
                falling = 0
                events.append(("候选高点", row["date"], ""))
            for kind, peak_date, confirmed_date in events:
                output.append(dict(series=series, date=row["date"], value=row["value"],
                                   signal=kind, peak_date=peak_date,
                                   confirmation_date=confirmed_date))
    return sorted(output, key=lambda r: (r["date"], r["series"], r["signal"]))


def append_jsonl(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False) + "\n")


def sync():
    DATA.mkdir(exist_ok=True)
    existing = read_csv(DATA / "observations.csv")
    seen_at = now_utc()
    incoming = {}
    errors = {}
    page_updates = {}
    for series in SERIES:
        # Recheck a 60-day overlap to catch recent revisions without downloading
        # the entire FRED graph on every GitHub Actions run.
        try:
            previous_dates = [r["date"] for r in existing if r["series"] == series]
            start = (Date.fromisoformat(max(previous_dates)) - timedelta(days=60)).isoformat() if previous_dates else None
            incoming[series] = fetch(series, start=start)
            page_updates[series] = None
            try:
                page_updates[series] = fetch_page_update(series)
            except Exception:
                pass
        except Exception as exc:
            errors[series] = str(exc)
    if errors:
        latest_saved = {s: max((r["date"] for r in existing if r["series"] == s), default=None) for s in SERIES}
        failed_status = dict(checked_utc=seen_at, status="failed",
                             latest_observation_date=latest_saved, new_rows=0, revised_rows=0,
                             errors=errors, fred_page_updated=page_updates,
                             source_update_note="FRED download failed; saved observations were not treated as new data")
        (DATA / "status.json").write_text(json.dumps(failed_status, ensure_ascii=False, indent=2), encoding="utf-8")
        append_jsonl(DATA / "runs.jsonl", failed_status)
        build_dashboard(existing, read_csv(DATA / "signals.csv"), failed_status)
        raise RuntimeError(f"FRED download failed; saved data left unchanged: {errors}")
    rows, revisions, added, changed = merge(existing, incoming, seen_at)
    write_csv(DATA / "observations.csv", FIELDS, rows)
    old_revisions = read_csv(DATA / "revisions.csv")
    write_csv(DATA / "revisions.csv", ("series", "date", "previous", "revised", "detected_utc"), old_revisions + revisions)
    signals = calculate(rows)
    write_csv(DATA / "signals.csv", ("series", "date", "value", "signal", "peak_date", "confirmation_date"), signals)
    latest = {s: max((d for d in incoming[s]), default=None) for s in incoming}
    status = dict(checked_utc=seen_at, status="ok",
                  latest_observation_date=latest, new_rows=added, revised_rows=changed,
                  errors=errors, fred_page_updated=page_updates,
                  source_update_note="FRED series-page update time is for the series, not each observation")
    (DATA / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    append_jsonl(DATA / "runs.jsonl", status)
    build_dashboard(rows, signals, status)
    print(json.dumps(status, ensure_ascii=False))


def build_dashboard(rows=None, signals=None, status=None):
    rows = rows if rows is not None else read_csv(DATA / "observations.csv")
    signals = signals if signals is not None else read_csv(DATA / "signals.csv")
    status = status if status is not None else json.loads((DATA / "status.json").read_text(encoding="utf-8"))
    template = (ROOT / "dashboard_template.html").read_text(encoding="utf-8")
    display_rows = [{**r, "signal_eligible": is_trading_day(r["date"])} for r in rows]
    payload = json.dumps(dict(rows=display_rows, signals=signals, status=status), ensure_ascii=False).replace("</", "<\\/")
    html = template.replace("__DATA_JSON__", payload)
    (ROOT / "index.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=("sync", "build"))
    args = p.parse_args()
    sync() if args.command == "sync" else build_dashboard()
