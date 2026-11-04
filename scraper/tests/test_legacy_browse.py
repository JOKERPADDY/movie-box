import json

import requests
from fastapi.testclient import TestClient

import main as legacy


def test_legacy_vercel_browse_uses_short_catalog(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({
            "code": 0,
            "data": {
                "items": [
                    {
                        "subjectId": "short-1",
                        "subjectType": 7,
                        "title": "Short Drama",
                        "detailPath": "short-drama-abc",
                        "cover": {"url": "https://example.test/poster.jpg"},
                        "hasResource": True,
                    },
                    {"subjectId": "movie-1", "subjectType": 1, "title": "Movie"},
                ],
                "pager": {"hasMore": True, "nextPage": "3"},
            },
        }).encode()
        return response

    monkeypatch.setattr(legacy.session, "post", post)
    legacy.app.dependency_overrides[legacy.verify_token] = lambda: True
    try:
        with TestClient(legacy.app) as client:
            response = client.get("/v1/browse", params={"kind": "short_series", "page": 2})
    finally:
        legacy.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "items": [{
            "id": "short-1",
            "detail_path": "short-drama-abc",
            "kind": "short_series",
            "title": "Short Drama",
            "description": "",
            "release_date": None,
            "duration_seconds": None,
            "genres": [],
            "poster_url": "https://example.test/poster.jpg",
            "backdrop_url": None,
            "country": None,
            "rating": None,
            "has_resource": True,
        }],
        "page": 2,
        "next_page": 3,
        "has_more": True,
    }
    assert len(calls) == 1
    assert calls[0][0] == f"{legacy.H5_API_BASE}/subject/filter"
    assert calls[0][1]["json"] == {
        "channelId": 7,
        "page": 2,
        "perPage": 18,
        "sort": "ForYou",
    }
