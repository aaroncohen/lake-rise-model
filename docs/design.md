# Crystal Lake field view — Design

Nov 14, 2026 · @Aaron

Status: approved (2026-11-14)

Reviewed: builder-check (Claude), codex gpt-5.6-sol, 2026-11-14; 31 items folded in as D19–D38, and the user settled U10 as D20.

## Intent

The field view is a phone-first page that tells a Crystal Lake dam operator, in seconds, whether the lake needs action and by when. It becomes the page everyone lands on, including from alert emails. The full analysis stays one tap away, never shown by default.

Why now: on 2026-11-14 operators reported that the live dashboard is too much to take in out in the rain, doesn't work on phones, and buries the answer in a dense multi-series graph. They like the amount of information, so nothing is removed; it moves behind a tap.

Later, and lower priority: a watershed scene. It shows the lake and its surroundings, with soil saturation, evaporation and the other contributions to the lake's change drawn as pictures. Each can be tapped for detail and a small graph, and a time slider runs underneath.

Success (my guess, to confirm): an operator opens the alert link on a phone in the rain and knows within seconds whether to act, what to do, and when to look again.

## Users and scenarios

The users are the dam operators. They use it mostly in emergencies, on a phone, often outdoors in the rain.

**Cover the downstream face?** A storm is on and the lake is rising. The operator has to decide whether overtopping is likely enough, and soon enough, to cover the downstream face of the dam with plastic. They need the chance of reaching the crest and how long until it might.

**Pull a stop log early?** The operator can remove a stop log before the peak to lower the spillway and pass more water. They need to know whether the forecast peak justifies it, and how much difference one log would make.

**Is it getting better or worse?** Checking on the dam's health during or after a storm, the operator needs the direction (rising, cresting, falling) and by how much over the next hours and days.

**How they get there.** The system's emergency email reaches them first. From then on they monitor the lake by opening this page on their phones, again and again, through the event (D1).

## Goals, non-goals, anti-goals

**Goals**

- An operator on a phone knows within seconds whether the lake needs action, how bad it could get, and when.
- Every visit shows the current status and what changed since the last forecast (D2).
- Shows what pulling stop logs now would do (D3).
- Shows when rain falls and how much (D10).
- Everything is in stick height (user preference).
- All of today's detail stays reachable, but not by default.
- The page and the alert emails agree: built by the same code from the same inputs, so the same forecast hour gives the same level and counts (D12, D19).

**Non-goals**

- New forecasting or model changes beyond counting chances (D12).
- Advising whether pulling a stop log is safe (D4).
- Lead-time or "time to prepare" features (D1).
- Logins, user accounts or per-user settings (D13).
- A native app or push notifications: alerts stay email.
- Rebuilding the desktop what-if and backtest tools: they stay as they are, one tap away.

**Anti-goals** (failure even if it works)

- A screen of sentences: anything a picture can carry is drawn (D7, D9).
- Sections shown when they don't apply, such as a status box or zero chances on a quiet day (D8).
- Any absolute elevation shown to an operator.
- The page saying something different from the alert email.

## Constraints

- **Stick height only.** Every level shown is the staff-gauge reading. Absolute elevation is never shown to users; it may stay inside the model (user, 2026-11-14).
- **Existing app.** The FastAPI service on the Synology NAS serves one static page (`static/index.html`, Chart.js 4.4.1 from jsDelivr) and the JSON API (`/live/predict`, `/simulate`, `/backtest`, `/alert/run`). The field view is built into this app, not a new one.
- **Same numbers as the alerts.** The page must show the same forecast, probabilities and actions as the alert emails, from the same pure predictor and alert rules.
- **EAP actions.** Recommended actions come from the dam's Emergency Action Plan, as the alert emails already encode them, not from new wording.
- **Not production yet.** Operators are trying it, but nothing depends on today's layout, so the default view can change outright.

## Product design

First mockup: [Crystal Lake field view mockup](https://claude.ai/artifact/GdkBWxNAcic8QDPgpKBFxt), with three phone screens (example storm, today, all details). Features and flows will be written here once the mockup settles.

The summary screen, top to bottom (second mockup). Sections appear only when the situation needs them (D8):

1. Status bar: alert-ladder level and the headline chance, with a trend arrow. Hidden when normal; a small "Normal" tag instead.
2. The lake and its stick: level now plus trend, and the stick drawing with zones, the water line, 50 peak dots, middle peak and last forecast's (D9). When normal: lake now and the 3-day peak range only.
3. Next 3 days: middle-case line and shading over danger zones with clock times, and the rain strip below (D10).
4. Do now: short actions with icons, plus the EAP actions for the next level as tags. Hidden when normal.
5. Stop logs: range bar per option on the same zones, with the overtopping chance under each (D3). Hidden when pulling changes nothing.
6. Details link: what's raising the lake, rain by day, the three cases, data and model, desktop tools.

### Features

**F1 — Field view is the landing page** (slice: S1) The app root serves the field view; the alert emails' link opens it. Phone-first (390 px), with the header showing the forecast time and a refresh button, in light or dark mode following the phone. A broken input never blanks the page: a stale gauge is flagged on the lake number; a missing forecast shows the lake alone; no recent ECMWF run falls back to the Apple band, labelled with percentages instead of counts.

**F2 — Status** (S1) The alert ladder's level (the same rules as the emails) as a coloured bar with an icon and the headline chance, e.g. WATCH · Overtop 22%. When normal, a small "Normal" tag instead.

**F3 — Lake and stick** (S1) The lake now with its trend over the last hour. The stick drawing has the danger zones (overtopping edges as bands, D6), the water line, and the dot tally of the 50 forecasts' 3-day peaks, coloured by zone (D11). Zone labels give each level's chance as a percentage (D39), and a line marks the middle peak. When normal: the lake now and the 3-day peak range.

**F4 — Next 3 days** (S1) The middle-case line and shading (half / 8 in 10 of the forecasts) over the danger zones, with clock-time axis labels. Below it, the rain strip: inches per 6 h, middle over wet (D10).

**F5 — What changed** (S2) The previous forecast's middle peak as a dashed line on the stick, with "was" labels on the counts that moved. The previous forecast is the newest forecast-of-record line at least 50 minutes old. Nothing is shown when nothing moved (under 0.05 ft and no count changed).

**F6 — Do now** (S2) The alert emails' preventative actions and the EAP actions for the next level up, cut to short labels with icons. Wording comes only from `alerting/eap.py` and `render._preventative_actions`, shortened, never new advice. Hidden when normal.

**F7 — Stop-log comparison** (S2) One range bar per option, from leaving them to pulling all of them, on the same zones: dry to wet with a middle mark, and the overtopping count under each (D3). Shown at any level (D4). Hidden when no logs are in or no option changes any count.

**F8 — Counts are the chances** (S1) With an ensemble, the chance of reaching a level is the share of the 50 forecasts' peaks that reach it, in the page and the alert emails (D12).

**F9 — Details** (S3) A phone screen of collapsible sections:

- what's raising the lake (surface runoff, rain on the lake, groundwater seepage, evaporation, out the spillways);
- rain per day by case;
- the three cases;
- data and model (gauge age, ECMWF run, soil saturation, agreement);
- links to today's desktop dashboard (what-if, backtest).

**F10 — Watershed scene** (S4, lower priority) An illustration of the lake and its surroundings, with soil saturation, evaporation, runoff and the spillways drawn as pictures. Tap one for its detail and a small graph; a time slider runs underneath. It gets its own mockup round when S4 starts.

### Screens and flows

Alert email → field view (F1–F7) → Details (F9) → desktop dashboard (existing what-if and backtest tools). Mockup screens: Example storm, Today, All details ([mockup](https://claude.ai/artifact/GdkBWxNAcic8QDPgpKBFxt)).

## Architecture

The field view is one new endpoint, one pure assembly module and one new static page inside the existing app. The model changes only to count chances (D12).

### Stack

- **Back end:** Python 3.11 and FastAPI, the existing service; pydantic for the response.
- **Front end:** one static page, `static/field.html`, in plain HTML, CSS and JS. The drawings are inline SVG built by small functions from the JSON. No framework and no Chart.js on this page; the existing dashboard keeps Chart.js. One page with a few drawings doesn't need more.
- **Tests:** pytest, as now, for the assembly module, the endpoint and D12. One Playwright smoke test (Python) renders the page at 390 px from fixture responses in a normal and a WATCH state.

### Components

- **`predict` (existing):** with an ensemble, chances become counts (D12), and the result carries the 50 members' peaks (`ensemble_peaks_ft`).
- **`field.py` (new, pure):** turns prediction results, the bundle, the alert decision and the previous forecast-of-record into the field-view summary. That covers status, counts per level (EAP and model levels, D5/D6), timeline and bands, rain per 6 h by case, stop-log options, what changed, actions, and the details. Everything is converted to stick height here, so the response carries no absolute elevation.
- **`GET /field` (new, api.py):** pulls live data the way `/live/predict` does (HA, archive spin-up, ensemble). It runs `predict` for the current stop logs and for each option with fewer, reads the previous forecast-of-record, and calls `alerting.rules.evaluate` for the level. It returns `field.py`'s summary, cached in memory for 2 minutes.
- **`static/field.html` (new):** draws the summary; Details (F9) is a second view in the same page.
- **Routing:** `/` serves the field view and the existing dashboard moves to `/dashboard`. The alert emails' link drops `?mode=live`.
- **Alert rules (existing):** use the counts when an ensemble is present (D12); the ladder itself is unchanged.

### Data model

No new storage. The response has a status; the lake (now, trend, stale flag); levels (name, stick height or \[low, high\] band, chance %); the 50 peaks; the middle and previous middle peak; a timeline (times, middle, bands); rain per 6 h (middle, wet); stop-log options (pulled, dry, middle, wet, overtopping count); actions (now, and the next level's); details.

### Integrations

- **Home Assistant (gauge, rain, Apple forecast):** a stale gauge is flagged on the lake number; with no Apple forecast, the ensemble alone drives it.
- **ECMWF archive:** with no run under 30 h old, the page falls back to the Apple band.
- **Forecast log:** with no previous record, the page shows no "what changed".

### Security and auth

Public, read-only: `GET /` and `GET /field` need no login (D13). The 2-minute cache bounds how hard public traffic can hit Home Assistant. The response holds no secrets. Endpoints that change anything keep their tokens.

### Deploy and operate

The same container, `deploy.sh` and reverse-proxy path. Requests are logged like the others.

### Considered and rejected

- **Precompute in the hourly alert job:** the lake number would be up to an hour old; the 1.1 s live compute plus the cache is fast enough.
- **Reuse `/live/predict`:** it has no counts, stop-log options or change, and a heavy payload.
- **A front-end framework or Chart.js for this page:** not needed for a few SVG drawings.
- **Fading-water stick:** the dot tally reads better (D11).

## Decision log

| ID | Decision | Alternatives | Why | Whose call |
| --- | --- | --- | --- | --- |
| D1 | The emergency email is the first warning; the page is for monitoring afterwards. The page needs no lead-time or "how long to prepare" feature. | Show time remaining to act (e.g. hours to get plastic on) | Operators learn of an overtopping risk from the email, then check the page often from their phones. Warning time comes from the email, not the page. | user |

D2 (user): every look at the page shows both the current status and what changed: whether the forecast got better or worse and whether the lake is rising faster or slower. Changed relative to what is U7.

D3 (user, U3): the page shows what pulling stop logs now would do, so operators can make that call earlier. My proposal for the form: beside the current forecast, the peak and chance of reaching each EAP level if one log is pulled now, and if all of them are. Hidden when no logs are in.

D4 (user, U2): the stop-log comparison shows at any lake level. Whether pulling a log is safe or possible is the operator's judgement; the page doesn't gate it.

D5 (user, U6): the field view shows both the EAP levels (3.30 / 3.90 / 4.40 ft; since the 2026-11 EAP also 2.30 ft, D49) and the model's thresholds (early warning 2.63, crest 3.82, bridge deck 4.32 ft). The model's lines can prompt actions of their own.

D6 (user's direction, my form; U8): until the upcoming survey measures the stop logs against the stick, both sets of numbers stand. The page draws each overtopping edge as a narrow band spanning the model's and the EAP's value: "overtopping begins 3.82–3.90 ft" and "bridge covered 4.32–4.40 ft". The forecast's chances keep using the model's lower values, the earlier and more cautious side. The EAP actions keep triggering on the EAP's gauge readings, as the alerts do now. After the survey, the offset is fixed from it and the bands collapse to single lines: a calibration task, not part of this design.

D7 (user, on the first mockup): cut the words. The summary carries numbers and short labels, not sentences or descriptions; explanations live in All details.

D8 (user): show only what the situation needs. When it's normal there is no big status box and no chances, actions, stop-log or agreement sections, just the lake, its 3-day range and the chart. As the level rises, sections appear: chances for levels in play, actions, the stop-log comparison while it changes something.

D9 (user): show, don't tell. Anything a picture can carry is drawn, not written. The stick is drawn with its danger zones and the lake's water line. The 50 forecast peaks are dots beside it, so their spread shows the chances and the agreement. The last forecast's middle peak is a dashed line, so the change is visible. The stop-log options are range bars on the same zones. Actions are short labels with icons.

D10 (user): the page must show when rain falls and how much. A rain strip sits under the 3-day chart on the same time axis: middle-case bars over pale wet-case bars, inches per 6 hours.

D11 (user, after comparing two stick drawings): the forecast peaks are a dot tally. Each of the 50 forecasts' 3-day peaks is a dot, stacked in rows by height beside the stick and coloured by the zone it lands in. The zone labels give the chance as a percentage ("Overtop 22%"; D39 replaced the original "11 of 50"). The rejected alternative was fading water in the stick. Also: the details screen says "surface runoff", not "runoff from the land".

D12 (user took my recommendation, U9): with an ensemble, the chance of reaching a level is the count of the 50 forecasts whose 3-day peak reaches it, as a fraction (2 % steps). The page and the alert emails both use it, so they always match. The fallback band keeps its smoothed percentages. Trade-off accepted: a level above every forecast's peak reads 0 %, not a small tail chance. Changes `predict` and the alert rules, so it needs the anchors and alert tests re-checked.

D13 (user): anyone with the link can open the field view (`https://storage.cohenaudio.com/lake-rise`, the address the alert emails use); no login. It stays read-only: endpoints that change anything (alert runs, calibration) keep their token checks.

D14 (mine, U7): "what changed" compares against the newest forecast-of-record line at least 50 minutes old. The NAS logs one every hour, so it works on any phone with no sign-in. Rejected: "since you last looked", which needs each phone to remember its last visit.

D15 (mine, U4): "better or worse" is the 3-day peak and the counts, plus the lake's trend over the last hour. No separate 6 h / 24 h views: the 3-day chart shows when within the window.

D16 (mine): light or dark mode follows the phone's setting. Light reads better in daylight, dark at night; both are checked for contrast.

D17 (mine): `/field` is computed live on request (about 1.3 s) and cached in memory for 2 minutes. Rejected: precomputing in the hourly job, which would make the lake number up to an hour old.

D18 (mine): action wording only shortens what `alerting/eap.py` and `render._preventative_actions` already say. The mockup's "Check every 2 h" becomes "Monitor more often", from the existing "Increase monitoring frequency".

D39 (user, while planning S1): chances read as percentages ("Overtop 22%"), never "11 of 50", on the page and in the emails. "11 of 50" would confuse readers who don't know about ensembles. The dots stay as the picture of the spread, with the key "one possible outcome". Under the hood a chance is still the share of forecasts reaching the level (D12).

D20 (user, U10): when the gauge already reads above an EAP level, the status bar leads with that observed level and the reading ("BRIDGE CLOSURE · at 3.95 ft"), with the forecast ladder level beneath it. The EAP actions apply now.

**Rulings from the design review (mine, 2026-11-14)**

- **D19 — Parity.** One shared live-forecast builder serves the alert job, `/live/predict` and `/field` (the alert job's path, `build_live_bundle`). The same inputs at the same hour give the same numbers. The page always shows the newest forecast with "Updated h:mm", so it may be newer than the last email. The status bar shows the current level from `evaluate()`, not the emails' send latch. This replaces "always agree".
- **D21 — Counts.** A level's count is the number of forecasts whose 3-day peak reaches it (peak ≥ level), so counts are cumulative. Levels, low to high: EAP 1 2.30 (D49); early warning 2.63; EAP 2 3.30 (was "EAP plastic"); overtopping begins (band 3.82–3.90, counted at 3.82); bridge covered (band 4.32–4.40, counted at 4.32).
  - A dot takes the colour of the highest level it reaches; a dot inside a band takes that band's level.
  - A level is "in play" when its count is 1 or more.
  - A level the lake is already over reads "reached".
  - Percentages use the actual member count, which can be under 50.
- **D22 — Scope of D12.** With an ensemble, `predict`'s threshold probabilities are the counts for every consumer (dashboard, log, alerts). "Already over it = 100 %" stays. The emails' EAP Likely/Possible lines keep using the middle and wet peaks.
  - Before S1 deploys, replay the alert decisions over the archived storms with both methods and report the differences to the user.
  - It blocks only if a past storm's level would drop; then the user decides.
- **D23 — Fallback (no ensemble run under 30 h).** The stick shows the low-to-high range as a bar with a middle mark, like the stop-log bars. Labels and the status headline are percentages; the chart is shaded between low and high; the rain strip shows the middle and high scenarios.
- **D24 — Failures.** Never a synthesized dry forecast.
  - **Home Assistant unreachable:** the last successful result, under a banner "As of h:mm — can't reach the gauge"; with none, an error card.
  - **No Apple forecast:** the ensemble drives it alone, with rain spread evenly within each day.
  - **No forecast of either kind:** the lake alone, plus the last valid forecast marked stale.
  - **Stale gauge** (`gauge_problem`, 15 min): flagged on the lake number, and the forecast still runs, as the alerts do.
  - Errors are never cached. Refresh re-requests through the 2-minute cache.
- **D25 — S1 response.** No stop-log options, "what changed" or actions; the schema grows in S2. A cache miss costs about 1.1 s for the live pull plus about 0.1 s per extra stop-log option.
- **D26 — Trend.** The 30-minute-smoothed reading now against the same one hour ago, from Home Assistant history. Under 0.03 ft/h reads "steady". "Faster or slower" (D2) compares that rate with the hour before, in S2's "what changed".
- **D27 — Chart.** The line is the middle case's trajectory; the shading is each hour's 25–75 % and 10–90 % across the members. The rain strip is the middle and wet cases' rain, in 6-hour blocks from the forecast start. Times are labelled Pacific; the horizon is the alert horizon (72 h).
- **D28 — Header time.** "Updated h:mm" is when the result was computed. The gauge's age and the ECMWF run time are in Details.
- **D29 — S1 acceptance.** Fixture-backed tests at 390 px for normal, WATCH, fallback, stale gauge and Home Assistant down. A dry-run email render shows counts. The user checks it on a real phone and okays the NAS deploy (the standing rule).
- **D30 — Playwright** is added as a dev dependency in S1 for those fixtures.
- **D31 — Routes.** `/` and `/?mode=live` serve the field view; `/?mode=manual|backtest` redirects to `/dashboard?mode=…`. The dashboard's POST endpoints stay public as they are today (already reachable through the alert link); hardening them is out of scope.
- **D32 — Stick frame.** Stick height is the sensor frame: absolute minus `sensor_to_absolute_offset_ft` (338.375), the frame the gauge was calibrated to against the stick. `staff_to_absolute_offset_ft` (339.0) is unused.
- **D33 — Forecast log grows in S2.** Each record gains per-level counts, the member peaks, the middle peak, the stop-log count and the rain source. "What changed" uses the previous record only if it is no more than 3 h old with the same stop-log count and rain source; otherwise it shows nothing.
- **D34 — Stop-log options.** Every count from the current one down to 0, pulled at the forecast start. Each bar shows dry, middle and wet, with the counts for levels in play underneath (this narrows D3 to the levels in play).
  - The section shows whenever an option changes any count, or the middle peak by 0.05 ft or more, at any level. This settles D4 against D8: effect, not alert level, decides.
  - A log count taken from the seasonal default is labelled "(default)".
- **D35 — Next-level actions.** "Next level" is the lowest EAP level not yet reached that the wet case reaches.
  - Short labels sit beside the full text in `alerting/eap.py`, as fixed IDs, so shortening can't change the advice.
  - The user approves the short wording when S2 is planned.
- **D36 — S2 acceptance** uses a deterministic replay of an archived storm, not a live one.
- **D37 — S3 content** is the mockup's All details sections.
  - Agreement reads as the percentage of forecasts within 0.1 ft of the middle.
  - Soil saturation reads as % full.
- **D38 — S4** is not planned until its mockup round.
- **D40 — Rain on the field view** (user request, 2026-11-15). The "Lake now" card's header row shows a small raindrop tag, `0.12 in/hr`, only while the rain gauge reads above 0.00 in/hr; the forecast's rain amount ("Rain 0.80 in (0.20–1.60)": middle, then dry to wet, summed over the 3 days) sits in the "Next 3 days" card's header line, not in the status. The rate comes from the HA rain-rate sensor (`sensor.gw3000b_rain_rate_piezo`), and it is hidden when HA fails or the sensor has not reported within the lake gauge's stale limit, so an old rate is never shown as current. The forecast total carries over with the rest of the forecast when a new build has none.
- **D41 — The factor bars say they are the forecast (user, 2026-11-15).** It was unclear whether "what's raising the lake" meant now or the forecast. The header reads "What will raise the lake", with a first line "Forecast, middle case · now → peak {Tue 5p}", or "· next 3 days" when the lake doesn't rise. The server sends the peak time as `factors_until`.
- **D42 — Example storm for demonstrations (user, 2026-11-15).** `/?example=storm` shows the field view on a fixed 50-member storm (`presets.example_storm`: a storm 22 hours into a 48-hour atmospheric river, so it is raining now and the lake rising; the 50 members share the rain so far, and their storm totals sit on a bell curve around a likely 7.0", ±1.6"; a WATCH whose dots bunch around the EAP alert line and thin out toward Warning and Overtop), built through the same prediction, alert evaluation and summary as `/field` by `GET /field/example`. It reads nothing live and writes nothing (no Home Assistant, alert state, forecast log or `/field` cache). The page marks it: header "Example storm", a banner "Example storm — not a real forecast · Back to live" on the summary and Details. It is reached from Details › Tools ("Example storm"), one tap deeper than the live view so it isn't hit by accident mid-storm.
- **D43 — "EAP alert", not "Plastic" (user, 2026-11-15; superseded by D49).** The 3.30 ft level (the EAP's Mandatory Alert: notify all, move east-side cars, sandbag and plastic on the downstream slope) is labelled "EAP alert" on the field view, which was unclear as "Plastic". Short enough for the charts, and it names the EAP level operators know. The level's key stays `eap_plastic`; earlier plans that say "Plastic" mean this label.
- **D44 — The dots are forecasts, not the lake now (user, 2026-11-15).** The stick with the 50 forecasts' dot tally moves out of the "Lake now" card into its own card, "3-day peak · 50 forecasts", between Lake now and Next 3 days. On a normal day the "3-day peak" line moves into the Next 3 days card. Lake now keeps only what is measured now: the level, its trend, rain now and gauge problems. Supersedes the stick's placement in F3/D9.
- **D45 — Inches are written " (user, 2026-11-15).** Throughout the field view and the desktop dashboard: `0.24"/hr`, `Rain 0.32"`, `Rain per 6 h (")`. Screen-reader labels still say "inches". "Stop logs: 3 in" means logs in place and stays.
- **D46 — "Driest, middle, wettest" (user, 2026-11-15).** The forecast cases are named "driest", "middle" and "wettest" on the field view and the desktop dashboard: "dry" and "wet" read as the weather. The API keys stay `dry`/`middle`/`wet` (and the model's `low`/`median`/`high`). Earlier entries that say "dry" and "wet" case mean these.
- **D47 — The lake reading is filtered (user request, 2026-11-15; docs/plans/lake-filter.md).** The forecast's starting lake level and the page's "Lake now" trend come from a rain-adaptive, model-assisted Kalman filter over the 1-minute raw depth instead of the 30-minute median, which stays as the fallback when the filter can't run. **It runs in dry weather only (P16, user, 2026-11-16):** in rain, or with the rain gauge offline, within its wet window, the reading is the 30-minute median.
- **D48 — Desktop columns (user, 2026-11-16).** From 1100 px wide the field view's cards flow into three columns so a storm fits one screen. The header carries Details next to refresh, and the banner and status bar span the full width. Left: Lake now and the 3-day peak. Middle: the next 3 days. Right: what to do, with Do now above the stop-log options. A quiet day, with no 3-day peak card, stays the phone column, which already fits. Details is capped at 720 px. At 1440×900 a storm fits except the stop-log percentages, just below the fold. The phone layout is unchanged.
  - The trend is the filter's current rate, and "faster or slower" compares it with the same rate an hour earlier (P10). This supersedes D26's trend; the 0.03 ft/h "steady" line and the page copy are unchanged.
  - Storm lag (P12, the user's bound): with the model blind to a storm in rain, the filtered reading may trail today's 30-minute median by at most 0.03 ft more at 30 and 60 minutes after the rise starts, and by no more at 120 minutes. With the model right or at half the rate, it is never further behind than today's.
  - Its parameters are set by `lake-rise lake-filter-study` (calibration log, 2026-11-15 entry). The observed-EAP crossing state, the hourly archive and the backtest stay unfiltered (P11). `/live/predict` starts every option, the dashboard's what-ifs included, from the filtered level when available, else the 30-minute median (P11 as amended, #74).
- **D49 — The revised EAP action table (user, 2026-11-23).** The EAP's Water Level / Action Table (KI08-0195) was revised; calibration log 2026-11-23. `alerting/eap.py` now holds its five action rows, low to high: 2.30 ft Emergency Level 1 (alert DSO; monitor daily; optionally lower the stop logs 4" on a bad forecast), 2.60 ft lower the stop logs 4" or more, 3.30 ft Emergency Level 2 (alert NORCOM, KCOEM, DSO; east-side cars to the west side), 3.90 ft bridge closure (overtopping starts 25 ft east of the bridge; tarps and plastic with sandbags at the point of overtopping; pull the remaining stop logs), 4.40 ft Emergency Level 3 (bridge deck overtopped; overtopping by 6" for 12 h is "imminent failure of the dam"). The rows are yellow (2.30, 2.60) or red (3.30 and up) on the table and the pole; the emails print the colour.
  - Every row pages on the observed track (user): 2.30 and 2.60 to ops, then emergency, road and evacuate as before. The track re-arms below 2.25 ft for 30 minutes.
  - The field view gains an "EAP 1" line and chance at 2.30 ft; the 3.30 ft level reads "EAP 2" (key `eap2`, was `eap_plastic`, since plastic now goes on at 3.90). The 2.60 ft row has no line: it sits on the model's Warning line (2.63). Forecast-log records from before keep `eap_plastic`; the change line reads it as `eap2`.
  - The EAP no longer says "evacuate downstream" or names KCDOT. The forecast ladder's top level keeps the name EVACUATE and the `evacuate` audience (so deployed settings keep working) but is shown as "Imminent Failure" in notices and on the page (user).
  - The pool rows (summer 1.30, winter 0.97, winter with one more log out 0.65 ft) are unchanged. New stop-log heights are due within days (2026-11-23); they feed the artifact's `stop_logs.board_heights_ft`, not the EAP levels.

## Unknowns

| ID | Question | Status |
| --- | --- | --- |
| U1 | How much warning does covering the downstream face with plastic need? (Not a page concern: the email warns first.) | → D1 |
| U2 | Up to what lake level can a stop log still be pulled safely, and how long does it take? | → D4 |
| U3 | Should the page compare the forecast with one fewer stop log ("pull one now: peak X instead of Y")? | → D3 |
| U4 | What counts as "better or worse" for a health check: the next 6 h, 24 h, 3 days? | → D15 |
| U5 | Where do the EAP's action wordings live today, and do they cover plastic and stop logs? | Found: `alerting/eap.py` has the EAP levels by stick height (3.30 ft Mandatory Alert: sandbag and cover the downstream slope with plastic; 3.90 ft Bridge Closure; 4.40 ft Evacuate Downstream). `render._preventative_actions` adds "pull the stop logs" when the crest chance is at least 10 % or an EAP level is likely. |
| U6 | The page's danger lines are the model's thresholds (early warning 2.63, crest 3.82, bridge deck 4.32 ft stick), not the EAP's (3.30 / 3.90 / 4.40 ft). Which does the field view show? My guess: the EAP levels, since operators act on them. | → D5 |

U7 (→ D14): "what changed" relative to what? My guess: against the forecast of an hour or so earlier, which the forecast log on the NAS already keeps hourly, plus the lake's measured change over the last hour and 3 hours. That works on any phone with no sign-in. The alternative, "since you last looked", would need each phone to remember its last visit.

U8 (→ D6): overtopping is a range, not a line (user, 2026-11-14). It begins at the crest's low point, about 25 ft east of the bridge. The wetted crest then widens until the bridge deck, 0.50 ft higher, is covered. The EAP escalates to imminent failure at overtopping by 6 in (over the low point, so the deck just covered) sustained for 12 h (corrected 2026-11-23; first written here as 6 in over the deck). The model and the EAP put the start and the deck 0.08 ft apart: model 3.82 / 4.32 ft vs EAP 3.90 / 4.40 ft stick. The model deliberately anchors to the surveyed crest and keeps only the EAP's 0.50 ft interval (calibration log, 2026-06-13), inside the \~0.12 ft datum uncertainty. Open: which numbers label the zone's edges on the page. My guess: draw overtopping as one shaded zone from start to bridge covered, labelled with the EAP numbers operators know.

U9 (→ D12): the dot counts and the alert percentages can disagree. In the example, 6 of 50 forecasts cover the bridge (12 %), but the alert says 15 %, because it smooths a tail beyond the highest dot. My recommendation: page and alerts both use the counts, so they always match. 50 forecasts give 2 % steps, fine for the alert ladder's 10–60 % triggers. The fallback band (no recent ensemble run) keeps its smoothed percentages and shows them as such.

U10 (→ D20): when the gauge already reads above an EAP level (e.g. 3.95 ft, Bridge Closure), what does the status bar lead with: the observed EAP level, the forecast ladder level, or both? My proposal: the observed EAP level leads ("BRIDGE CLOSURE · at 3.95 ft") and the forecast level sits beneath it, since the EAP actions apply now.

## Slices

**S1 — Field view, end to end** Delivers: F1, F2, F3, F4, F8. Depends on: D5, D6, D7–D13, D16, D19–D32. Done when the alert link on a phone opens the field view with today's real status, lake, stick tally, 3-day chart and rain strip. The alert emails use counts. Anchors and tests pass, and it's deployed to the NAS.

**S2 — What changed, actions, stop logs** Delivers: F5, F6, F7. Depends on: D2, D3, D4, D14, D33–D36. Done when a WATCH situation (fixture and live) shows the change from the previous forecast, the actions and the stop-log options, and a quiet day shows none of them.

**S3 — Details** Delivers: F9. Done when the details screen covers what operators open the desktop dashboard for today (factors, rain by day, cases, data) and links to it for what-if and backtest.

**S4 — Watershed scene** (lower priority) Delivers: F10, after its own mockup round.

## Risks

- **Counting changes when alerts fire (D12).** A storm beyond every forecast reads 0 % instead of a small chance. Early check: in S1, replay the alert decisions over the archived storms with both methods and compare the levels.
- **Datum offset (D6).** Until the survey, the overtopping edges are 0.08 ft bands. If the survey moves the offset, the counts move too.
- **Two display modes.** With no recent ECMWF run the page falls back to percentages, a path that's rarely exercised. The Playwright fixtures cover both.
- **Load and latency.** A cache miss runs up to 4 forecasts plus the HA pull (about 1.3 s). A slow Home Assistant makes a slow page; the 2-minute cache keeps it to one pull per 2 minutes.
- **Outdoor legibility.** Zone colours and small labels in sun and rain; dark mode at night. Check on a real phone before S1 is called done.

## Left to the builder

- Exact colours, spacing and icons, within the mockup's look; contrast must pass in both modes.
- How the SVG drawing code is organised, and the JSON field names.
- The cache mechanism (in-process is enough).
- How the Playwright fixtures are served.

## Status

| Slice | Plan | State |
| --- | --- | --- |
| S1 | docs/plans/s1-field-view.md | implemented |
| S2 | docs/plans/s2-change-actions-stoplogs.md | implemented |
| S3 | docs/plans/s3-details.md | implemented |
| S4 | docs/plans/s4-watershed-scene.md | not planned |
| Lake filter | docs/plans/lake-filter.md | implemented |
