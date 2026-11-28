"""Front-view elevation drawing of the two spillways and their stop logs, as SVG.

Vertical is to scale in stick feet (the lake staff gauge); horizontal is representative only.
Every height comes from the artifact (the 2026-11-25 survey in ``stop_logs``, the spillway
openings in ``spillway``) or from the EAP table (``alerting.eap``), so the drawing is redrawn,
not edited, when either changes: ``lake-rise spillway-drawing``.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from .alerting.eap import EAP_LEVELS, EAP_POOL_ROWS
from .artifact import Artifact

# Layout (px). Only the vertical axis carries data: PX_PER_FT stick feet.
PX_PER_FT = 150.0
STICK_TOP, STICK_BOTTOM = 4.75, -0.30
TOP, LEFT_GAUGE, WIDTH = 96.0, 64.0, 1300.0
LINE_END, LABEL_X = 846.0, 858.0
MAIN_X, MAIN_W = 214.0, 250.0           # the main opening: 10 ft of crest
AUX_X = 594.0                            # the aux opening, drawn 7.5/10 as wide
PIER_W = 30.0

INK, MUTED, PAPER = "#1f2328", "#59636e", "#ffffff"
CONCRETE, CONCRETE_EDGE = "#d5d7da", "#8c959f"
BOARD, BOARD_DARK, BOARD_EDGE = "#e7c38f", "#c99a5b", "#7a5326"
POLE = {"green": "#1a7f37", "yellow": "#9a6700", "red": "#cf222e"}
MODEL = "#8250df"


def _y(stick_ft: float) -> float:
    return TOP + (STICK_TOP - stick_ft) * PX_PER_FT


def _ft(stick_ft: float) -> str:
    """Two decimals, rounding half up (3.825 -> 3.83, not the float's 3.82)."""
    return f"{round(stick_ft + 1e-9, 2):.2f}"


def _inches(ft: float) -> str:
    inches = round(ft * 12 * 8) / 8          # nearest 1/8 in
    whole, frac = int(inches), inches - int(inches)
    frac_txt = {0.0: "", 0.125: "⅛", 0.25: "¼", 0.375: "⅜", 0.5: "½", 0.625: "⅝",
                0.75: "¾", 0.875: "⅞"}[round(frac, 3)]
    return f"{whole}{frac_txt}″"


@dataclass(frozen=True)
class Board:
    bottom: float
    top: float
    name: str
    note: str            # "in now", "mortared", "summer", ...
    added: bool = False  # drawn dashed: not in at the current (winter) count


def leg_boards(art: Artifact) -> dict[str, list[Board]]:
    """Each leg's stack, bottom first, from the bare sill to the summer-normal crest."""
    sl = art.stop_logs
    h = sl.log_height_ft
    out: dict[str, list[Board]] = {}
    for key, leg in (("primary", sl.primary), ("auxiliary", sl.auxiliary)):
        boards: list[Board] = []
        z = leg.sill_stick_ft(h)
        for i, bh in enumerate(leg.base_board_heights_ft):
            mortared = i < leg.mortared_boards
            boards.append(Board(z, z + bh, "Base board",
                                "mortared in place" if mortared else "removable in an emergency"))
            z += bh
        for n in range(1, sl.summer_count + 1):
            top = leg.crest_stick_ft(n, h)
            if top <= z + 1e-9:
                continue                      # this count doesn't add a log on this leg
            boards.append(Board(z, top, f"Log {n}",
                                "in for winter" if n <= sl.winter_count else "added for summer",
                                added=n > sl.winter_count))
            z = top
        out[key] = boards
    return out


def _text(x: float, y: float, s: str, *, size: int = 13, anchor: str = "start", weight: str = "400",
          fill: str = INK, style: str = "") -> str:
    italic = f' font-style="{style}"' if style else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" '
            f'font-weight="{weight}" fill="{fill}"{italic}>{escape(s)}</text>')


def _gauge() -> list[str]:
    x0, w = LEFT_GAUGE, 34.0
    top, bot = _y(4.7), _y(0.0)
    out = [f'<rect x="{x0}" y="{top:.1f}" width="{w}" height="{bot - top:.1f}" fill="{PAPER}" '
           f'stroke="{INK}" stroke-width="1.5"/>',
           _text(x0 + w / 2, top - 30, "Stick", size=13, anchor="middle", weight="600"),
           _text(x0 + w / 2, top - 14, "(ft)", size=12, anchor="middle", fill=MUTED)]
    for k in range(0, 95):                        # every 0.05 ft from 0 to 4.70
        s = k * 0.05
        y = _y(s)
        long = k % 10 == 0
        tick = 18 if long else (12 if k % 2 == 0 else 6)
        out.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x0 + tick}" y2="{y:.1f}" stroke="{INK}" '
                   f'stroke-width="{1.4 if long else 0.8}"/>')
        if long:
            out.append(_text(x0 - 6, y + 4.5, f"{s:.1f}", size=13, anchor="end", weight="600"))
    return out


def _concrete(x: float, y_top: float, w: float, y_bot: float) -> str:
    return (f'<rect x="{x:.1f}" y="{y_top:.1f}" width="{w:.1f}" height="{y_bot - y_top:.1f}" '
            f'fill="url(#hatch)" stroke="{CONCRETE_EDGE}" stroke-width="1"/>')


def _stack(x: float, w: float, boards: list[Board], crests: dict[float, str]) -> list[str]:
    """The boards, each labelled with its height and top; ``crests`` marks the tops water
    first passes over (winter, summer)."""
    out = []
    for i, b in enumerate(boards, 1):
        yt, yb = _y(b.top), _y(b.bottom)
        if b.added:
            style = f'fill="{BOARD}" fill-opacity="0.35" stroke="{BOARD_EDGE}" stroke-dasharray="6 4"'
        elif b.note.startswith("mortared"):
            style = f'fill="url(#mortar)" stroke="{BOARD_EDGE}"'
        else:
            style = f'fill="{BOARD}" stroke="{BOARD_EDGE}"'
        out.append(f'<rect x="{x:.1f}" y="{yt:.1f}" width="{w:.1f}" height="{yb - yt:.1f}" {style} '
                   f'stroke-width="1.2"/>')
        mid = (yt + yb) / 2
        name = f"{b.name} {i}" if b.name == "Base board" else b.name
        out.append(_text(x + w / 2, mid - 2, f"{name} · {_inches(b.top - b.bottom)}", size=13,
                         anchor="middle", weight="600"))
        out.append(_text(x + w / 2, mid + 13, f"{b.note} · top {_ft(b.top)}", size=11.5,
                         anchor="middle", fill=MUTED if b.added else INK))
        crest = next((label for top, label in crests.items() if abs(top - b.top) < 1e-6), None)
        if crest is not None:
            out += _crest_mark(x, b.top, crest, outlined=b.added)
    return out


def _crest_mark(x: float, stick_ft: float, label: str, *, outlined: bool) -> list[str]:
    """A ▼ on the crest at the opening's left edge, labelled: where water first passes over."""
    y = _y(stick_ft)
    cx = x + 12
    color = POLE["green"]
    return [f'<path d="M {cx - 7:.1f} {y - 12:.1f} L {cx + 7:.1f} {y - 12:.1f} L {cx:.1f} {y:.1f} Z" '
            f'fill="{PAPER if outlined else color}" stroke="{color}" stroke-width="1.5"/>',
            _text(cx + 11, y - 3, label, size=11.5, weight="700", fill=color)]


def _threshold(stick_ft: float, color: str, label: str, *, dash: str | None, width: float = 1.3) -> list[str]:
    y = _y(stick_ft)
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return [f'<line x1="{LEFT_GAUGE + 34:.1f}" y1="{y:.1f}" x2="{LINE_END:.1f}" y2="{y:.1f}" '
            f'stroke="{color}" stroke-width="{width}"{d}/>',
            _text(LABEL_X, y + 4.5, f"{_ft(stick_ft)}", size=13, weight="700", fill=color),
            _text(LABEL_X + 42, y + 4.5, label, size=12.5, fill=color)]


def render_svg(art: Artifact) -> str:
    """The drawing as a standalone SVG document."""
    sl = art.stop_logs
    off = art.datum.sensor_to_absolute_offset_ft
    boards = leg_boards(art)
    aux_w = MAIN_W * (art.spillway.auxiliary.crest_length_ft / art.spillway.primary.crest_length_ft)
    bottom = _y(STICK_BOTTOM)
    height = bottom + 150

    body: list[str] = []
    # EAP lines go under the structures; labels sit to the right.
    for ft, name in EAP_POOL_ROWS:
        body += _threshold(ft, POLE["green"], f"EAP pool · {name}", dash="2 4", width=1.1)
    for lv in EAP_LEVELS:
        label = {"Bridge Closure": "EAP · overtopping starts (low point E of bridge); close the bridge",
                 "Emergency Level 3": "EAP Emergency Level 3 · bridge deck overtopped"}.get(
            lv.title, f"EAP {lv.title} · {lv.short_actions[0]}")
        heavy = lv.title in ("Bridge Closure", "Emergency Level 3")
        body += _threshold(lv.gauge_ft, POLE[lv.pole_color], label, dash=None if heavy else "8 4",
                           width=2.2 if heavy else 1.3)

    # Structures. Each leg's piers rise to its opening top (soffit); the deck top is the EAP's 4.40.
    legs = (("primary", MAIN_X, MAIN_W, art.spillway.primary),
            ("auxiliary", AUX_X, aux_w, art.spillway.auxiliary))
    deck = max(lv.gauge_ft for lv in EAP_LEVELS)
    for key, x, w, leg in legs:
        sill = boards[key][0].bottom
        soffit = (leg.soffit_elev_ft - off) if leg.soffit_elev_ft is not None else None
        pier_top = soffit if soffit is not None else sill + 3.0
        body.append(_concrete(x - PIER_W, _y(sill), w + 2 * PIER_W, bottom))           # sill
        body.append(_concrete(x - PIER_W, _y(pier_top), PIER_W, _y(sill)))            # piers
        body.append(_concrete(x + w, _y(pier_top), PIER_W, _y(sill)))
        leg_sl = getattr(sl, key)
        crests = {round(leg_sl.crest_stick_ft(sl.winter_count, sl.log_height_ft), 6): "overflow (winter)",
                  round(leg_sl.crest_stick_ft(sl.summer_count, sl.log_height_ft), 6): "overflow (summer)"}
        body += _stack(x, w, boards[key], crests)
        body.append(_text(x + w / 2, _y(sill) + 18, f"bare sill · {_ft(sill)}", size=11.5,
                          anchor="middle", fill=MUTED, style="italic"))
        if soffit is not None:
            ys = _y(soffit)
            body.append(f'<line x1="{x - PIER_W:.1f}" y1="{ys:.1f}" x2="{x + w + PIER_W:.1f}" y2="{ys:.1f}" '
                        f'stroke="{INK}" stroke-width="1.4" stroke-dasharray="5 3"/>')
        title = "Main (primary) spillway" if key == "primary" else "Auxiliary spillway"
        body.append(_text(x + w / 2, bottom + 22, title, size=14, anchor="middle", weight="700"))
        body.append(_text(x + w / 2, bottom + 40, f"{leg.crest_length_ft:g} ft crest", size=12,
                          anchor="middle", fill=MUTED))

    # The bridge spans the main spillway: from the main's opening top (its underside, an
    # estimate from the rating) up to the deck top (the EAP's 4.40).
    prim = art.spillway.primary
    if prim.soffit_elev_ft is not None:
        under = prim.soffit_elev_ft - off
        bx, bw = MAIN_X - PIER_W - 18, MAIN_W + 2 * PIER_W + 36
        body.append(f'<rect x="{bx:.1f}" y="{_y(deck):.1f}" width="{bw:.1f}" height="{_y(under) - _y(deck):.1f}" '
                    f'fill="url(#hatch)" stroke="{CONCRETE_EDGE}" stroke-width="1.2"/>')
        body.append(_text(bx + bw / 2, _y(deck) + 22, "Bridge", size=14, anchor="middle", weight="700"))
        body.append(_text(bx + bw / 2, _y(deck) + 40, f"deck top {_ft(deck)} (EAP)", size=12, anchor="middle"))
        body.append(_text(bx + bw / 2, _y(under) - 10, f"underside {_ft(under)} (estimated)", size=12,
                          anchor="middle"))
    aux = art.spillway.auxiliary
    if aux.soffit_elev_ft is not None:
        top = aux.soffit_elev_ft - off
        body.append(_text(AUX_X + aux_w / 2, _y(top) - 8, f"opening top {_ft(top)}",
                          size=12, anchor="middle", fill=MUTED, style="italic"))

    # The model's overtopping levels, where they differ from the EAP's (the datum is unsurveyed).
    model = [(art.thresholds_abs_ft.dam_crest - off, "model overtop"),
             (art.thresholds_abs_ft.bridge_deck - off, "model deck")] if art.thresholds_abs_ft.bridge_deck else []
    for ft, name in model:
        y = _y(ft)
        body.append(f'<line x1="{LEFT_GAUGE + 34:.1f}" y1="{y:.1f}" x2="{LEFT_GAUGE + 120:.1f}" y2="{y:.1f}" '
                    f'stroke="{MODEL}" stroke-width="1.2" stroke-dasharray="2 2"/>')
        body.append(_text(LEFT_GAUGE + 124, y + 4, f"{name} {_ft(ft)}", size=11, fill=MODEL))

    notes = [
        "Vertical: to scale, in stick feet (the lake staff gauge). Horizontal: representative only; "
        "the left/right placement of the spillways is schematic.",
        f"Stop logs: laser-level survey 2026-11-25. Winter normal = {sl.winter_count} log (shown solid), "
        f"summer normal = {sl.summer_count} logs (added logs dashed). Logs ~{_inches(sl.log_height_ft)}; "
        "main base boards 3½″.",
        "EAP lines are the EAP's Water Level / Action Table (KI08-0195, rev. 2026-11) as written. Purple ticks: the "
        "model's overtopping levels (the stick-to-dam datum is not surveyed).",
        "Opening tops are estimates: the main's (the bridge underside) is inferred from its rating; the aux's "
        "depends on which sill its 2.5 ft was measured from.",
    ]
    for i, n in enumerate(notes):
        body.append(_text(LEFT_GAUGE - 40, bottom + 76 + i * 18, n, size=12, fill=MUTED))

    defs = f"""<defs>
  <pattern id="hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
    <rect width="8" height="8" fill="{CONCRETE}"/><line x1="0" y1="0" x2="0" y2="8" stroke="{CONCRETE_EDGE}" stroke-width="0.8"/>
  </pattern>
  <pattern id="mortar" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
    <rect width="7" height="7" fill="{BOARD_DARK}"/><line x1="0" y1="0" x2="0" y2="7" stroke="{BOARD_EDGE}" stroke-width="0.9"/>
  </pattern>
</defs>"""
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH:.0f}" height="{height:.0f}" '
            f'viewBox="0 0 {WIDTH:.0f} {height:.0f}" font-family="-apple-system, Helvetica, Arial, sans-serif">')
    title = [f'<rect width="{WIDTH:.0f}" height="{height:.0f}" fill="{PAPER}"/>',
             _text(LEFT_GAUGE - 40, 34, "Crystal Lake Dam · spillways and stop logs, front elevation",
                   size=20, weight="700"),
             _text(LEFT_GAUGE - 40, 56, "Heights in stick feet. Winter setup (1 log) solid; summer logs dashed.",
                   size=13, fill=MUTED)]
    return "\n".join([head, defs, *title, *_gauge(), *body, "</svg>"]) + "\n"
