import os
import time
import json
import requests
from fastapi import FastAPI, HTTPException, Query, Header, Depends
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Nexastream / MovieBox Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_TOKEN = os.getenv("API_TOKEN", "")

def verify_token(authorization: str = Header(default="")):
    if API_TOKEN:
        token = authorization.replace("Bearer ", "").strip()
        if token != API_TOKEN:
            raise HTTPException(status_code=401, detail="Unauthorized")
    return True

UPSTREAM_BASE = "https://themoviebox.xyz"
H5_API_BASE = "https://themoviebox.xyz/wefeed-h5api-bff"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": f"{UPSTREAM_BASE}/",
    "X-Request-Lang": "en",
}

session = requests.Session()
session.headers.update(DEFAULT_HEADERS)

_guest_token_cache = {"token": None, "expires": 0}

def get_guest_token() -> str:
    now = time.time()
    if _guest_token_cache["token"] and _guest_token_cache["expires"] > now:
        return _guest_token_cache["token"]
    try:
        resp = session.get(
            f"{H5_API_BASE}/subject/trending",
            params={"page": 1, "perPage": 1, "host": "themoviebox.xyz"},
            timeout=10,
        )
        x_user = resp.headers.get("x-user")
        if x_user:
            token = json.loads(x_user).get("token")
            if token:
                _guest_token_cache["token"] = token
                _guest_token_cache["expires"] = now + 300
                return token
        token = session.cookies.get("token")
        if token:
            _guest_token_cache["token"] = token
            _guest_token_cache["expires"] = now + 300
            return token
    except Exception:
        pass
    return ""

def is_family_friendly(item: dict) -> bool:
    genre_str = (item.get("genre") or "").casefold()
    title_str = (item.get("title") or "").casefold()
    bad_keywords = [
        "adult", "erotica", "savita", "porn", "sex", "orgasm",
        "hentai", "uncensored", "ecchi", "seinen", "mature",
        "melty", "yamitsuki", "kowaremono", "mesudachi", "torokase",
        "inyouchuu", "shiranai", "nagachichi", "kaede", "suzu", "shoku",
        "brickleberry", "kengan ashura", "edition"
    ]
    if any(b in title_str or b in genre_str for b in bad_keywords):
        return False
    if item.get("isPornographic") or item.get("webHighRisk"):
        return False
    return True

@app.get("/")
def root():
    return {"status": "ok", "service": "movie-box API"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/v1/home")
def get_home(_ = Depends(verify_token)):
    try:
        res = session.get(f"{H5_API_BASE}/home", params={"host": "themoviebox.xyz"}, timeout=15)
        if res.status_code == 200:
            return res.json().get("data", {"banners": [], "sections": []})
    except Exception:
        pass
    return {"banners": [], "sections": []}

def _browse_title(raw: dict) -> dict:
    cover = raw.get("cover") or {}
    stills = raw.get("stills") or {}
    return {
        "id": str(raw.get("subjectId") or ""),
        "detail_path": raw.get("detailPath") or "",
        "kind": {1: "movie", 2: "series", 7: "short_series"}.get(raw.get("subjectType"), "other"),
        "title": raw.get("title") or "",
        "description": raw.get("description") or "",
        "release_date": raw.get("releaseDate") or None,
        "duration_seconds": raw.get("duration") or None,
        "genres": [value.strip() for value in (raw.get("genre") or "").split(",") if value.strip()],
        "poster_url": cover.get("url"),
        "backdrop_url": stills.get("url"),
        "country": raw.get("countryName") or None,
        "rating": raw.get("imdbRatingValue") or None,
        "has_resource": bool(raw.get("hasResource")),
    }

@app.get("/v1/browse")
def get_browse(page: int = Query(default=1, ge=1), kind: str | None = None, tab: str | None = None, _ = Depends(verify_token)):
    if kind not in (None, "movie", "series", "short_series", "other"):
        raise HTTPException(status_code=422, detail="Invalid kind")
    if tab not in (None, "home", "movies", "midnight"):
        raise HTTPException(status_code=422, detail="Invalid tab")

    try:
        if kind == "short_series":
            response = session.post(
                f"{H5_API_BASE}/subject/filter",
                json={"channelId": 7, "page": page, "perPage": 18, "sort": "ForYou"},
                timeout=15,
            )
        else:
            selected_tab = tab or ("movies" if kind == "movie" else "home")
            params = {"page": page, "perPage": 18}
            tab_id = {"movies": 2, "midnight": 9}.get(selected_tab)
            if tab_id is not None:
                params["tabId"] = tab_id
            response = session.get(f"{H5_API_BASE}/subject/trending", params=params, timeout=15)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Provider browse request failed") from exc

    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="Provider browse request failed")
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Provider returned invalid JSON") from exc
    if payload.get("code") != 0:
        raise HTTPException(status_code=502, detail="Provider rejected the browse request")

    data = payload.get("data") or {}
    raw_items = data.get("items") if kind == "short_series" else data.get("subjectList")
    items = [_browse_title(item) for item in raw_items or []]
    if kind:
        items = [item for item in items if item["kind"] == kind]
    pager = data.get("pager") or {}
    next_page = None
    if pager.get("hasMore"):
        try:
            next_page = int(pager.get("nextPage"))
        except (TypeError, ValueError):
            pass
    return {
        "items": items,
        "page": page,
        "next_page": next_page,
        "has_more": bool(pager.get("hasMore")),
    }

@app.get("/v1/catalog/{name}")
def get_catalog(name: str, page: int = 1, genre: str = Query(default=""), _ = Depends(verify_token)):
    try:
        canonical = name.lower().strip()
        token = get_guest_token()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        sections = []
        all_items = []
        seen_ids = set()

        if canonical == "kids":
            row_queries = [
                ("Cartoon Movies", "cartoon movie"),
                ("Cartoon Series", "cartoon series"),
                ("Pixar Studio", "pixar"),
                ("DreamWorks Studio", "dreamworks"),
                ("Illumination Studio", "illumination"),
                ("Kung Fu Panda", "kung fu panda"),
                ("Cars", "cars"),
                ("Frozen", "frozen"),
                ("Minions", "minions"),
            ]
            for title, query in row_queries:
                res = session.post(
                    f"{H5_API_BASE}/subject/search",
                    json={"keyword": query, "page": page, "perPage": 12, "subjectType": 0},
                    headers=headers,
                    timeout=15,
                )
                sec_items = []
                if res.status_code == 200:
                    data = res.json().get("data", {})
                    for x in data.get("items", []):
                        if is_family_friendly(x):
                            sec_items.append(x)
                            sid = x.get("subjectId")
                            if sid not in seen_ids:
                                seen_ids.add(sid)
                                all_items.append(x)
                if sec_items:
                    sections.append({"title": title, "items": sec_items})

        elif canonical == "anime":
            row_queries = [
                ("Anime Movies", "anime movie"),
                ("Japanese Anime", "japanese anime"),
                ("Western Anime", "western animation"),
                ("Dragon Ball", "dragon ball"),
                ("Naruto", "naruto"),
                ("One Piece", "one piece"),
            ]
            for title, query in row_queries:
                res = session.post(
                    f"{H5_API_BASE}/subject/search",
                    json={"keyword": query, "page": page, "perPage": 12, "subjectType": 0},
                    headers=headers,
                    timeout=15,
                )
                sec_items = []
                if res.status_code == 200:
                    data = res.json().get("data", {})
                    for x in data.get("items", []):
                        if is_family_friendly(x) and "anime" in (x.get("genre") or "").casefold():
                            sec_items.append(x)
                            sid = x.get("subjectId")
                            if sid not in seen_ids:
                                seen_ids.add(sid)
                                all_items.append(x)
                if sec_items:
                    sections.append({"title": title, "items": sec_items})
        else:
            keyword = canonical if canonical != "animation" else "animation"
            res = session.post(
                f"{H5_API_BASE}/subject/search",
                json={"keyword": keyword, "page": page, "perPage": 28, "subjectType": 0},
                headers=headers,
                timeout=15,
            )
            if res.status_code == 200:
                data = res.json().get("data", {})
                all_items = [x for x in data.get("items", []) if is_family_friendly(x)]
                sections = [{"title": name.capitalize(), "items": all_items}]

        return {
            "items": all_items,
            "sections": sections,
            "next_page": page + 1 if len(all_items) >= 10 else None
        }
    except Exception:
        pass
    return {"items": [], "sections": [], "next_page": None}

@app.get("/v1/search")
def get_search(q: str = Query(...), page: int = 1, _ = Depends(verify_token)):
    try:
        token = get_guest_token()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        res = session.post(
            f"{H5_API_BASE}/subject/search",
            json={"keyword": q, "page": page, "perPage": 28, "subjectType": 0},
            headers=headers,
            timeout=15,
        )
        if res.status_code == 200:
            data = res.json().get("data", {})
            items = [x for x in data.get("items", []) if is_family_friendly(x)]
            return {"items": items, "next_page": data.get("next_page")}
    except Exception:
        pass
    return {"items": [], "next_page": None}

@app.get("/v1/search/suggestions")
def get_suggestions(q: str = Query(...), _ = Depends(verify_token)):
    try:
        token = get_guest_token()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        res = session.post(
            f"{H5_API_BASE}/subject/search-suggest",
            json={"keyword": q, "perPage": 10},
            headers=headers,
            timeout=15,
        )
        if res.status_code == 200:
            items = res.json().get("data", {}).get("items", [])
            return [x["word"] for x in items if x.get("word")]
    except Exception:
        pass
    return []

@app.get("/v1/search/popular")
def get_popular(_ = Depends(verify_token)):
    try:
        res = session.get(
            f"{H5_API_BASE}/subject/everyone-search",
            params={"host": "themoviebox.xyz"},
            timeout=15,
        )
        if res.status_code == 200:
            items = res.json().get("data", {}).get("everyoneSearch", [])
            return [x["title"] for x in items if x.get("title")]
    except Exception:
        pass
    return []

@app.get("/v1/titles/{path:path}/recommendations")
def get_recommendations(path: str, page: int = 1, _ = Depends(verify_token)):
    try:
        headers = {"Referer": f"{UPSTREAM_BASE}/movies/{path}"}
        detail_res = session.get(
            f"{H5_API_BASE}/detail",
            params={"detailPath": path, "host": "themoviebox.xyz"},
            headers=headers,
            timeout=15,
        )
        if detail_res.status_code == 200:
            subject_id = detail_res.json().get("data", {}).get("subject", {}).get("subjectId")
            if subject_id:
                rec_res = session.get(
                    f"{H5_API_BASE}/subject/detail-rec",
                    params={"subjectId": subject_id, "page": page, "perPage": 12, "host": "themoviebox.xyz"},
                    headers=headers,
                    timeout=15,
                )
                if rec_res.status_code == 200:
                    items = [x for x in rec_res.json().get("data", {}).get("items", []) if is_family_friendly(x)]
                    return {"items": items, "next_page": page + 1 if len(items) == 12 else None}
    except Exception:
        pass
    return {"items": [], "next_page": None}

@app.get("/v1/titles/{path:path}/playback")
def get_playback(path: str, season: int = 0, episode: int = 0, _ = Depends(verify_token)):
    try:
        headers = {"Referer": f"{UPSTREAM_BASE}/movies/{path}"}
        detail_res = session.get(
            f"{H5_API_BASE}/detail",
            params={"detailPath": path, "host": "themoviebox.xyz"},
            headers=headers,
            timeout=15,
        )
        subject_id = None
        if detail_res.status_code == 200:
            subject_data = detail_res.json().get("data", {})
            subject_id = subject_data.get("subject", {}).get("subjectId")

        play_res = session.get(
            f"{H5_API_BASE}/subject/play",
            params={
                "subjectId": subject_id or "",
                "se": season,
                "ep": episode,
                "detailPath": path,
                "streamSignType": 1,
                "supportCodecs[h264]": 1,
            },
            headers=headers,
            timeout=15,
        )
        if play_res.status_code == 200:
            return play_res.json().get("data", {"streams": [], "vip_locked": False, "limited": False})
        raise HTTPException(status_code=play_res.status_code, detail="Playback unavailable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/v1/titles/{path:path}/captions")
def get_captions(path: str, stream_id: str = "", season: int = 0, episode: int = 0, _ = Depends(verify_token)):
    try:
        headers = {"Referer": f"{UPSTREAM_BASE}/movies/{path}"}
        detail_res = session.get(
            f"{H5_API_BASE}/detail",
            params={"detailPath": path, "host": "themoviebox.xyz"},
            headers=headers,
            timeout=15,
        )
        subject_id = ""
        if detail_res.status_code == 200:
            subject_id = detail_res.json().get("data", {}).get("subject", {}).get("subjectId", "")

        cap_res = session.get(
            f"{H5_API_BASE}/subject/caption",
            params={
                "format": "MP4",
                "id": stream_id,
                "subjectId": subject_id,
                "detailPath": path,
            },
            headers=headers,
            timeout=15,
        )
        if cap_res.status_code == 200:
            captions_raw = cap_res.json().get("data", {}).get("captions", [])
            formatted = []
            for c in captions_raw:
                lang = c.get("lan", "en").replace("in_id", "id")
                formatted.append({
                    "label": c.get("lanName", lang),
                    "language": lang,
                    "url": c.get("url"),
                    "request_headers": {"Referer": UPSTREAM_BASE, "User-Agent": DEFAULT_HEADERS["User-Agent"]}
                })
            return formatted
    except Exception:
        pass
    return []

@app.get("/v1/titles/{path:path}")
def get_title_detail(path: str, _ = Depends(verify_token)):
    try:
        headers = {"Referer": f"{UPSTREAM_BASE}/movies/{path}"}
        res = session.get(
            f"{H5_API_BASE}/detail",
            params={"detailPath": path, "host": "themoviebox.xyz"},
            headers=headers,
            timeout=15,
        )
        if res.status_code == 200:
            payload = res.json()
            data = payload.get("data")
            if data and ("subject" in data or "title" in data):
                return data
            if payload.get("code") == 0:
                return data or {}
            raise HTTPException(status_code=404, detail=f"Upstream returned code {payload.get('code')}: {payload.get('msg')}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=404, detail="Title not found")
