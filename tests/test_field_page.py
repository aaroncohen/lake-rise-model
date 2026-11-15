"""The field page in a real browser at phone width (390 x 844), one test per state an operator
can meet. Each state's /field answer is built with ``field.summarize`` on synthetic inputs and
served by route interception, so it can't drift from the schema. Skips without a browser."""

import json
import re
import socket
import threading
import time
from datetime import datetime, timezone

import pytest

from lake_rise import forecast_log
from lake_rise.alerting.rules import evaluate
from lake_rise.bundle import InputBundle, ScenarioRain
from lake_rise.field import summarize
from lake_rise.predict import predict

from test_ensemble_rain import _bundle, _storm

NOW = datetime(2026, 11, 14, 13, tzinfo=timezone.utc)
ABSOLUTE_ELEVATION = re.compile(r"\b3(39|4[0-3])\.\d")

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
    return {**record, "middle_peak_ft": record["middle_peak_ft"] - middle_by_ft,
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
    assert "3-day peak" in page.get_by_test_id("lake").inner_text()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_watch(show, watch, scheme):
    page = show(f"watch-{scheme}", watch.model_dump_json(), scheme=scheme)
    status = page.get_by_test_id("status").inner_text()
    assert "WATCH" in status and "Overtop 22%" in status
    assert page.get_by_test_id("peak-dot").count() == 50
    assert "22%" in page.get_by_test_id("level-overtop").text_content()
    assert "of 50" not in page.evaluate(VISIBLE_TEXT_JS)
    timeline = page.get_by_test_id("timeline")
    assert timeline.get_by_test_id("timeline-shade").count() == 2
    assert timeline.get_by_test_id("timeline-middle").count() == 1
    assert page.get_by_test_id("rain-bar").count() == len(watch.rain_6h["middle"])


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
