#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pillow"]
# ///
"""Render the profile README hero SVGs (dark + light) from profile.md.

    uv run scripts/render_profile.py [profile.md]

profile.md format:
  - YAML-ish frontmatter: avatar, crop, cell, colors, out_dark, out_light
  - `# user@host`  -> panel header line
  - `## Title`     -> section rule
  - `- Key: Value` -> dot-leader stat line
  - `{uptime since YYYY-MM}` in a value expands to "N years, M months"
    computed at render time.

The avatar is resampled to its native pixel grid (crop / cell / colors)
and embedded as crisp pixel art; stats text is pinned per-segment with
textLength so alignment is identical across browsers and fonts.
"""

import ast
import base64
import io
import re
import sys
from datetime import date
from pathlib import Path

from PIL import Image

STAT_W = 44  # stats panel width in character cells
CELL_PX = 8  # svg px per art pixel

THEMES = {
    "dark": {
        "bg": "#0d1117",
        "border": "#30363d",
        "text": "#c9d1d9",
        "dots": "#484f58",
        "key": "#2dd4bf",
        "head": "#f0883e",
        "rule": "#3d444d",
        "at": "#8b949e",
    },
    "light": {
        "bg": "#ffffff",
        "border": "#d0d7de",
        "text": "#24292f",
        "dots": "#c4ccd4",
        "key": "#007c70",
        "head": "#c8462e",
        "rule": "#d0d7de",
        "at": "#6e7781",
    },
}

FONT = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"


def expand_uptime(value):
    def repl(m):
        y, mo = int(m.group(1)), int(m.group(2))
        today = date.today()
        months = (today.year - y) * 12 + (today.month - mo)
        years, rem = divmod(months, 12)
        ytxt = f"{years} year" + ("s" if years != 1 else "")
        if rem == 0:
            return ytxt
        return f"{ytxt}, {rem} month" + ("s" if rem != 1 else "")

    return re.sub(r"\{uptime since (\d{4})-(\d{2})\}", repl, value)


def parse(path):
    text = Path(path).read_text()
    meta = {}
    body = text
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        for line in fm.strip().splitlines():
            k, v = line.split(":", 1)
            v = v.strip()
            try:
                meta[k.strip()] = ast.literal_eval(v)
            except (ValueError, SyntaxError):
                meta[k.strip()] = v

    lines = []  # each: list of (text, colorclass) segments, or [] for blank
    for raw in body.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("## "):
            title = s[3:].strip()
            head = f"- {title} "
            lines.append([])
            lines.append(
                [
                    ("- ", "dots"),
                    (f"{title} ", "head"),
                    ("─" * (STAT_W - len(head)), "rule"),
                ]
            )
        elif s.startswith("# "):
            title = s[2:].strip()
            if "@" in title:
                user, host = title.split("@", 1)
                segs = [(user, "key"), ("@", "at"), (f"{host} ", "head")]
            else:
                segs = [(f"{title} ", "head")]
            pad = sum(len(t) for t, _ in segs)
            lines.append(segs + [("─" * (STAT_W - pad), "rule")])
        elif s.startswith("- ") and ": " in s:
            key, val = s[2:].split(": ", 1)
            val = expand_uptime(val.strip())
            base = f". {key}:"
            ndots = STAT_W - len(base) - len(val) - 2
            if ndots < 1:
                sys.exit(f"line too wide (> {STAT_W} cols): {key}: {val}")
            lines.append(
                [
                    (". ", "dots"),
                    (f"{key}:", "key"),
                    (f" {'.' * ndots} ", "dots"),
                    (val, "text"),
                ]
            )
        else:
            sys.exit(f"unrecognized line: {raw}")
    return meta, lines


def pixel_art(meta, root):
    img = Image.open(root / meta["avatar"]).convert("RGB")
    img = img.crop(tuple(meta["crop"]))
    cell = meta["cell"]
    gw, gh = round(img.width / cell), round(img.height / cell)
    small = img.resize((gw, gh), Image.BOX)
    small = small.quantize(colors=meta["colors"], method=Image.MEDIANCUT, dither=Image.NONE)
    big = small.convert("RGB").resize((gw * CELL_PX, gh * CELL_PX), Image.NEAREST)
    buf = io.BytesIO()
    big.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode(), gw * CELL_PX, gh * CELL_PX


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(theme, lines, art_b64, art_w, art_h):
    t = THEMES[theme]
    pad = 24
    fs = 17
    u = 10.2  # forced char cell width via textLength
    lh = 23
    stat_x = pad + art_w + 28
    text_h = 14 + (len(lines) - 1) * lh + 6
    H = max(art_h, text_h) + pad * 2
    W = stat_x + round(STAT_W * u) + pad
    art_y = pad + (H - pad * 2 - art_h) // 2
    stat_top = pad + 14 + (H - pad * 2 - text_h) // 2

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="12" fill="{t["bg"]}" stroke="{t["border"]}"/>',
        f'<image x="{pad}" y="{art_y}" width="{art_w}" height="{art_h}" '
        f'style="image-rendering:pixelated" href="data:image/png;base64,{art_b64}"/>',
        f'<g font-family="{FONT}" font-size="{fs}" xml:space="preserve">',
    ]
    y = stat_top
    for segs in lines:
        if segs:
            spans = []
            col = 0
            for txt, cls in segs:
                x = stat_x + col * u
                wgt = ' font-weight="600"' if cls in ("key", "head") else ""
                spans.append(
                    f'<tspan x="{x:.1f}" fill="{t[cls]}"{wgt} '
                    f'textLength="{len(txt) * u:.1f}" '
                    f'lengthAdjust="spacingAndGlyphs">{esc(txt)}</tspan>'
                )
                col += len(txt)
            parts.append(f'<text y="{y}">{"".join(spans)}</text>')
        y += lh
    parts.append("</g></svg>")
    return "\n".join(parts)


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "profile.md")
    root = src.resolve().parent
    meta, lines = parse(src)
    art_b64, art_w, art_h = pixel_art(meta, root)
    for theme, key in (("dark", "out_dark"), ("light", "out_light")):
        out = root / meta[key]
        out.write_text(render(theme, lines, art_b64, art_w, art_h))
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
