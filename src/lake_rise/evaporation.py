"""Open-water evaporation off the lake from archived on-site weather.

The forecast uses the monthly climatology ``lake_evaporation_in_per_day`` (no future water
temperature exists). Wherever weather was *recorded* -- backtests, storm records, re-deriving
that table -- this module estimates each local day's evaporation from the continuous archive,
with the same methods the table was built from (2026-11-04 calibration-log entry):

1. **Mass transfer** when the lake probe covers the day:
   ``E = 2.6 (1 + 0.54 u2) (es(Tw) - ea)`` mm/day (Penman 1948 wind function), on daily means.
2. **FAO-56 Penman-Monteith reference ET** otherwise, with solar radiation from the daily
   temperature range (Hargreaves; there is no radiation sensor), grass albedo 0.23.
3. ``None`` when neither has enough hours -- the caller falls back to the monthly table.

The humidity sensor's RH is relative to its RAW (warm-reading) temperature, so actual vapor
pressure is ``ea = RH x es(T_raw)``; the operator-corrected temperature is used for the air.
Pure and framework-free.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .artifact import Artifact
from .calibration.archive import HourSample

# A day needs this many hours of each input to be estimated (otherwise: the monthly table).
MIN_HOURS_PER_DAY = 20

_MM_PER_IN = 25.4
_MPS_PER_MPH = 0.44704


def _c(f: float) -> float:
    return (f - 32.0) / 1.8


def es_kpa(t_c: float) -> float:
    """Saturation vapor pressure (kPa) at ``t_c`` deg C (FAO-56 eq. 11)."""
    return 0.6108 * math.exp(17.27 * t_c / (t_c + 237.3))


def mass_transfer_in_per_day(water_c: float, ea_kpa: float, u2_mps: float) -> float:
    """Open-water evaporation from the water-surface / air vapor-pressure difference."""
    return max(0.0, 2.6 * (1 + 0.54 * u2_mps) * (es_kpa(water_c) - ea_kpa)) / _MM_PER_IN


def fao56_reference_et_in_per_day(tmax_c: float, tmin_c: float, tmean_c: float, ea_kpa: float,
                                  u2_mps: float, day: date, lat_deg: float, elev_m: float,
                                  krs: float) -> float:
    """FAO-56 daily reference ET (grass), radiation from Hargreaves' temperature-range form."""
    lat = math.radians(lat_deg)
    pressure = 101.3 * ((293 - 0.0065 * elev_m) / 293) ** 5.26
    gamma = 0.000665 * pressure
    j = day.timetuple().tm_yday
    dr = 1 + 0.033 * math.cos(2 * math.pi * j / 365)
    dec = 0.409 * math.sin(2 * math.pi * j / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(lat) * math.tan(dec))))
    ra = (24 * 60 / math.pi * 0.0820 * dr
          * (ws * math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.sin(ws)))
    rso = (0.75 + 2e-5 * elev_m) * ra
    rs = min(krs * math.sqrt(max(tmax_c - tmin_c, 0.0)) * ra, rso)
    rnl = (4.903e-9 * ((tmax_c + 273.16) ** 4 + (tmin_c + 273.16) ** 4) / 2
           * (0.34 - 0.14 * math.sqrt(max(ea_kpa, 0.0))) * (1.35 * rs / rso - 0.35 if rso > 0 else 0.0))
    rn = (1 - 0.23) * rs - rnl
    delta = 4098 * es_kpa(tmean_c) / (tmean_c + 237.3) ** 2
    es_d = (es_kpa(tmax_c) + es_kpa(tmin_c)) / 2
    et0 = ((0.408 * delta * rn + gamma * 900 / (tmean_c + 273) * u2_mps * (es_d - ea_kpa))
           / (delta + gamma * (1 + 0.34 * u2_mps)))
    return max(0.0, et0) / _MM_PER_IN


def daily_lake_evaporation(art: Artifact, samples: list[HourSample]) -> dict[date, tuple[float, str]]:
    """``{local day: (inches/day, method)}`` for every day the archive can estimate."""
    site = art.lake_evaporation_model
    tz = ZoneInfo(site.timezone)
    days: dict[date, list[HourSample]] = defaultdict(list)
    for s in samples:
        days[datetime.fromisoformat(s.hour).astimezone(tz).date()].append(s)
    out: dict[date, tuple[float, str]] = {}
    for day, hours in days.items():
        air = [h for h in hours if h.air_temp_f is not None]
        hum = [h for h in hours if h.rh_pct is not None and h.air_temp_raw_f is not None]
        wind = [h.wind_mph for h in hours if h.wind_mph is not None]
        if len(air) < MIN_HOURS_PER_DAY or len(hum) < MIN_HOURS_PER_DAY or len(wind) < MIN_HOURS_PER_DAY:
            continue
        ea = sum(h.rh_pct / 100 * es_kpa(_c(h.air_temp_raw_f)) for h in hum) / len(hum)
        u2 = sum(wind) / len(wind) * _MPS_PER_MPH
        water = [h.water_temp_f for h in hours if h.water_temp_f is not None]
        if len(water) >= MIN_HOURS_PER_DAY:
            out[day] = (round(mass_transfer_in_per_day(_c(sum(water) / len(water)), ea, u2), 4),
                        "mass_transfer")
            continue
        temps = [_c(h.air_temp_f) for h in air]
        out[day] = (round(fao56_reference_et_in_per_day(
            max(temps), min(temps), sum(temps) / len(temps), ea, u2, day,
            site.latitude_deg, site.elevation_m, site.hargreaves_krs), 4), "fao56_reference_et")
    return out


def hourly_rates(art: Artifact, samples: list[HourSample], start: datetime,
                 hours: int) -> list[float | None]:
    """The estimated evaporation rate (inches/day) for each model hour from ``start`` (hour i
    covers [start + i h, start + i + 1 h)), or ``None`` where the day can't be estimated --
    the model then uses the monthly table for that hour."""
    tz = ZoneInfo(art.lake_evaporation_model.timezone)
    daily = daily_lake_evaporation(art, samples)
    out: list[float | None] = []
    for i in range(hours):
        day = (start + timedelta(hours=i)).astimezone(tz).date()
        out.append(daily[day][0] if day in daily else None)
    return out
