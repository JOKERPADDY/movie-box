import os
import time
import json
import re
import base64
import hashlib
import hmac
import requests
from fastapi import FastAPI, HTTPException, Query, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Nexastream / MovieBox Backend", version="1.1.0")

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
OFFICIAL_BASE_URL = "https://api.inmoviebox.com"
OFFICIAL_KEY_STR = "76iRl07s0xSN9jqmEWAt79EBJZulIQIsV64FZr2O"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": f"{UPSTREAM_BASE}/",
    "X-Request-Lang": "en",
}

session = requests.Session()
session.headers.update(DEFAULT_HEADERS)

_guest_token_cache = {"token": None, "expires": 0}
_visitor_token_cache = {"token": None, "expires": 0}

def md5_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.md5(data).hexdigest()

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

def get_official_signature(method: str, endpoint: str, query_params: dict, body_str: str, timestamp_str: str) -> tuple[str, str]:
    reversed_ts = timestamp_str[::-1]
    client_token = f"{timestamp_str},{md5_hex(reversed_ts)}"

    params_copy = dict(query_params)
    if "host" not in params_copy:
        params_copy["host"] = "api.inmoviebox.com"

    sorted_keys = sorted(params_copy.keys())
    query_parts = []
    for k in sorted_keys:
        query_parts.append(f"{k}={params_copy[k]}")
    sorted_query_str = "&".join(query_parts)

    canonical_path_query = endpoint + (f"?{sorted_query_str}" if sorted_query_str else "")

    body_length = ""
    body_hash = ""
    if body_str:
        body_bytes = body_str.encode("utf-8")
        body_length = str(len(body_bytes))
        sub_bytes = body_bytes[:102400]
        body_hash = md5_hex(sub_bytes)

    accept = "application/json"
    content_type = "application/json;charset=UTF-8"

    canonical_str = f"{method}\n{accept}\n{content_type}\n{body_length}\n{timestamp_str}\n{body_hash}\n{canonical_path_query}"

    try:
        key_bytes = base64.b64decode(OFFICIAL_KEY_STR)
    except Exception:
        key_bytes = OFFICIAL_KEY_STR.encode("utf-8")

    sig_digest = hmac.new(key_bytes, canonical_str.encode("utf-8"), hashlib.md5).digest()
    base64_sig = base64.b64encode(sig_digest).decode("utf-8")
    signature_header = f"{timestamp_str}|2|{base64_sig}"

    return client_token, signature_header

def bootstrap_visitor_token() -> str:
    res = make_official_request("POST", "/wefeed-mobile-bff/user-api/visitor-login", body_str="{}")
    if res and isinstance(res.get("data"), dict):
        token = res["data"].get("token")
        if token:
            _visitor_token_cache["token"] = token
            _visitor_token_cache["expires"] = time.time() + 3600
            return token
    return ""

def make_official_request(method: str, endpoint: str, query_params: dict = None, body_str: str = "") -> dict | None:
    global _visitor_token_cache
    if query_params is None:
        query_params = {}

    timestamp_str = str(int(time.time() * 1000))
    client_token, signature_header = get_official_signature(method, endpoint, query_params, body_str, timestamp_str)

    now = time.time()
    active_token = _visitor_token_cache.get("token")
    if not active_token or _visitor_token_cache.get("expires", 0) <= now:
        if endpoint != "/wefeed-mobile-bff/user-api/visitor-login":
            active_token = bootstrap_visitor_token()

    headers = {
        "User-Agent": "MovieBox/4.0.02 (Android 14; Pixel 6)",
        "Accept": "application/json",
        "Accept-Language": "en-IN,hi-IN;q=0.9,en;q=0.8",
        "Content-Type": "application/json;charset=UTF-8",
        "X-Sign-Version": "2.0",
        "appid": "4U01pxRu278GqCZKY9",
        "region": "IN",
        "lang": "en",
        "os": "android",
        "X-Timestamp": timestamp_str,
        "Referer": "https://api.inmoviebox.com/",
        "X-Client-Token": client_token,
        "x-tr-signature": signature_header,
        "X-Play-Mode": "2",
        "X-Client-Info": json.dumps({
            "package_name": "com.community.oneroom",
            "version_name": "4.0.02",
            "version_code": 50020126,
            "os": "android",
            "os_version": "14",
            "install_ch": "ps",
            "device_id": "a1b2c3d4e5f67890",
            "install_store": "ps",
            "gaid": "",
            "brand": "Google",
            "model": "Pixel 6",
            "system_language": "en",
            "net": "wifi",
            "region": "IN",
            "timezone": "Asia/Kolkata",
            "sp_code": "404"
        }, separators=(',', ':'))
    }

    if active_token:
        headers["Authorization"] = f"Bearer {active_token}"
        headers["X-Client-Status"] = "0"
    else:
        headers["X-Client-Status"] = "1"

    url_params = dict(query_params)
    if "host" not in url_params:
        url_params["host"] = "api.inmoviebox.com"

    url = f"{OFFICIAL_BASE_URL}{endpoint}"

    try:
        if method.upper() == "GET":
            resp = session.get(url, params=url_params, headers=headers, timeout=15)
        else:
            resp = session.post(url, params=url_params, data=body_str, headers=headers, timeout=15)

        x_user = resp.headers.get("x-user") or resp.headers.get("X-User")
        if x_user:
            try:
                u_data = json.loads(x_user)
                t = u_data.get("token")
                if t:
                    _visitor_token_cache["token"] = t
                    _visitor_token_cache["expires"] = now + 3600
            except Exception:
                pass

        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Official request error ({endpoint}): {e}")
    return None

def decode_sign_cookie_url(cookie_str: str) -> str | None:
    if not cookie_str or "urlprefix=" not in cookie_str:
        return None
    try:
        prefix_start = cookie_str.find("urlprefix=") + len("urlprefix=")
        prefix_end = cookie_str.find(":", prefix_start)
        if prefix_end == -1:
            prefix_end = cookie_str.find(";", prefix_start)
        if prefix_end == -1:
            prefix_end = len(cookie_str)
        base64_str = cookie_str[prefix_start:prefix_end]
        padding = (4 - len(base64_str) % 4) % 4
        base64_str += "=" * padding
        decoded_bytes = base64.b64decode(base64_str)
        decoded_path = decoded_bytes.decode("utf-8")
        if decoded_path.endswith("/"):
            return f"{decoded_path}index.m3u8"
        else:
            return f"{decoded_path}/index.m3u8"
    except Exception:
        return None

def is_dummy_url(url: str) -> bool:
    if not url:
        return True
    u_lower = url.lower()
    return any(dummy in u_lower for dummy in ["macdn.aoneroom.com", "please_update", "update_app", "b164fbfb43"])

def resolve_subject_id_from_path(path: str) -> str | None:
    if not path:
        return None
    if path.isdigit():
        return path
    try:
        headers = {"Referer": f"{UPSTREAM_BASE}/movies/{path}"}
        detail_res = session.get(
            f"{H5_API_BASE}/detail",
            params={"detailPath": path, "host": "themoviebox.xyz"},
            headers=headers,
            timeout=10,
        )
        if detail_res.status_code == 200:
            data = detail_res.json().get("data") or {}
            sid = (data.get("subject") or {}).get("subjectId") or data.get("subjectId")
            if sid:
                return str(sid)
    except Exception:
        pass

    clean_query = re.sub(r"-\d{4}$", "", path).replace("-", " ").strip()
    try:
        search_res = make_official_request(
            "POST",
            "/wefeed-mobile-bff/subject-api/search",
            {},
            json.dumps({"keyword": clean_query, "page": 1, "perPage": 5, "subjectType": 0})
        )
        if search_res and search_res.get("data"):
            items = search_res["data"].get("items") or []
            if items:
                return str(items[0].get("subjectId"))
    except Exception:
        pass
    return None

def fetch_captions(subject_id: str, resource_id: str = "", season: int = 1, episode: int = 1, is_movie: bool = True) -> list[dict]:
    params = {"subjectId": subject_id}
    if resource_id:
        params["resourceId"] = resource_id
    if not is_movie:
        params["se"] = str(season)
        params["ep"] = str(episode)

    res = make_official_request("GET", "/wefeed-mobile-bff/subject-api/get-ext-captions", params)
    sub_data = (res.get("data") or {}) if res else {}
    ext_list = sub_data.get("extCaptions") or sub_data.get("list") or []

    captions = []
    seen_urls = set()
    for sub in ext_list:
        url = sub.get("url") or ""
        if url and url not in seen_urls:
            seen_urls.add(url)
            lan = sub.get("lan") or "en"
            captions.append({
                "label": sub.get("lanName") or lan,
                "language": lan,
                "url": url,
                "request_headers": {"User-Agent": DEFAULT_HEADERS["User-Agent"]}
            })
    return captions

def resolve_streams(subject_id: str, season: int = 1, episode: int = 1, resource_id: str | None = None) -> dict:
    detail_res = make_official_request("GET", "/wefeed-mobile-bff/subject-api/get", {"subjectId": subject_id})
    detail_data = (detail_res.get("data") or {}) if detail_res else {}

    subject_type = int(detail_data.get("subjectType") or detail_data.get("type") or 1)
    has_tv_indicators = (
        (detail_data.get("seNum") and int(detail_data.get("seNum") or 0) > 0) or
        detail_data.get("episodeCount") is not None or
        detail_data.get("seasonCount") is not None or
        bool(detail_data.get("seasons"))
    )
    is_movie = (subject_type == 1) and not has_tv_indicators

    detectors = detail_data.get("resourceDetectors") or []
    dubs = detail_data.get("dubs") or []

    orig_dub = next((d for d in dubs if d.get("original") is True or "orig" in str(d.get("lanName", "")).lower()), None)
    main_subject_id = orig_dub.get("subjectId") if orig_dub else subject_id

    final_subject_id = subject_id
    final_resource_id = resource_id

    if resource_id:
        if resource_id.startswith("dub_"):
            final_subject_id = resource_id.replace("dub_", "")
            final_resource_id = None
        elif resource_id.startswith("res_"):
            final_resource_id = resource_id.replace("res_", "")

    params = {
        "subjectId": final_subject_id,
        "host": "api6.aoneroom.com"
    }
    if not is_movie:
        params["se"] = str(season)
        params["ep"] = str(episode)
    if final_resource_id:
        params["resourceId"] = final_resource_id

    play_res = make_official_request("GET", "/wefeed-mobile-bff/subject-api/play-info/v2", params)
    play_data = (play_res.get("data") or {}) if play_res else {}

    raw_streams = play_data.get("streamList") or play_data.get("streams") or []
    sign_cookie = (play_res.get("signCookie") or play_data.get("signCookie") or "") if play_res else ""

    streams = []
    for s in raw_streams:
        u = s.get("url") or ""
        cookie_override = s.get("signCookie") or sign_cookie
        decoded_u = decode_sign_cookie_url(cookie_override) if (is_dummy_url(u) and cookie_override) else None

        real_url = decoded_u or u
        if not is_dummy_url(real_url):
            res_num = s.get("resolution") or s.get("resolutions") or 720
            try:
                res_num = int(re.sub(r"\D", "", str(res_num)) or "720")
            except Exception:
                res_num = 720

            streams.append({
                "id": str(s.get("id") or s.get("resourceId") or res_num),
                "url": real_url,
                "resolution": res_num,
                "quality": f"{res_num}p",
                "format": s.get("format") or "MP4",
                "codecName": s.get("codecName") or "h264",
                "size": s.get("size") or f"{res_num}p",
                "signCookie": cookie_override
            })

    if not streams:
        ugc_streams = []
        candidate_ids = [final_subject_id]
        if main_subject_id != final_subject_id and main_subject_id not in candidate_ids:
            candidate_ids.append(main_subject_id)

        target_ep = episode if not is_movie else 1
        found_in_collection = False
        ep_regex = re.compile(rf"(?i)(?:s0*{season}\s*)?(?:ep|episode|e|#|\s|^)0*{target_ep}(?:[^\d]|$)")

        for cand_id in candidate_ids:
            if found_in_collection:
                break
            ugc_first = make_official_request("GET", "/wefeed-mobile-bff/ugc/video/first-by-subject", {"subjectId": cand_id})
            ugc_first_data = (ugc_first.get("data") or {}) if ugc_first else {}
            ugc_vid_id = str(ugc_first_data.get("ugcVideoId") or "")
            belong_coll = ugc_first_data.get("belongToCollection") or {}
            coll_id = str(belong_coll.get("collectionId") or "")

            if coll_id:
                total_vids = int(belong_coll.get("videoNum") or 0)
                total_pages = max(1, ((total_vids - 1) // 50) + 1)
                primary_page = max(1, min(total_pages, ((target_ep - 1) // 50) + 1))

                pages_to_try = [primary_page]
                for p in [1, 2, 3, 4, 5, 6, 7, 8]:
                    if 1 <= p <= total_pages and p not in pages_to_try:
                        pages_to_try.append(p)

                target_item = None
                for p in pages_to_try:
                    coll_res = make_official_request("GET", "/wefeed-mobile-bff/ugc/collection/download-list", {"collectionId": coll_id, "page": str(p), "perPage": "50"})
                    items = (coll_res.get("data") or {}).get("items") or [] if coll_res else []

                    for item in items:
                        item_ep = item.get("episode") or item.get("ep") or item.get("episodeNumber") or item.get("order")
                        if item_ep is not None and int(str(item_ep) if str(item_ep).isdigit() else 0) == target_ep:
                            target_item = item
                            break
                        ep_title = str(item.get("epTitle") or "")
                        title = str(item.get("title") or "")
                        if ep_regex.search(ep_title) or ep_regex.search(title):
                            target_item = item
                            break
                    if not target_item and target_ep == 1 and p == 1 and items:
                        target_item = items[0]
                    if target_item:
                        break

                if target_item:
                    found_in_collection = True
                    resources = target_item.get("resources") or []
                    for r in resources:
                        r_url = r.get("url") or ""
                        if not is_dummy_url(r_url):
                            raw_res = r.get("resolutions") or r.get("resolution") or r.get("quality") or 720
                            res_num = int(re.sub(r"\D", "", str(raw_res)) or "720")
                            ugc_streams.append({
                                "id": str(r.get("resourceId") or time.time()),
                                "url": r_url,
                                "resolution": res_num,
                                "quality": f"{res_num}p",
                                "format": "MP4",
                                "codecName": "h264",
                                "size": str(r.get("size") or f"{res_num}p")
                            })

            if not found_in_collection and ugc_vid_id and (is_movie or target_ep == 1):
                vid_dl = make_official_request("GET", "/wefeed-mobile-bff/ugc/video/download", {"ugcVideoId": ugc_vid_id})
                items = (vid_dl.get("data") or {}).get("items") or [] if vid_dl else []
                for item in items:
                    resources = item.get("resources") or []
                    for r in resources:
                        r_url = r.get("url") or ""
                        if not is_dummy_url(r_url):
                            raw_res = r.get("resolutions") or r.get("resolution") or 720
                            res_num = int(re.sub(r"\D", "", str(raw_res)) or "720")
                            ugc_streams.append({
                                "id": str(r.get("resourceId") or time.time()),
                                "url": r_url,
                                "resolution": res_num,
                                "quality": f"{res_num}p",
                                "format": "MP4",
                                "codecName": "h264",
                                "size": str(r.get("size") or f"{res_num}p")
                            })

        try:
            for cand_id in candidate_ids:
                res_list = make_official_request("GET", "/wefeed-mobile-bff/subject-api/resource", {"subjectId": cand_id, "page": "0", "perPage": "20", "pagerMode": "0"})
                items = (res_list.get("data") or {}).get("list") or (res_list.get("data") or {}).get("items") or [] if res_list else []
                for it in items:
                    it_ep = it.get("ep") or it.get("episode")
                    if is_movie or (it_ep is not None and int(str(it_ep) if str(it_ep).isdigit() else 0) == target_ep):
                        src_url = str(it.get("sourceUrl") or it.get("resourceLink") or "")
                        if src_url and not is_dummy_url(src_url):
                            sniff_res = make_official_request("GET", "/wefeed-mobile-bff/sniff/config", {"linkUrl": src_url, "subjectId": cand_id})
                            dl_url = (sniff_res.get("data") or {}).get("downloadUrl") if sniff_res else ""
                            if dl_url and not is_dummy_url(dl_url) and not any(s["url"] == dl_url for s in ugc_streams):
                                raw_res = it.get("resolution") or it.get("resolutions") or 720
                                res_num = int(re.sub(r"\D", "", str(raw_res)) or "720")
                                ugc_streams.append({
                                    "id": str(it.get("resourceId") or time.time()),
                                    "url": dl_url,
                                    "resolution": res_num,
                                    "quality": f"{res_num}p",
                                    "format": "MP4",
                                    "codecName": str(it.get("codecName") or "h264"),
                                    "size": str(it.get("size") or f"{res_num}p")
                                })
        except Exception:
            pass

        if is_movie and detectors:
            for d in detectors:
                direct_dl = str(d.get("downloadUrl") or "")
                r_link = str(d.get("resourceLink") or d.get("sourceUrl") or "")
                raw_res = d.get("resolutions") or d.get("resolution") or 1080
                res_num = int(re.sub(r"\D", "", str(raw_res)) or "1080")
                if direct_dl and not is_dummy_url(direct_dl) and not any(s["url"] == direct_dl for s in ugc_streams):
                    ugc_streams.append({
                        "id": str(d.get("resourceId") or time.time()),
                        "url": direct_dl,
                        "resolution": res_num,
                        "quality": f"{res_num}p",
                        "format": "MP4",
                        "codecName": "h264",
                        "size": f"{res_num}p HD"
                    })
                elif r_link and not is_dummy_url(r_link):
                    sniff_res = make_official_request("GET", "/wefeed-mobile-bff/sniff/config", {"linkUrl": r_link, "subjectId": final_subject_id})
                    dl_url = (sniff_res.get("data") or {}).get("downloadUrl") if sniff_res else ""
                    if dl_url and not is_dummy_url(dl_url) and not any(s["url"] == dl_url for s in ugc_streams):
                        ugc_streams.append({
                            "id": str(d.get("resourceId") or time.time()),
                            "url": dl_url,
                            "resolution": res_num,
                            "quality": f"{res_num}p",
                            "format": "MP4",
                            "codecName": "h264",
                            "size": f"{res_num}p HD"
                        })

        if ugc_streams:
            streams = ugc_streams

    streams.sort(key=lambda s: (s.get("resolution", 0), 1 if ".m3u8" in s.get("url", "") else 0), reverse=True)
    best_stream = streams[0] if streams else None

    captions = fetch_captions(final_subject_id, final_resource_id or (detectors[0].get("resourceId") if detectors else ""), season, episode, is_movie)

    return {
        "subjectId": final_subject_id,
        "is_movie": is_movie,
        "season": season,
        "episode": episode,
        "streams": streams,
        "best_stream": best_stream,
        "best_url": best_stream.get("url") if best_stream else None,
        "captions": captions
    }

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

@app.get("/")
@app.get("")
@app.get("/app.py")
@app.get("/api/index")
@app.get("/api/index.py")
def root():
    return {"status": "ok", "service": "movie-box API", "version": "1.1.0"}

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

@app.get("/v1/movies")
@app.get("/v1/series")
@app.get("/v1/anime")
@app.get("/v1/asian")
@app.get("/v1/western")
@app.get("/v1/kids")
@app.get("/v1/category/{category_id}")
def get_category_feed(request: Request, category_id: int | None = None, page: int = Query(default=1, ge=1), _ = Depends(verify_token)):
    cat_map = {
        "movies": 1,
        "series": 5,
        "anime": 8,
        "asian": 18,
        "western": 19,
        "kids": 23,
    }
    target_id = category_id
    if target_id is None:
        for k, v in cat_map.items():
            if k in request.url.path:
                target_id = v
                break
    if target_id is None:
        target_id = 1

    res = make_official_request("GET", "/wefeed-mobile-bff/tab-operating", {"tabId": str(target_id), "page": str(page), "pageSize": "24"})
    if res and res.get("data"):
        items = res["data"].get("items") or res["data"].get("subjectList") or []
        filtered = [x for x in items if is_family_friendly(x)]
        return {"items": filtered, "page": page, "tabId": target_id}
    return {"items": [], "page": page, "tabId": target_id}

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
def get_catalog(name: str, page: int = Query(default=1, ge=1), genre: str = Query(default=""), _ = Depends(verify_token)):
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
def get_search(q: str = Query(...), page: int = Query(default=1, ge=1), _ = Depends(verify_token)):
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

@app.get("/v1/stream/{subject_id}")
@app.get("/stream/{subject_id}")
def get_stream(
    subject_id: str,
    season: int = Query(default=1, ge=1),
    episode: int = Query(default=1, ge=1),
    resource_id: str | None = Query(default=None),
    _ = Depends(verify_token)
):
    try:
        return resolve_streams(subject_id, season=season, episode=episode, resource_id=resource_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/v1/titles/{path:path}/playback")
def get_playback(path: str, season: int = Query(default=1, ge=1), episode: int = Query(default=1, ge=1), _ = Depends(verify_token)):
    try:
        subject_id = resolve_subject_id_from_path(path)
        if not subject_id:
            raise HTTPException(status_code=404, detail="Subject not found for path")

        return resolve_streams(str(subject_id), season=season, episode=episode)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/v1/titles/{path:path}/captions")
@app.get("/v1/captions/{subject_id}")
def get_captions_route(path: str = "", subject_id: str = "", season: int = Query(default=1, ge=1), episode: int = Query(default=1, ge=1), _ = Depends(verify_token)):
    try:
        sid = subject_id or resolve_subject_id_from_path(path)
        if sid:
            return fetch_captions(str(sid), season=season, episode=episode)
    except Exception:
        pass
    return []

@app.get("/v1/titles/{path:path}/recommendations")
def get_recommendations(path: str, page: int = Query(default=1, ge=1), _ = Depends(verify_token)):
    try:
        sid = resolve_subject_id_from_path(path)
        if sid:
            rec_res = session.get(
                f"{H5_API_BASE}/subject/detail-rec",
                params={"subjectId": sid, "page": page, "perPage": 12, "host": "themoviebox.xyz"},
                headers={"Referer": f"{UPSTREAM_BASE}/movies/{path}"},
                timeout=15,
            )
            if rec_res.status_code == 200:
                items = [x for x in rec_res.json().get("data", {}).get("items", []) if is_family_friendly(x)]
                return {"items": items, "next_page": page + 1 if len(items) == 12 else None}
    except Exception:
        pass
    return {"items": [], "next_page": None}

@app.get("/v1/detail/{subject_id}")
def get_detail_by_id(subject_id: str, _ = Depends(verify_token)):
    res = make_official_request("GET", "/wefeed-mobile-bff/subject-api/get", {"subjectId": subject_id})
    if res and res.get("data"):
        return res["data"]
    raise HTTPException(status_code=404, detail="Detail not found")

@app.get("/v1/seasons/{subject_id}")
@app.get("/v1/episodes/{subject_id}")
def get_seasons(subject_id: str, _ = Depends(verify_token)):
    res = make_official_request("GET", "/wefeed-mobile-bff/subject-api/season-info/v2", {"subjectId": subject_id})
    if res and res.get("data"):
        return res["data"]
    return {"seasons": []}

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
        sid = resolve_subject_id_from_path(path)
        if sid:
            off_res = make_official_request("GET", "/wefeed-mobile-bff/subject-api/get", {"subjectId": sid})
            if off_res and off_res.get("data"):
                return off_res["data"]
        raise HTTPException(status_code=404, detail="Title not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
