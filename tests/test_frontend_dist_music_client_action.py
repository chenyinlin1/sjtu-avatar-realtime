import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "src/service/frontend_service/frontend/dist"


def test_served_frontend_dist_contains_music_client_action_handler():
    index_html = (DIST_DIR / "index.html").read_text()
    script_paths = re.findall(r'src="([^\"]+\.js)"', index_html)
    assert script_paths, "dist/index.html should reference built JavaScript assets"

    def resolve_dist_script(script_path: str) -> Path:
        script_path = script_path.removeprefix("/ui/").lstrip("/")
        return DIST_DIR / script_path

    bundled_js = "\n".join(
        resolve_dist_script(script_path).read_text(errors="ignore")
        for script_path in script_paths
    )

    assert "client_action" in bundled_js
    assert "music.play" in bundled_js
    assert "new Audio" in bundled_js
    assert "[music] client action received" in bundled_js
    assert "[music] play requested" in bundled_js
    assert "[music] audio error" in bundled_js
    assert "[music] WebRTC data channel consumed client_action" in bundled_js
    assert re.search(
        r"initChatDataChannel\(\)\{.*?handleClientAction.*?updateChatRecords",
        bundled_js,
        re.DOTALL,
    )
