"""Cinesip Recap — overlay engine.

Single source of truth for WYSIWYG: the frontend preview editor and the
ffmpeg render both use THIS percentage-based model.

Coordinate model (all values are percentages 0-100 of video dimensions):
  blur:      [{"x","y","w","h","s"}]  x,y = top-left %, w,h = size %, s = blur sigma
  logo:      {"enabled","x","y","w","opacity"}  w = % of video WIDTH
  watermark: {"enabled","text","x","y","size","opacity","color"}  size = % of video HEIGHT
  sub:       {"cx","cy","size","maxw","color","text"}  cx,cy = center %, size = % of H
  video:     {"brightness","contrast","saturation"}

The preview editor positions divs with these exact percentages over the
<video> element; build_filter() converts the same percentages to ffmpeg
expressions using W/H so the rendered output matches the preview pixel-wise.
"""
import os
import re

SUB_COLORS = {
    "White": "FFFFFF", "Yellow": "FFFF00", "Green": "00FF00",
    "Cyan": "00FFFF", "Red": "FF0000", "Black": "000000",
}


def _pct(v, lo, hi, dflt):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return dflt


def _esc_drawtext(s: str) -> str:
    # escape for drawtext filter argument
    return (s.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%"))


def resolve_font(font_dir: str, name: str | None) -> str | None:
    """Return absolute fontfile path or None if not found."""
    if not name:
        return None
    p = os.path.join(font_dir, os.path.basename(name))
    return p if os.path.isfile(p) else None


def sanitize_overlays(raw: dict | None) -> dict:
    """Clamp/validate overlay JSON from the client. Returns a clean dict.
    Accepts both the Phase-1 schema (wm/eq, logo.path) and the Studio UI
    schema (watermark/video, logo.logo_path)."""
    d = raw if isinstance(raw, dict) else {}
    # --- schema aliases (Studio UI) ---
    if "watermark" in d and "wm" not in d:
        d["wm"] = d["watermark"]
    if "video" in d and "eq" not in d:
        d["eq"] = d["video"]
    if isinstance(d.get("logo"), dict) and "logo_path" in d["logo"] \
            and "path" not in d["logo"]:
        d["logo"]["path"] = d["logo"].pop("logo_path")
    blur = []
    for r in (d.get("blur") or [])[:6]:
        if not isinstance(r, dict):
            continue
        x = _pct(r.get("x"), 0, 98, 0)
        y = _pct(r.get("y"), 0, 98, 70)
        w = _pct(r.get("w"), 1, 100, 100)
        h = _pct(r.get("h"), 1, 100, 20)
        blur.append({"x": x, "y": y, "w": min(w, 100 - x), "h": min(h, 100 - y),
                     "s": _pct(r.get("s"), 1, 60, 25)})
    lg = d.get("logo") if isinstance(d.get("logo"), dict) else {}
    logo = {"enabled": bool(lg.get("enabled", False)),
            "x": _pct(lg.get("x"), 0, 100, 3), "y": _pct(lg.get("y"), 0, 100, 3),
            "w": _pct(lg.get("w"), 2, 60, 12), "opacity": _pct(lg.get("opacity"), 0, 1, 0.9)}
    wm = d.get("watermark") if isinstance(d.get("watermark"), dict) else {}
    watermark = {"enabled": bool(wm.get("enabled", False)),
                 "text": str(wm.get("text") or "Cinesip")[:60],
                 "x": _pct(wm.get("x"), 0, 100, 50), "y": _pct(wm.get("y"), 0, 100, 92),
                 "size": _pct(wm.get("size"), 1, 12, 4),
                 "opacity": _pct(wm.get("opacity"), 0, 1, 0.5),
                 "color": str(wm.get("color") or "white")}
    sb = d.get("sub") if isinstance(d.get("sub"), dict) else {}
    color = str(sb.get("color") or "Yellow")
    sub = {"cx": _pct(sb.get("cx"), 0, 100, 50), "cy": _pct(sb.get("cy"), 0, 100, 88),
           "size": _pct(sb.get("size"), 1.5, 14, 4.5), "maxw": _pct(sb.get("maxw"), 30, 100, 88),
           "color": color if color in SUB_COLORS else "Yellow",
           "font": os.path.basename(str(sb.get("font") or "Padauk.ttf"))[:60],
           "text": str(sb.get("text") or "ဒါက နမူနာ စာတန်းထိုးပါ")[:200]}
    vd = d.get("video") if isinstance(d.get("video"), dict) else {}
    video = {"brightness": _pct(vd.get("brightness"), -0.5, 0.5, 0),
             "contrast": _pct(vd.get("contrast"), 0.5, 1.5, 1.0),
             "saturation": _pct(vd.get("saturation"), 0, 2.0, 1.0)}
    fx = d.get("fairuse") if isinstance(d.get("fairuse"), dict) else {}
    fairuse = {"max_clip_sec": _pct(fx.get("max_clip_sec"), 2, 8, 5),
               "mirror": bool(fx.get("mirror", False)),
               "grade": str(fx.get("grade") or "none")}
    return {"blur": blur, "logo": logo, "watermark": watermark,
            "sub": sub, "video": video, "fairuse": fairuse}


_GRADES = {
    "warm": "colorbalance=rs=0.15:gs=0.05:bs=-0.15",
    "cool": "colorbalance=rs=-0.12:gs=0.02:bs=0.18",
    "cinematic": "eq=contrast=1.08:saturation=0.85,colorbalance=rs=0.08:bs=0.10",
    "vintage": "eq=contrast=0.92:saturation=0.70:brightness=0.04",
    "bw": "hue=s=0",
}


def build_filter(w: int, h: int, ov: dict, logo_path: str | None = None,
                 font_dir: str = "fonts", ass_path: str | None = None) -> str:
    """Build the full ffmpeg -filter_complex chain from sanitized overlays.

    w,h: source video dimensions. Percentages are baked to integer pixels
    here (we always know the source size at render time), so the chain
    uses no fragile expressions. The percentage model lives in the JSON —
    the preview editor uses the same percentages, which is what makes
    preview == render (WYSIWYG).
    """
    ov = sanitize_overlays(ov)
    parts: list[str] = []
    cur = "0:v"

    def px(pct: float, base: int) -> int:
        return int(round(base * pct / 100))

    # 1. base video adjustments (brightness/contrast/saturation)
    v = ov["video"]
    parts.append(f"[{cur}]eq=brightness={v['brightness']:.3f}:contrast={v['contrast']:.3f}"
                 f":saturation={v['saturation']:.3f}[vb]")
    cur = "vb"

    # 2. fair-use grade (optional look)
    grade = _GRADES.get(ov["fairuse"]["grade"])
    if grade:
        parts.append(f"[{cur}]{grade}[vg]")
        cur = "vg"
    if ov["fairuse"]["mirror"]:
        parts.append(f"[{cur}]hflip[vf]")
        cur = "vf"

    # 3. blur regions (crop -> gblur -> overlay back)
    for i, r in enumerate(ov["blur"]):
        bw, bh, bx, by = px(r["w"], w), px(r["h"], h), px(r["x"], w), px(r["y"], h)
        if bw < 2 or bh < 2:
            continue
        parts.append(f"[{cur}]crop=w={bw}:h={bh}:x={bx}:y={by},gblur=sigma={r['s']:.1f}[bl{i}]")
        parts.append(f"[{cur}][bl{i}]overlay=x={bx}:y={by}[vb{i}]")
        cur = f"vb{i}"

    # 4. logo image
    lg = ov["logo"]
    if lg["enabled"] and logo_path and os.path.isfile(logo_path):
        lp = logo_path.replace("\\", "/").replace(":", "\\:")
        lw = max(8, px(lg["w"], w))
        parts.append(f"movie='{lp}',scale=w={lw}:h=-1,format=rgba,"
                     f"colorchannelmixer=aa={lg['opacity']:.2f}[lg]")
        parts.append(f"[{cur}][lg]overlay=x={px(lg['x'], w)}:y={px(lg['y'], h)}[vl]")
        cur = "vl"

    # 5. watermark text
    wm = ov["watermark"]
    if wm["enabled"] and wm["text"].strip():
        ff = resolve_font(font_dir, "Padauk.ttf")
        font_opt = f":fontfile='{ff}'" if ff else ""
        parts.append(
            f"[{cur}]drawtext=text='{_esc_drawtext(wm['text'])}'"
            f":x={px(wm['x'], w)}:y={px(wm['y'], h)}"
            f":fontsize={max(8, px(wm['size'], h))}:fontcolor={wm['color']}@{wm['opacity']:.2f}"
            f"{font_opt}:box=0[vw]")
        cur = "vw"

    vf = ";".join(parts)
    # 6. burned subtitles via ASS (needs the .ass file pre-written)
    if ass_path:
        ap = ass_path.replace("\\", "/").replace(":", "\\:")
        vf += f";[{cur}]subtitles='{ap}':fontsdir='{font_dir}'[vout]"
    else:
        vf += f";[{cur}]null[vout]"
    return vf


def write_ass(ass_path: str, w: int, h: int, lines: list[tuple[float, float, str]],
              ov: dict, font_name: str = "Padauk.ttf") -> None:
    """Write an ASS subtitle file honoring the overlay sub model.

    lines: [(start_sec, end_sec, text)]. Position comes from ov['sub']
    (cx, cy center %), size from sub['size'] (% of H), color from sub['color'].
    """
    ov = sanitize_overlays(ov)
    sb = ov["sub"]
    fontsize = max(10, int(h * sb["size"] / 100))
    # wrap text to maxw %
    max_px = w * sb["maxw"] / 100
    approx_char_px = fontsize * 0.62  # Burmese glyphs are wide
    cpl = max(8, int(max_px / approx_char_px))

    def wrap(text: str) -> str:
        words, out, cur = text.split(), [], ""
        for wd in words:
            if len(cur) + len(wd) + 1 > cpl and cur:
                out.append(cur); cur = wd
            else:
                cur = (cur + " " + wd).strip()
        if cur:
            out.append(cur)
        return "\\N".join(out)

    def ts(sec: float) -> str:
        sec = max(0, sec)
        cs = int(sec * 100) % 100
        s = int(sec) % 60; m = int(sec // 60) % 60; hh = int(sec // 3600)
        return f"{hh}:{m:02d}:{s:02d}.{cs:02d}"

    x = int(w * sb["cx"] / 100)
    y = int(h * sb["cy"] / 100)
    color = SUB_COLORS.get(sb["color"], "FFFF00")
    # ASS color: &HAABBGGRR  (AA=00 opaque)
    ass_color = f"&H00{color[4:6]}{color[2:4]}{color[0:2]}"

    head = ("[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {w}\nPlayResY: {h}\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
            "SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
            "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Cinesip,{font_name},{fontsize},{ass_color},&H000019FF,&H80000000,"
            f"&H80000000,0,0,0,0,100,100,0,0,1,2,1,7,20,20,20,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n")
    body = []
    for st, en, tx in lines:
        # \pos places the block center exactly at (cx%, cy%) like the preview
        body.append(f"Dialogue: 0,{ts(st)},{ts(en)},Cinesip,,0,0,0,,"
                    f"{{\\pos({x},{y})\\an5}}{wrap(tx)}")
    with open(ass_path, "w", encoding="utf-8-sig") as f:
        f.write(head + "\n".join(body) + "\n")
