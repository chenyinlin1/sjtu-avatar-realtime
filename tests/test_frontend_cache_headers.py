from fastapi import FastAPI
from fastapi.testclient import TestClient
import gradio

from service.frontend_service.frontend_service import (
    FrontendRegistrationOptions,
    NoCacheHTMLStaticFiles,
    register_frontend,
)


def test_frontend_html_entry_is_not_cached(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<div id='app'></div>", encoding="utf-8")

    app = FastAPI()
    app.mount("/ui", NoCacheHTMLStaticFiles(directory=dist), name="static")

    response = TestClient(app).get("/ui/index.html")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"


def test_frontend_hashed_assets_keep_default_static_headers(tmp_path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (assets / "main.abc123.js").write_text("console.log('ok')", encoding="utf-8")

    app = FastAPI()
    app.mount("/ui", NoCacheHTMLStaticFiles(directory=dist), name="static")

    response = TestClient(app).get("/ui/assets/main.abc123.js")

    assert response.status_code == 200
    assert "cache-control" not in response.headers


def test_root_redirect_targets_versioned_frontend_entry(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<div id='app'></div>", encoding="utf-8")

    app = FastAPI()
    with gradio.Blocks() as ui:
        pass
    register_frontend(
        app=app,
        ui=ui,
        parent_block=None,
        init_config={},
        options=FrontendRegistrationOptions(frontend_dist_relative_path=str(dist)),
    )

    response = TestClient(app).get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"
    assert response.headers["location"].startswith("/ui/index.html?v=")
