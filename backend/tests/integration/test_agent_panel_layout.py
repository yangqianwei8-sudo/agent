"""Agent panel layout contract — prevent chat column squeeze / composer clip regressions."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CSS = BACKEND_ROOT / "static" / "lawyer_agent" / "workspace.css"
HTML = BACKEND_ROOT / "templates" / "cases" / "workspace.html"


def test_agent_panel_grid_uses_minmax_for_dual_column():
    css = CSS.read_text(encoding="utf-8")
    assert "grid-template-columns: minmax(0, 1fr) minmax(320px, 400px)" in css


def test_agent_panel_sticky_with_fixed_internal_height():
    css = CSS.read_text(encoding="utf-8")
    assert "position: sticky" in css
    assert "--agent-panel-height" in css
    assert "height: var(--agent-panel-height)" in css


def test_chat_log_flex_scroll_not_viewport_cap():
    css = CSS.read_text(encoding="utf-8")
    assert "flex: 1 1 auto" in css
    assert "min-height: 0" in css
    # Old regression: capped messages area and pushed composer away
    assert "max-height: 42vh" not in css


def test_chat_composer_non_shrinking():
    css = CSS.read_text(encoding="utf-8")
    idx = css.index(".chat-form {")
    block = css[idx : idx + 200]
    assert "flex: 0 0 auto" in block


def test_workspace_html_agent_panel_class():
    html = HTML.read_text(encoding="utf-8")
    assert 'class="ws-right card chat agent-panel"' in html
