from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

from chessfen.app import create_app
from chessfen.settings import Settings

_START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_CHECKMATE_FEN = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"


@pytest.fixture(scope="module")
def live_analysis_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    database = tmp_path_factory.mktemp("browser") / "chessfen.db"
    app = create_app(
        Settings(
            database_url=f"sqlite:///{database.as_posix()}",
            secret_key="browser-test-secret",
            admin_password="browser-test-admin",
        )
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"{base_url}/health", timeout=0.5
            ) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.05)
    else:
        raise RuntimeError("Browser test server did not become ready")
    yield base_url
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def browser_page(live_analysis_url: str) -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(locale="en-US")
        page = context.new_page()
        page.set_default_timeout(30_000)
        yield page
        context.close()
        browser.close()


def test_real_browser_engine_analyzes_cancels_and_restarts(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: (
            console_errors.append(message.text)
            if message.type == "error"
            else None
        ),
    )
    page.goto(f"{live_analysis_url}/tools/analysis")

    expect(page.get_by_role("heading", level=1)).to_have_text(
        "Position analysis"
    )
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()

    status = page.locator("[data-analysis-status]")
    expect(status).to_contain_text("Analyzing", timeout=30_000)
    expect(page.get_by_role("button", name="Cancel")).to_be_enabled()
    page.get_by_role("button", name="Cancel").click()
    expect(status).to_contain_text("Analysis cancelled")
    expect(page.get_by_role("button", name="Restart")).to_be_enabled()

    page.get_by_role("button", name="Restart").click()
    expect(status).to_contain_text("Analysis complete", timeout=30_000)
    expect(page.locator("[data-analysis-evaluation]")).not_to_be_empty()
    expect(page.locator("[data-candidate-line]")).to_have_count(3)
    expect(page.locator("[data-analysis-depth]")).not_to_be_empty()
    expect(page.locator("[data-analysis-nps]")).not_to_be_empty()
    expect(page.locator("[data-analysis-root-move]")).not_to_be_empty()
    board_source = page.locator("[data-analysis-board]").get_attribute("src")
    assert board_source is not None
    assert board_source.startswith("data:image/svg+xml;base64,")
    candidate_moves = page.locator(".candidate-line-moves").first.inner_text()
    assert candidate_moves.strip()
    assert "e2e4" not in candidate_moves

    assert console_errors == []


def test_browser_names_the_mating_side_for_a_mate_evaluation(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")

    def mate_lines(route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "lines": [
                        {
                            "rank": 1,
                            "depth": 18,
                            "evaluation": {
                                "kind": "mate",
                                "value": 3,
                                "text": "+M3",
                            },
                            "moves": [{"uci": "e2e4", "san": "e4"}],
                        }
                    ]
                }
            ),
        )

    page.route("**/tools/analysis/lines", mate_lines)
    try:
        page.locator("#analysis-fen").fill(_START_FEN)
        page.get_by_role("button", name="Start analysis").click()
        expect(page.locator("[data-analysis-status]")).to_contain_text(
            "Analysis complete", timeout=30_000
        )
        expect(page.locator("[data-analysis-evaluation]")).to_have_text(
            "+M3 · White has a forced mate."
        )
    finally:
        page.unroute("**/tools/analysis/lines", mate_lines)


def test_browser_reports_an_engine_start_failure(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")

    def fail_engine(route) -> None:
        route.abort()

    page.route("**/stockfish-19-lite-single.js", fail_engine)
    try:
        page.locator("#analysis-fen").fill(_START_FEN)
        page.get_by_role("button", name="Start analysis").click()
        expect(page.locator("[data-analysis-error]")).to_have_text(
            "Stockfish could not start or stopped unexpectedly.",
            timeout=30_000,
        )
        expect(page.locator("[data-analysis-status]")).to_have_text(
            "Analysis stopped."
        )
    finally:
        page.unroute("**/stockfish-19-lite-single.js", fail_engine)


def test_browser_restart_during_search_does_not_reuse_the_old_job(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()

    status = page.locator("[data-analysis-status]")
    expect(status).to_contain_text("Analyzing", timeout=30_000)
    page.get_by_role("button", name="Restart").click()

    expect(status).to_contain_text("Analysis complete", timeout=30_000)
    expect(page.locator("[data-candidate-line]")).to_have_count(3)
    assert page.locator("[data-analysis-result]").get_attribute("hidden") is None


def test_browser_shows_terminal_result_without_candidate_lines(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_CHECKMATE_FEN)
    page.get_by_role("button", name="Start analysis").click()

    expect(page.locator("[data-analysis-terminal]")).to_have_text(
        "Checkmate — Black wins."
    )
    expect(page.locator("[data-analysis-status]")).to_have_text(
        "Analysis complete."
    )
    expect(page.locator("[data-candidate-line]")).to_have_count(0)
    expect(page.get_by_role("button", name="Restart")).to_be_disabled()


def test_browser_reports_placement_only_before_starting_engine(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill("8/8/8/8/8/8/4K3/7k")
    page.get_by_role("button", name="Start analysis").click()

    expect(page.locator("[data-analysis-error]")).to_have_text(
        "Use a Full FEN with six fields. Placement only is not enough for analysis."
    )
    expect(page.locator("[data-analysis-status]")).to_have_text(
        "Enter a Full FEN to begin."
    )
