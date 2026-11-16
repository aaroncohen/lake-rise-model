"""The field page in a real browser at phone width (390 x 844), one test per state an operator
can meet. Each state's /field answer is built with ``field.summarize`` on synthetic inputs and
served by route interception, so it can't drift from the schema. Skips without a browser."""

import json
import os
import re
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lake_rise import forecast_log
from lake_rise.alerting.rules import evaluate
from lake_rise.bundle import InputBundle, ScenarioRain
from lake_rise.field import summarize
from lake_rise.predict import predict

from test_ensemble_rain import _bundle, _storm

NOW = datetime(2026, 11, 14, 13, tzinfo=timezone.utc)
ABSOLUTE_ELEVATION = re.compile(r"\b3(39|4[0-3])\.\d")
# Inches are written " (user, 2026-11-15), never "in"; "Stop logs: 3 in" means logs in place.
INCHES_AS_IN = re.compile(r"(?<!logs: )\b\d+(?:\.\d+)? ?in(?:/hr?)?\b")

# Every element with its own HTML text, and its contrast against the first opaque background up
# its ancestors (WCAG relative luminance). SVG text is drawn on zones and halos, so it is left out.
CONTRAST_JS = """root => {
  const rgb = s => s.match(/[\\d.]+/g).map(Number);
  const lum = c => {
    const [r, g, b] = c.slice(0, 3).map(v => (v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const bg = el => {
    for (; el; el = el.parentElement) {
      const c = rgb(getComputedStyle(el).backgroundColor);
      if (c.length < 4 || c[3] > 0) return c;
    }
    return [255, 255, 255];
  };
  const out = [];
  for (const el of [root, ...root.querySelectorAll('*')]) {
    if (el.closest('svg') || !el.getClientRects().length) continue;
    if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    const a = lum(rgb(getComputedStyle(el).color)), b = lum(bg(el));
    out.push([el.textContent.trim(), (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)]);
  }
  return out;
}"""

VISIBLE_TEXT_JS = """() => document.body.innerText + '\\n'
  + [...document.querySelectorAll('svg text')].map(t => t.textContent).join('\\n')"""


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        # The installed Chrome first (no 150 MB download), then Playwright's own chromium.
        for kwargs in ({"channel": "chrome"}, {}):
            try:
                b = p.chromium.launch(**kwargs)
                break
            except sync_api.Error:
                continue
        else:
            pytest.skip("no chromium to launch (.venv/bin/playwright install chromium)")
        yield b
        b.close()


@pytest.fixture
def base_url(art, monkeypatch):
    import uvicorn

    from lake_rise.api import create_app

    for k in ("ALERT_ENABLED", "CALIB_ENABLED", "QPF_ARCHIVE_ENABLED", "HA_URL", "HA_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(art), host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def show(browser, base_url, tmp_path):
    """Open the page on a /field answer, run the every-state checks, save a screenshot."""
    contexts = []

    def _show(name, body, *, status=200, scheme="light"):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme)
        contexts.append(ctx)
        page = ctx.new_page()
        page.route("**/field", lambda route: route.fulfill(
            status=status, content_type="application/json", body=body))
        page.goto(f"{base_url}/")
        page.wait_for_selector("[data-testid=lake], [data-testid=error]")

        assert page.evaluate("document.documentElement.scrollWidth") <= 390
        assert not ABSOLUTE_ELEVATION.search(page.evaluate(VISIBLE_TEXT_JS))
        assert not INCHES_AS_IN.search(page.evaluate(VISIBLE_TEXT_JS))
        for dark in (False, True):
            page.emulate_media(color_scheme="dark" if dark else "light")
            for testid in ("header", "lake", "actions", "stop-logs", "error"):
                for el in page.get_by_test_id(testid).all():
                    pairs = el.evaluate(CONTRAST_JS)
                    assert pairs, testid
                    low = [(text, round(r, 2)) for text, r in pairs if r < 4.5]
                    assert not low, f"{testid} {'dark' if dark else 'light'}: {low}"
        page.emulate_media(color_scheme=scheme)
        page.screenshot(path=tmp_path / f"{name}.png", full_page=True)
        return page

    yield _show
    for ctx in contexts:
        ctx.close()


@pytest.fixture
def cfg(make_alert_config):
    return make_alert_config()


def _summary(art, cfg, bundle, *, gauge_problem=None, result=None, **s2):
    result = result or predict(bundle, art)
    return summarize(result, bundle, evaluate(result, bundle, art, cfg), art, observation=None,
                     trend_ft_per_h=0.05, gauge_problem=gauge_problem, computed_at=NOW, **s2)


def _previous(art, bundle, *, middle_by_ft, overtop_pct):
    """``bundle``'s forecast-log record with a lower middle peak and another overtop chance."""
    record = forecast_log.forecast_record(bundle, predict(bundle, art), artifact_version="t",
                                          art=art)
    return {**record, "as_of": (bundle.as_of - timedelta(hours=1)).isoformat(),
            "middle_peak_ft": record["middle_peak_ft"] - middle_by_ft,
            "levels_pct": {**record["levels_pct"], "overtop": overtop_pct}}


def _options(art, bundle):
    return [(k, predict(bundle.model_copy(update={"stop_log_count": k}), art))
            for k in range(bundle.stop_log_count - 1, -1, -1)]


WATCH_STORM = [_storm(8.0)] * 11 + [_storm(4.0)] * 10 + [_storm(0.0)] * 29
QUIET = [_storm(0.0)] * 50


@pytest.fixture
def normal(art, cfg):
    return _summary(art, cfg, _bundle(QUIET))


@pytest.fixture
def watch(art, cfg):
    # 11 of 50 members over the crest: Overtop 22%.
    return _summary(art, cfg, _bundle(WATCH_STORM))


def test_normal(show, normal):
    page = show("normal", normal.model_dump_json())
    assert page.get_by_test_id("normal").is_visible()
    assert page.get_by_test_id("status").count() == 0
    assert page.get_by_test_id("stick").count() == 0
    # The peak is a forecast: one line in the Next 3 days card, not in Lake now (D44).
    assert "3-day peak" not in page.get_by_test_id("lake").inner_text()
    assert page.get_by_test_id("next-3-days").get_by_test_id("peak-row").inner_text().startswith(
        "3-day peak")
    assert page.get_by_test_id("forecast-peaks").count() == 0


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_watch(show, watch, scheme):
    page = show(f"watch-{scheme}", watch.model_dump_json(), scheme=scheme)
    status = page.get_by_test_id("status").inner_text()
    assert "WATCH" in status and "Overtop 22%" in status
    assert page.get_by_test_id("peak-dot").count() == 50
    assert "22%" in page.get_by_test_id("level-overtop").text_content()
    # The dots are forecasts: their own "3-day peak" card between Lake now and Next 3 days (D44).
    assert page.get_by_test_id("lake").get_by_test_id("stick").count() == 0
    peaks = page.get_by_test_id("forecast-peaks")
    assert peaks.get_by_test_id("stick").count() == 1
    assert peaks.locator(".row").inner_text().split("\n") == ["3-DAY PEAK", "50 forecasts"]
    order = page.evaluate("""() => [...document.querySelectorAll('main > section.card')]
      .map(c => c.dataset.testid || c.getAttribute('aria-label'))""")
    assert order[:3] == ["lake", "forecast-peaks", "next-3-days"]
    # The stick stops at 4.8 ft, just above the 10,000-year storm's 4.73 ft, when every peak is
    # below it.
    assert max(watch.peaks_ft) < 4.6
    stick = page.get_by_test_id("stick")
    four = float(stick.locator("text.t-muted", has_text="4").first.get_attribute("y")) - 4
    top_ft = 4 + (four - 8) / page.evaluate("STICK_PX_PER_FT")   # the scale's top sits at y = 8
    assert top_ft == pytest.approx(4.8, abs=0.02)
    assert "5" not in stick.locator("text.t-muted").all_text_contents()
    assert "of 50" not in page.evaluate(VISIBLE_TEXT_JS)
    timeline = page.get_by_test_id("timeline")
    assert timeline.get_by_test_id("timeline-shade").count() == 2
    assert timeline.get_by_test_id("timeline-middle").count() == 1
    assert page.get_by_test_id("rain-bar").count() == len(watch.rain_6h["middle"])


# Each shaded band's colour, blended over the card at its fill opacity, and its contrast against the
# card. A band that is too faint reads as "no spread" (it vanished in dark mode, 2026-11-15).
SHADE_CONTRAST_JS = """svg => {
  const rgb = s => s.match(/[\\d.]+/g).map(Number);
  const lum = c => {
    const [r, g, b] = c.map(v => (v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const card = rgb(getComputedStyle(svg.closest('.card')).backgroundColor).slice(0, 3);
  return [...svg.querySelectorAll('[data-testid=timeline-shade]')].map(el => {
    const cs = getComputedStyle(el), a = Number(cs.fillOpacity);
    const fill = rgb(cs.fill).slice(0, 3).map((v, i) => a * v + (1 - a) * card[i]);
    const [x, y] = [lum(fill), lum(card)];
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  });
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_forecast_spread_is_visible_in_both_schemes(show, watch, scheme):
    page = show(f"spread-{scheme}", watch.model_dump_json(), scheme=scheme)
    outer, inner = page.get_by_test_id("timeline").evaluate(SHADE_CONTRAST_JS)
    assert outer >= 1.2 and inner >= 1.5, (outer, inner)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_timeline_labels_the_middle_peak(show, watch, scheme):
    page = show("timeline-peak", watch.model_dump_json(), scheme=scheme)
    _shot(page, f"timeline-peak-{scheme}")
    peak = page.get_by_test_id("timeline-peak")
    assert peak.text_content() == f"Peak {max(watch.timeline.middle_ft):.2f}"
    assert page.get_by_test_id("timeline-peak-dot").count() == 1
    svg, box = page.get_by_test_id("timeline").bounding_box(), peak.bounding_box()
    assert svg["x"] <= box["x"] and box["x"] + box["width"] <= svg["x"] + svg["width"]
    assert svg["y"] <= box["y"] and box["y"] + box["height"] <= svg["y"] + svg["height"]



# Peak hours at the left edge, mid-chart and the right edge; "warning" puts the peak just under the
# warning line, where its label would land on the warning label.
@pytest.mark.parametrize("peak_hour,peak", [(1, "high"), (30, "high"), (72, "high"),
                                            (1, "warning"), (30, "warning")])
def test_the_peak_label_stays_on_its_dot_wherever_the_peak_falls(show, watch, peak_hour, peak):
    now, n = watch.lake.now_ft, len(watch.timeline.middle_ft)
    warning = next(lv.stick_ft for lv in watch.levels if lv.key == "early_warning")
    rise = (warning - 0.08 if peak == "warning" else now + 1.5) - now
    hill = [round(now + rise - abs(h - peak_hour) * rise / n, 2) for h in range(1, n + 1)]
    shaped = watch.model_copy(update={"timeline": watch.timeline.model_copy(update={
        "middle_ft": hill, "bands": {k: hill for k in watch.timeline.bands}})})
    page = show(f"timeline-peak-{peak_hour}h-{peak}", shaped.model_dump_json())
    _shot(page, f"timeline-peak-{peak_hour}h-{peak}")
    svg = page.get_by_test_id("timeline").bounding_box()
    dot = page.get_by_test_id("timeline-peak-dot").bounding_box()
    label = page.get_by_test_id("timeline-peak").bounding_box()
    assert page.get_by_test_id("timeline-peak").text_content() == f"Peak {now + rise:.2f}"
    for zone in page.get_by_test_id("timeline").get_by_test_id("zone-label").all():
        z = zone.bounding_box()
        assert not (z["x"] < label["x"] + label["width"] and label["x"] < z["x"] + z["width"]
                    and z["y"] < label["y"] + label["height"] and label["y"] < z["y"] + z["height"]), (
            zone.text_content())
    assert svg["x"] <= label["x"] and label["x"] + label["width"] <= svg["x"] + svg["width"]
    assert label["x"] <= dot["x"] + dot["width"] and dot["x"] <= label["x"] + label["width"]
    assert dot["y"] - label["y"] < 30 and label["y"] < dot["y"] + dot["height"] + 20

def test_a_falling_lake_gets_no_peak_label(show, watch):
    now = watch.lake.now_ft
    falling = watch.model_copy(update={"timeline": watch.timeline.model_copy(update={
        "middle_ft": [round(now - 0.01 * (i + 1), 2) for i in range(len(watch.timeline.middle_ft))]})})
    page = show("timeline-falling", falling.model_dump_json())
    assert page.get_by_test_id("timeline-peak").count() == 0
    assert page.get_by_test_id("timeline-peak-dot").count() == 0

def test_fallback(show, art, cfg):
    rain = {"low": 1.0, "median": 3.0, "high": 5.5}
    scenarios = [ScenarioRain(name=n, hourly_in=[0.0] * 3 + [total] + [0.0] * 68)
                 for n, total in rain.items()]
    bundle = InputBundle(as_of=NOW, current_elevation_abs_ft=339.0, stop_log_count=3,
                         forecast_scenarios=scenarios)
    summary = _summary(art, cfg, bundle)
    assert summary.mode == "fallback"
    page = show("fallback", summary.model_dump_json())
    assert page.get_by_test_id("range-bar").count() == 1
    assert page.get_by_test_id("peak-dot").count() == 0
    assert "%" in page.get_by_test_id("level-overtop").text_content()


def test_stale_gauge(show, art, cfg):
    summary = _summary(art, cfg, _bundle([_storm(0.0)] * 50), gauge_problem="gauge liveness is stale")
    page = show("stale-gauge", summary.model_dump_json())
    assert page.get_by_test_id("gauge-flag").is_visible()


def test_ha_down_with_a_previous_result(show, watch):
    stale = watch.model_copy(update={"stale_since": watch.computed_at,
                                     "unavailable": "Can't reach the gauge"})
    page = show("ha-down-previous", stale.model_dump_json())
    assert "Can't reach the gauge" in page.get_by_test_id("banner").inner_text()


def test_ha_down_with_none(show):
    page = show("ha-down-none", json.dumps({"detail": "Can't reach the gauge"}), status=502)
    error = page.get_by_test_id("error")
    assert "Can't reach the gauge" in error.inner_text()
    assert error.get_by_role("button", name="Try again").is_visible()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_watch_with_change_shows_the_was_line_faster_actions_next_level_and_stop_logs(
        show, art, cfg, scheme):
    bundle = _bundle(WATCH_STORM)
    summary = _summary(art, cfg, bundle, trend_was_ft_per_h=0.02, options=_options(art, bundle),
                       previous=_previous(art, bundle, middle_by_ft=0.12, overtop_pct=10))
    page = show(f"watch-change-{scheme}", summary.model_dump_json(), scheme=scheme)
    assert page.get_by_test_id("was-line").text_content() == f"was {summary.change.middle_was_ft:.2f}"
    assert page.get_by_test_id("level-was-overtop").text_content() == " (was 10%)"
    assert page.get_by_test_id("trend-change").text_content() == " · faster"
    actions = page.get_by_test_id("actions").inner_text()
    assert all(a.short in actions for a in summary.actions + summary.next_level.actions)
    assert page.get_by_test_id("next-level").inner_text() == "At 3.30 ft"
    stop_logs = page.get_by_test_id("stop-logs")
    assert stop_logs.inner_text().startswith("Stop logs: 3 in")
    assert stop_logs.get_by_test_id("option-bar").count() == len(summary.stop_logs.options) == 4
    captions = [t.text_content() for t in stop_logs.locator("svg text").all()]
    assert {"Leave 3", "Pull 1", "Pull 2", "Pull all"} <= set(captions)


def test_normal_shows_no_change_no_actions_and_no_stop_logs(show, art, cfg):
    bundle = _bundle(QUIET)
    summary = _summary(art, cfg, bundle, options=_options(art, bundle),
                       previous=_previous(art, bundle, middle_by_ft=0.12, overtop_pct=10))
    page = show("normal-s2", summary.model_dump_json())
    for testid in ("was-line", "actions", "next-level", "stop-logs"):
        assert page.get_by_test_id(testid).count() == 0, testid
    assert "Warning" in page.get_by_test_id("timeline").text_content()


def test_stop_logs_hidden_when_pulling_changes_nothing(show, art, cfg):
    bundle = _bundle(WATCH_STORM)
    result = predict(bundle, art)
    summary = _summary(art, cfg, bundle, result=result, options=[(k, result) for k in (2, 1, 0)])
    page = show("stop-logs-unchanged", summary.model_dump_json())
    assert page.get_by_test_id("actions").is_visible()
    assert page.get_by_test_id("stop-logs").count() == 0


# ---- The details view ----

@pytest.fixture
def details(show):
    """Open the page on ``summary``, then its details view with every section expanded."""
    def _open(summary, *, scheme="light", name=None):
        page = show(f"details-{name or scheme}", summary.model_dump_json(), scheme=scheme)
        page.get_by_test_id("details").click()
        page.evaluate("document.querySelectorAll('.sec-h[aria-expanded=false]').forEach(h => h.click())")
        return page
    return _open


def _shot(page, name):
    """Keep a screenshot for a human to look at when SHOTS_DIR is set."""
    if os.environ.get("SHOTS_DIR"):
        Path(os.environ["SHOTS_DIR"]).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=Path(os.environ["SHOTS_DIR"]) / f"{name}.png", full_page=True)


def _no_forecast(art, cfg):
    bundle = InputBundle(as_of=NOW, current_elevation_abs_ft=339.0, stop_log_count=3,
                         forecast_scenarios=[ScenarioRain(name=n, hourly_in=[])
                                             for n in ("low", "median", "high")])
    summary = _summary(art, cfg, bundle)
    assert summary.mode == "no_forecast"
    return summary


def _rain(summary, rate, total=None):
    total = total or {"dry": 0.2, "middle": 0.8, "wet": 1.6}
    return summary.model_copy(update={
        "lake": summary.lake.model_copy(update={"rain_rate_in_per_h": rate}),
        "rain_total_in": total if summary.mode != "no_forecast" else None})


def _contrast_ok(page, testid):
    for dark in (False, True):
        page.emulate_media(color_scheme="dark" if dark else "light")
        pairs = page.get_by_test_id(testid).evaluate(CONTRAST_JS)
        assert pairs and all(r >= 4.5 for _, r in pairs), (testid, dark, pairs)


def test_rain_now_shows_only_when_it_is_raining(show, normal):
    page = show("rain-0", _rain(normal, 0.0).model_dump_json())
    assert page.get_by_test_id("rain-now").count() == 0
    page = show("rain-tiny", _rain(normal, 0.004).model_dump_json())
    assert page.get_by_test_id("rain-now").count() == 0
    page = show("rain-none", _rain(normal, None).model_dump_json())
    assert page.get_by_test_id("rain-now").count() == 0


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_rain_now_is_in_the_lake_card_header_with_contrast(show, normal, scheme):
    page = show(f"rain-{scheme}", _rain(normal, 0.12).model_dump_json(), scheme=scheme)
    tag = page.get_by_test_id("lake").get_by_test_id("rain-now")
    assert tag.inner_text().strip() == '0.12"/hr'
    assert tag.get_attribute("aria-label") == "Raining 0.12 inches per hour"
    assert page.get_by_test_id("normal").is_visible()
    _contrast_ok(page, "rain-now")


@pytest.mark.parametrize("kind", ["normal", "watch"])
def test_rain_forecast_is_in_the_next_3_days_card_not_the_lake(show, normal, watch, kind):
    page = show(f"rain-forecast-{kind}", _rain({"normal": normal, "watch": watch}[kind], 0.0)
                .model_dump_json())
    card = page.get_by_role("region", name="Next 3 days")
    text = card.get_by_test_id("rain-forecast").inner_text()
    assert 'Rain 0.80"' in text and "(0.20–1.60)" in text
    assert page.get_by_test_id("lake").get_by_test_id("rain-forecast").count() == 0
    _contrast_ok(page, "rain-forecast")


def test_no_forecast_has_no_rain_forecast(show, art, cfg):
    page = show("rain-no-forecast", _rain(_no_forecast(art, cfg), 0.12).model_dump_json())
    assert page.get_by_test_id("rain-forecast").count() == 0
    assert page.get_by_test_id("rain-now").is_visible()


def test_details_opens_and_closes(show, watch):
    page = show("details-toggle", watch.model_dump_json())
    page.get_by_test_id("details").click()
    assert page.get_by_test_id("details-view").is_visible()
    assert not page.get_by_test_id("lake").is_visible()
    assert re.fullmatch(r"All details · Updated \d+:\d\d [ap]m",
                        page.get_by_test_id("details-title").inner_text())
    assert page.get_by_test_id("back").bounding_box()["height"] >= 44
    page.get_by_test_id("back").click()
    assert page.get_by_test_id("lake").is_visible()
    assert not page.get_by_test_id("details-view").is_visible()


def test_every_section_expands(show, watch):
    page = show("details-sections", watch.model_dump_json())
    page.get_by_test_id("details").click()
    for sec in ("sec-factors", "sec-rain", "sec-cases", "sec-data"):
        h = page.get_by_test_id(sec)
        assert h.bounding_box()["height"] >= 56
        if h.get_attribute("aria-expanded") == "false":
            h.click()
        assert h.get_attribute("aria-expanded") == "true"
        panel = page.locator(f"#{h.get_attribute('aria-controls')}")
        assert panel.is_visible() and panel.inner_text().strip()
        h.click()
        assert not panel.is_visible()


def test_factor_bars_show_five_rows_and_a_net(details, watch):
    page = details(watch)
    rows = page.get_by_test_id("factor")
    assert rows.count() == 5
    texts = [r.inner_text() for r in rows.all()]
    assert [t.split("\n")[0] for t in texts] == [f.label for f in watch.details.factors]
    assert all(re.search(r"[+−]?\d\.\d\d ft", t) for t in texts)
    net = page.get_by_test_id("net").inner_text()
    assert "→" in net if watch.details.factors_window == "to peak" else "over 3 days" in net


def test_factors_say_they_are_the_forecast_and_over_what_window(details, watch):
    until = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)      # Fri 2 am Pacific
    peak = watch.model_copy(update={"details": watch.details.model_copy(
        update={"factors_window": "to peak", "factors_until": until})})
    page = details(peak)
    assert page.get_by_test_id("sec-factors").inner_text().startswith("What will raise the lake")
    assert page.get_by_test_id("factors-window").inner_text() == (
        "Forecast, middle case · now → peak Fri 2a")
    _contrast_ok(page, "factors-window")
    flat = watch.model_copy(update={"details": watch.details.model_copy(
        update={"factors_window": "3 days", "factors_until": None})})
    assert details(flat, name="flat").get_by_test_id("factors-window").inner_text() == (
        "Forecast, middle case · next 3 days")


def test_a_stale_factor_caption_starts_at_the_forecast_start_not_now(details, watch):
    start = datetime(2026, 11, 14, 22, tzinfo=timezone.utc)      # Sat 2 pm Pacific
    until = datetime(2026, 11, 15, 10, tzinfo=timezone.utc)      # Sun 2 am Pacific
    stale = watch.model_copy(update={
        "stale_since": watch.computed_at, "unavailable": "Can't reach the gauge",
        "timeline": watch.timeline.model_copy(update={"start": start}),
        "details": watch.details.model_copy(update={
            "factors_window": "to peak", "factors_until": until})})
    caption = details(stale, name="stale-factors").get_by_test_id("factors-window").inner_text()
    assert caption == "Forecast, middle case · Sat 2p → peak Sun 2a"
    assert "now" not in caption


def test_rain_columns_are_labelled_by_the_day_each_block_ends(details, art, cfg):
    start = datetime(2026, 11, 18, 1, tzinfo=timezone.utc)   # Tue 5 pm Pacific
    bundle = _bundle(WATCH_STORM).model_copy(update={"as_of": start})
    page = details(_summary(art, cfg, bundle))
    table = page.get_by_test_id("rain-table")
    assert [h.inner_text() for h in table.locator("thead th").all()][:2] == ["", "Wed"]
    assert "5 pm–5 pm" in table.locator("caption").inner_text()


def test_an_overtop_past_midnight_pacific_shows_the_right_weekday(details, watch):
    at = datetime(2026, 11, 14, 9, tzinfo=timezone.utc)      # Sat 1 am Pacific
    cases = [c.model_copy(update={"overtop_at": at, "overtop_reached": False})
             if c.name == "wet" else c for c in watch.details.cases]
    summary = watch.model_copy(update={"details": watch.details.model_copy(update={"cases": cases})})
    page = details(summary)
    assert "Sat 1a" in page.locator("#sec-cases-b").inner_text()


def test_tools_link_to_the_dashboard_modes_and_the_example_storm(details, watch):
    links = details(watch).locator("a.tool")
    assert [l.get_attribute("href") for l in links.all()] == [
        "dashboard?mode=manual", "dashboard?mode=backtest", "?example=storm"]
    assert all(l.bounding_box()["height"] >= 52 for l in links.all())


# A zone label's fill against the card it sits on, and its halo is the card's colour.
ZONE_LABEL_ON_CARD_JS = """el => {
  const rgb = s => s.match(/[\\d.]+/g).map(Number).slice(0, 3);
  const lum = c => {
    const [r, g, b] = c.map(v => (v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const cs = getComputedStyle(el), card = getComputedStyle(el.closest('.card')).backgroundColor;
  if (cs.stroke !== card) return 0;
  const [a, b] = [lum(rgb(cs.fill)), lum(rgb(card))];
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}"""

@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_example_storm_is_marked_end_to_end_and_links_back_to_live(browser, base_url, scheme):
    """The real /field/example behind the page: never the live /field."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme)
    try:
        page = ctx.new_page()
        asked = []
        page.on("request", lambda r: asked.append(r.url.removeprefix(base_url)))
        page.goto(f"{base_url}/?example=storm")
        page.wait_for_selector("[data-testid=lake], [data-testid=error]")
        _shot(page, f"example-storm-{scheme}")
        banner = page.get_by_test_id("banner")
        assert banner.inner_text().split("\n")[:2] == ["Example storm", "Not a real forecast"]
        link = banner.locator("a")
        assert link.inner_text() == "Back to live" and link.get_attribute("href") == "./"
        # The button sits beside the text, on one row: never wrapped under it.
        box, text = link.bounding_box(), banner.locator("span").first.bounding_box()
        assert box["height"] >= 44 and box["x"] > text["x"] + text["width"]
        assert box["y"] < text["y"] + text["height"] and text["y"] < box["y"] + box["height"]
        assert page.locator("#updated").inner_text() == "Example storm"
        assert page.get_by_test_id("status").inner_text().startswith("WATCH")
        # A storm underway: raining now and the lake rising, in the Lake now card.
        lake = page.get_by_test_id("lake")
        assert lake.get_by_test_id("rain-now").is_visible()
        assert "ft/hr" in lake.locator(".trend").inner_text()
        assert page.get_by_test_id("peak-dot").count() == 50
        assert page.get_by_test_id("timeline-peak").count() == 1
        assert page.get_by_test_id("was-line").count() == 1 and page.get_by_test_id("actions").is_visible()
        # The middle case sits on the EAP alert line: the level labels keep clear of its label.
        stick = page.get_by_test_id("stick")
        others = [stick.get_by_test_id("middle-label").bounding_box(),
                  stick.get_by_test_id("was-line").locator("text").bounding_box()]
        for level in stick.locator("[data-testid^=level-]:not([data-testid^=level-was])").all():
            b = level.bounding_box()
            for o in others:   # text boxes carry glyph side-bearings: touching by 2 px is clear
                assert not (b["x"] + 2 < o["x"] + o["width"] and o["x"] + 2 < b["x"] + b["width"]
                            and b["y"] + 2 < o["y"] + o["height"] and o["y"] + 2 < b["y"] + b["height"]), (
                    level.text_content())
        assert "/field/example" in asked and "/field" not in asked
        assert page.evaluate("document.documentElement.scrollWidth") <= 390
        assert not ABSOLUTE_ELEVATION.search(page.evaluate(VISIBLE_TEXT_JS))
        assert not INCHES_AS_IN.search(page.evaluate(VISIBLE_TEXT_JS))
        _contrast_ok(page, "banner")
        # The example's timeline tops out just above Overtop, so its label sits below the line,
        # on the card rather than a pink zone: it must read against the card (it was outlined).
        overtop = page.get_by_test_id("timeline").locator("[data-testid=zone-label].z-overtop")
        assert "on-card" in overtop.get_attribute("class")
        assert overtop.evaluate(ZONE_LABEL_ON_CARD_JS) >= 4.5
        page.get_by_test_id("details").click()
        assert page.get_by_test_id("details-title").inner_text() == "All details · Example storm"
        assert page.get_by_test_id("details-banner").is_visible()
        assert page.get_by_test_id("tool-live").get_attribute("href") == "./"
        assert page.get_by_test_id("tool-example").count() == 0
    finally:
        ctx.close()


def test_no_forecast_shows_only_data_and_model(details, art, cfg):
    page = details(_no_forecast(art, cfg))
    for sec in ("sec-factors", "sec-rain", "sec-cases"):
        assert page.get_by_test_id(sec).count() == 0
    assert page.get_by_test_id("sec-data").count() == 1
    assert "Soil:" in page.get_by_test_id("details-view").inner_text()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("kind", ["ensemble", "no-forecast"])
def test_details_view_fits_hides_elevations_and_keeps_contrast(details, art, cfg, watch, kind, scheme):
    summary = watch if kind == "ensemble" else _no_forecast(art, cfg)
    page = details(summary, scheme=scheme, name=f"{kind}-{scheme}")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert not ABSOLUTE_ELEVATION.search(page.evaluate(VISIBLE_TEXT_JS))
    assert not INCHES_AS_IN.search(page.evaluate(VISIBLE_TEXT_JS))
    for dark in (False, True):
        page.emulate_media(color_scheme="dark" if dark else "light")
        pairs = page.get_by_test_id("details-view").evaluate(CONTRAST_JS)
        low = [(t, round(r, 2)) for t, r in pairs if r < 4.5]
        assert pairs and not low, f"{'dark' if dark else 'light'}: {low}"
    page.emulate_media(color_scheme=scheme)
    _shot(page, f"details-{kind}-{scheme}")


# ---- Details on a stale path, focus on view switches, the gauge row ----

def _stale(summary, message):
    return summary.model_copy(update={"stale_since": summary.computed_at, "unavailable": message})


@pytest.mark.parametrize("message", ["Can't reach the gauge", "No new forecast"])
def test_details_keeps_the_stale_marker_and_a_net_without_endpoints(details, watch, message):
    page = details(_stale(watch, message))
    assert message in page.get_by_test_id("details-banner").inner_text()
    assert re.fullmatch(r"All details · As of \d+:\d\d [ap]m",
                        page.get_by_test_id("details-title").inner_text())
    net = page.get_by_test_id("net").inner_text()
    assert "→" not in net and re.match(r"Net (to peak|over 3 days)", net)


def test_a_fresh_summary_has_no_details_banner(details, watch):
    assert details(watch).get_by_test_id("details-banner").count() == 0


def test_focus_and_scroll_follow_the_view_switches(show, watch):
    page = show("details-focus", watch.model_dump_json())
    page.evaluate("window.scrollTo(0, 150)")
    before = page.evaluate("window.scrollY")
    assert before > 0
    page.evaluate("document.getElementById('details').click()")
    assert page.evaluate("document.activeElement.id") == "back"
    page.get_by_test_id("back").click()
    assert page.evaluate("document.activeElement.id") == "details"
    assert page.evaluate("window.scrollY") == before


def test_gauge_row_is_labelled_for_a_problem_and_for_no_age_when_stale(details, art, cfg, watch):
    problem = _summary(art, cfg, _bundle(QUIET), gauge_problem="gauge liveness is stale")
    assert "Lake gauge: gauge liveness is stale" in details(problem).locator("#sec-data-b").inner_text()
    stale = _stale(watch, "Can't reach the gauge")
    stale = stale.model_copy(update={"details": stale.details.model_copy(update={"gauge_age_min": None})})
    assert "Lake gauge: can't reach" in details(stale).locator("#sec-data-b").inner_text()


# ---- A failed refresh, and a refresh in Details ----

def test_a_failed_refresh_keeps_the_page_and_the_view_with_a_banner(show, watch):
    page = show("refresh-failed", watch.model_dump_json())
    page.unroute("**/field")
    page.route("**/field", lambda route: route.abort())
    page.get_by_test_id("refresh").click()
    banner = page.locator("main [data-testid=fetch-failed]")
    banner.wait_for()
    assert re.fullmatch(r"Can't refresh — showing \d+:\d\d [ap]m", banner.inner_text())
    assert page.get_by_test_id("lake").is_visible()
    assert page.get_by_test_id("error").count() == 0
    page.get_by_test_id("details").click()
    assert page.locator("#details-view [data-testid=fetch-failed]").is_visible()
    page.get_by_test_id("refresh").click()
    page.wait_for_function("!document.getElementById('refresh').hasAttribute('aria-busy')")
    assert page.get_by_test_id("details-view").is_visible()
    assert page.locator("#details-view [data-testid=fetch-failed]").count() == 1


def test_a_successful_refresh_clears_the_banner(show, watch):
    page = show("refresh-recovered", watch.model_dump_json())
    page.unroute("**/field")
    page.route("**/field", lambda route: route.fulfill(status=502, content_type="application/json",
                                                       body='{"detail": "x"}'))
    page.get_by_test_id("refresh").click()
    page.get_by_test_id("fetch-failed").first.wait_for()
    page.unroute("**/field")
    page.route("**/field", lambda route: route.fulfill(
        status=200, content_type="application/json", body=watch.model_dump_json()))
    page.get_by_test_id("refresh").click()
    page.get_by_test_id("fetch-failed").first.wait_for(state="detached")
    assert page.get_by_test_id("lake").is_visible()


def test_a_refresh_keeps_each_details_section_open_or_closed(show, watch):
    page = show("refresh-sections", watch.model_dump_json())
    page.get_by_test_id("details").click()
    page.get_by_test_id("sec-factors").click()     # open by default: close it
    page.get_by_test_id("sec-rain").click()        # closed by default: open it
    page.get_by_test_id("refresh").click()
    page.wait_for_function("!document.getElementById('refresh').hasAttribute('aria-busy')")
    assert page.get_by_test_id("sec-factors").get_attribute("aria-expanded") == "false"
    assert page.get_by_test_id("sec-rain").get_attribute("aria-expanded") == "true"
    assert page.get_by_test_id("sec-cases").get_attribute("aria-expanded") == "false"
