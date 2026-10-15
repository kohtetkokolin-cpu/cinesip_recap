"""Movie/Drama Finder — trend discovery + AI suggestions.

Purpose: help the user decide WHAT to recap (what's trending, AI picks for
a Myanmar recap audience) and show legal info links (TMDB page, trailer,
legal streaming availability).

Deliberately NOT included: downloading movies/dramas. The user sources the
video file themselves (outside the app) and uploads it into the wizard.
This module never touches pirate sites and never downloads copyrighted
video.
"""
import json
import urllib.parse
import urllib.request

from .. import config

TMDB_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w500"
BACKDROP_BASE = "https://image.tmdb.org/t/p/w780"


def _key() -> str:
    k = (config.TMDB_API_KEY or "").strip()
    if not k:
        raise RuntimeError("TMDB_API_KEY မရှိသေးပါ — .env မှာ ထည့်ပါ (free: themoviedb.org)")
    return k


def _get(path: str, params: dict | None = None) -> dict:
    qs = urllib.parse.urlencode(
        {"api_key": _key(), "language": "en-US", **(params or {})})
    req = urllib.request.Request(
        f"{TMDB_BASE}{path}?{qs}",
        headers={"User-Agent": "cinesip-recap/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def _card(item: dict, media_type: str) -> dict:
    title = item.get("title") or item.get("name") or "?"
    date = item.get("release_date") or item.get("first_air_date") or ""
    poster = item.get("poster_path")
    return {
        "tmdb_id": item.get("id"),
        "media_type": media_type,
        "title": title,
        "year": date[:4] if date else "",
        "rating": round(float(item.get("vote_average") or 0), 1),
        "overview": (item.get("overview") or "")[:300],
        "poster": f"{IMG_BASE}{poster}" if poster else "",
    }


def trending(media_type: str = "movie", window: str = "week") -> list:
    """TMDB trending. media_type: movie | tv | all. window: day | week."""
    if media_type not in ("movie", "tv", "all"):
        media_type = "movie"
    if window not in ("day", "week"):
        window = "week"
    data = _get(f"/trending/{media_type}/{window}", {"page": 1})
    out = []
    for it in data.get("results", [])[:20]:
        mt = it.get("media_type") or media_type
        if mt not in ("movie", "tv"):
            continue
        out.append(_card(it, mt))
    return out


def search(query: str) -> list:
    """TMDB multi-search (movies + TV)."""
    q = (query or "").strip()
    if not q:
        return []
    data = _get("/search/multi", {"query": q, "page": 1,
                                  "include_adult": "false"})
    out = []
    for it in data.get("results", [])[:20]:
        mt = it.get("media_type")
        if mt not in ("movie", "tv"):
            continue
        out.append(_card(it, mt))
    return out


def details(tmdb_id: int, media_type: str = "movie") -> dict:
    """Full details + trailer YouTube key + legal watch providers."""
    if media_type not in ("movie", "tv"):
        media_type = "movie"
    data = _get(f"/{media_type}/{tmdb_id}",
                {"append_to_response": "videos,watch/providers"})
    # trailer: prefer official YouTube trailer
    trailer_key = ""
    for v in (data.get("videos") or {}).get("results", []):
        if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser"):
            trailer_key = v.get("key") or ""
            if v.get("type") == "Trailer" and v.get("official"):
                break
    # watch providers — prefer MM, fall back to US
    prov = ((data.get("watch/providers") or {}).get("results") or {})
    region = prov.get("MM") or prov.get("US") or {}
    providers = {}
    for k in ("flatrate", "ads", "free", "rent", "buy"):
        items = region.get(k) or []
        providers[k] = [{"name": p.get("provider_name"),
                         "logo": f"{IMG_BASE}{p['logo_path']}" if p.get("logo_path") else ""}
                        for p in items[:8]]
    poster = data.get("poster_path")
    backdrop = data.get("backdrop_path")
    date = data.get("release_date") or data.get("first_air_date") or ""
    return {
        "tmdb_id": data.get("id"),
        "media_type": media_type,
        "title": data.get("title") or data.get("name") or "?",
        "year": date[:4] if date else "",
        "rating": round(float(data.get("vote_average") or 0), 1),
        "overview": data.get("overview") or "",
        "genres": [g.get("name") for g in data.get("genres", [])],
        "poster": f"{IMG_BASE}{poster}" if poster else "",
        "backdrop": f"{BACKDROP_BASE}{backdrop}" if backdrop else "",
        "trailer_key": trailer_key,
        "trailer_url": f"https://www.youtube.com/watch?v={trailer_key}" if trailer_key else "",
        "providers": providers,
        "provider_region": "MM" if prov.get("MM") else "US",
        "tmdb_url": f"https://www.themoviedb.org/{media_type}/{data.get('id')}",
    }


# ---------------------------------------------------------------- AI picks
SUGGEST_PROMPT = """You are a YouTube strategist for a Myanmar movie-recap channel.
Suggest {n} movies or dramas that are trending/popular RIGHT NOW and would
get views as Burmese-language recap videos. Mix: Hollywood movies, Chinese
dramas, Korean dramas, anime films — whatever is hot. For each give:
- title (original English title)
- kind: movie | tv
- year (best guess)
- why_hot: one short sentence why it's trending now
- hook_angle: one short Burmese hook idea for the recap opening (Burmese text)

Reply as a JSON array only, no other text. Example:
[{{"title":"...","kind":"movie","year":2026,"why_hot":"...","hook_angle":"..."}}]
"""


def suggest(client, n: int = 8) -> list:
    """Gemini-powered recap picks, resolved to TMDB cards.

    Returns [{card fields...}, why_hot, hook_angle]. Uses ~1 Gemini call.
    """
    from . import script as _sc
    raw = client.generate(SUGGEST_PROMPT.format(n=n))
    items = _sc._extract_json(raw)
    if not isinstance(items, list):
        items = []
    picks = []
    for it in items[:n]:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        try:
            res = search(title)
        except Exception:
            res = []
        card = res[0] if res else {
            "tmdb_id": 0, "media_type": it.get("kind") or "movie",
            "title": title, "year": str(it.get("year") or ""),
            "rating": 0, "overview": "", "poster": ""}
        card["why_hot"] = it.get("why_hot") or ""
        card["hook_angle"] = it.get("hook_angle") or ""
        picks.append(card)
    return picks
