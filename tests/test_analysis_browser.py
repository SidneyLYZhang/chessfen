from __future__ import annotations

import http.server
import io
import json
import re
import socket
import ssl
import subprocess
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import chess
import chess.pgn
import pytest
import uvicorn
from playwright.sync_api import Browser, Page, expect, sync_playwright

from chessfen.app import create_app
from chessfen.settings import Settings

_START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_AFTER_E4_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
_CHECKMATE_FEN = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
_FOOLS_MATE_SETUP_FEN = (
    "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq g3 0 2"
)
_FORCED_CAPTURE_FEN = "7k/8/8/8/8/8/1q6/K7 w - - 0 1"


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
def local_cross_origin_llm(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[dict[str, Any]]:
    certificate_dir = tmp_path_factory.mktemp("custom-llm")
    certificate = certificate_dir / "cert.pem"
    private_key = certificate_dir / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    state: dict[str, Any] = {"mode": "success", "requests": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def _cors(self, allow: bool) -> dict[str, str]:
            if not allow:
                return {}
            origin = self.headers.get("Origin", "*")
            return {
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Headers": "authorization,content-type",
                "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
                "Access-Control-Max-Age": "0",
                "Vary": "Origin",
            }

        def _record(self) -> None:
            state["requests"].append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": {
                        key.lower(): value
                        for key, value in self.headers.items()
                    },
                }
            )

        def _json_response(
            self, status: int, payload: dict[str, Any], allow_cors: bool = True
        ) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            for key, value in self._cors(allow_cors).items():
                self.send_header(key, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self._record()
            if state["mode"] == "cors":
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(204)
            for key, value in self._cors(True).items():
                self.send_header(key, value)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            self._record()
            self._json_response(
                503, {"error": {"message": "model list unavailable"}}
            )

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            state["requests"].append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": {
                        key.lower(): value
                        for key, value in self.headers.items()
                    },
                    "body": body,
                }
            )
            mode = state["mode"]
            if mode == "cors":
                self._json_response(
                    200, {"choices": [{"message": {"content": "{}"}}]}, False
                )
                return
            content = json.dumps(
                {
                    "summary": (
                        "Custom no usage"
                        if mode == "no_usage"
                        else "Custom local summary"
                    ),
                    "why": "engine facts",
                    "alternatives": "candidate lines",
                    "uncertainty": "limited",
                }
            )
            if mode == "malformed":
                content = "{not-json"
            response: dict[str, Any] = {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": content,
                            "reasoning_content": "SECRET-LOCAL-REASONING",
                        },
                    }
                ]
            }
            if mode != "no_usage":
                response["usage"] = {
                    "prompt_tokens": 3,
                    "completion_tokens": 6,
                    "completion_tokens_details": {"reasoning_tokens": 4},
                    "total_tokens": 9,
                }
            self._json_response(200, response)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {
        "url": f"https://127.0.0.1:{port}/v1",
        "state": state,
    }
    server.shutdown()
    thread.join(timeout=10)
    server.server_close()


@pytest.fixture(scope="module")
def playwright_instance():
    with sync_playwright() as playwright:
        yield playwright


@pytest.fixture(scope="module")
def browser(playwright_instance):
    instance = playwright_instance.chromium.launch()
    yield instance
    instance.close()


@pytest.fixture
def browser_page(browser) -> Iterator[Page]:
    context = browser.new_context(locale="en-US", ignore_https_errors=True)
    page = context.new_page()
    page.set_default_timeout(30_000)
    page.on("dialog", lambda dialog: dialog.accept())
    yield page
    context.close()


@pytest.fixture(scope="module", params=["chromium", "firefox", "webkit"])
def cross_browser(
    playwright_instance, request
) -> Iterator[tuple[str, Browser]]:
    instance = getattr(playwright_instance, request.param).launch()
    yield request.param, instance
    instance.close()


@pytest.fixture
def cross_browser_page(cross_browser) -> Iterator[tuple[str, Page]]:
    name, browser = cross_browser
    context = browser.new_context(locale="en-US", ignore_https_errors=True)
    page = context.new_page()
    page.set_default_timeout(30_000)
    page.on("dialog", lambda dialog: dialog.accept())
    yield name, page
    context.close()


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


def test_browser_labels_a_mate_loss_without_a_fake_centipawn_value(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")

    def mate_loss_assessment(route) -> None:
        line = {
            "rank": 1,
            "depth": 18,
            "evaluation": {"kind": "cp", "value": 0, "text": "0.00"},
            "moves": [{"uci": "e2e4", "san": "e4", "fen": _AFTER_E4_FEN}],
        }
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "lines": [line],
                    "assessment": {
                        "quality": "blunder",
                        "cpl": None,
                        "mate_loss": True,
                        "best_move": {
                            "uci": "d2d4",
                            "san": "d4",
                            "fen": _AFTER_E4_FEN,
                        },
                        "actual_move": {
                            "uci": "e2e4",
                            "san": "e4",
                            "fen": _AFTER_E4_FEN,
                        },
                        "best_evaluation": {
                            "kind": "cp",
                            "value": 0,
                            "text": "0.00",
                        },
                        "actual_evaluation": {
                            "kind": "mate",
                            "value": -2,
                            "text": "-M2",
                        },
                        "is_forced": False,
                        "terminal": None,
                    },
                }
            ),
        )

    page.route("**/tools/analysis/assess", mate_loss_assessment)
    try:
        page.locator("#analysis-fen").fill(_START_FEN)
        page.locator("#analysis-move").fill("e4")
        page.get_by_role("button", name="Start analysis").click()
        expect(page.locator("[data-analysis-status]")).to_contain_text(
            "Analysis complete", timeout=30_000
        )
        expect(page.locator("[data-assessment-loss-label]")).to_have_text(
            "Mate loss"
        )
        expect(page.locator("[data-assessment-cpl]")).to_have_text("Yes")
    finally:
        page.unroute("**/tools/analysis/assess", mate_loss_assessment)


def test_browser_reports_an_engine_start_failure(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")

    def fail_engine(route) -> None:
        route.abort()

    page.route("**/stockfish-19-lite-*.js", fail_engine)
    try:
        page.locator("#analysis-fen").fill(_START_FEN)
        page.get_by_role("button", name="Start analysis").click()
        expect(page.locator("[data-analysis-error]")).to_have_text(
            "Stockfish could not start or stopped unexpectedly. Reload the page and check that this browser supports Web Workers and WebAssembly.",
            timeout=30_000,
        )
        expect(page.locator("[data-analysis-status]")).to_have_text(
            "Analysis stopped."
        )
    finally:
        page.unroute("**/stockfish-19-lite-*.js", fail_engine)


def test_browser_assesses_an_actual_move_and_shows_its_facts(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.locator("#analysis-move").fill("e4")
    page.get_by_role("button", name="Start analysis").click()

    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    assessment = page.locator("[data-analysis-assessment]")
    expect(assessment).to_be_visible()
    expect(page.locator("[data-assessment-actual]")).to_have_text("e4 (e2e4)")
    expect(page.locator("[data-assessment-best]")).not_to_be_empty()
    expect(page.locator("[data-assessment-quality]")).not_to_be_empty()
    expect(page.locator("[data-assessment-cpl]")).to_have_text(re.compile(r"^\d+$"))
    expect(page.locator("[data-assessment-after-fen]")).to_have_text(
        _AFTER_E4_FEN
    )
    expect(page.locator("[data-candidate-line]")).to_have_count(3)
    expect(page.locator("[data-analysis-root-move]")).to_have_text("e2e4")
    assert page.locator("#analysis-fen").input_value() == _START_FEN
    assert page.locator("#analysis-move").input_value() == "e4"


def test_browser_shows_terminal_outcome_for_an_actual_move(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_FOOLS_MATE_SETUP_FEN)
    page.locator("#analysis-move").fill("Qh4#")
    page.get_by_role("button", name="Start analysis").click()

    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    expect(page.locator("[data-assessment-actual]")).to_have_text(
        "Qh4# (d8h4)"
    )
    expect(page.locator("[data-assessment-quality]")).to_have_text("Best")
    expect(page.locator("[data-assessment-outcome]")).to_have_text(
        "Checkmate — Black wins."
    )
    expect(page.locator("[data-assessment-outcome]")).to_be_visible()


def test_browser_marks_a_forced_actual_move(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_FORCED_CAPTURE_FEN)
    page.locator("#analysis-move").fill("Kxb2")
    page.get_by_role("button", name="Start analysis").click()

    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    expect(page.locator("[data-assessment-quality]")).to_have_text("Forced")
    expect(page.locator("[data-assessment-cpl]")).to_have_text("0")
    expect(page.locator("[data-assessment-outcome]")).to_have_text(
        "Draw by insufficient material."
    )


def test_browser_reports_an_illegal_actual_move_as_a_field_error(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.locator("#analysis-move").fill("e5")
    page.get_by_role("button", name="Start analysis").click()

    expect(page.locator("[data-analysis-move-error]")).to_have_text(
        "That move is not legal in this Position."
    )
    expect(page.locator("[data-analysis-status]")).to_have_text(
        "Enter a Full FEN to begin."
    )
    expect(page.locator("[data-analysis-result]")).to_be_hidden()


def test_browser_steps_through_candidate_lines_without_mutating_input(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )

    board = page.locator("[data-analysis-board]")
    original_board = board.get_attribute("src")
    assert original_board is not None

    page.locator("[data-candidate-line-button]").first.click()
    playback = page.locator("[data-analysis-playback]")
    expect(playback).to_be_visible()
    expect(page.locator("[data-playback-status]")).to_contain_text("#1 · 1/")
    expect(board).not_to_have_attribute("src", original_board)
    first_board = board.get_attribute("src")

    page.get_by_role("button", name="Next").click()
    expect(page.locator("[data-playback-status]")).to_contain_text("#1 · 2/")
    expect(board).not_to_have_attribute("src", first_board)

    page.get_by_role("button", name="Previous").click()
    expect(page.locator("[data-playback-status]")).to_contain_text("#1 · 1/")
    expect(board).to_have_attribute("src", first_board)

    expect(page.locator("[data-analysis-telemetry]")).to_be_hidden()
    expect(page.locator(".analysis-summary")).to_be_hidden()

    page.get_by_role("button", name="Return to original Position").click()
    expect(page.locator("[data-playback-status]")).to_contain_text("#1 · 0/")
    expect(board).to_have_attribute("src", original_board)
    expect(page.locator("[data-analysis-telemetry]")).to_be_visible()
    expect(page.locator(".analysis-summary")).to_be_visible()
    assert page.locator("#analysis-fen").input_value() == _START_FEN

    second_line = page.locator("[data-candidate-line-button]").nth(1)
    second_line.click()
    expect(second_line).to_have_attribute("aria-pressed", "true")
    playback_status = page.locator("[data-playback-status]")
    expect(playback_status).to_contain_text("#2 · 1/")
    status_text = playback_status.inner_text()
    steps_match = re.search(r"1/(\d+)", status_text)
    assert steps_match is not None
    total_steps = int(steps_match.group(1))
    for step in range(2, total_steps + 1):
        page.get_by_role("button", name="Next").click()
        expect(playback_status).to_contain_text(f"#2 · {step}/{total_steps}")
    expect(page.get_by_role("button", name="Next")).to_be_disabled()
    expect(page.get_by_role("button", name="Return to original Position")).to_be_enabled()


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


def test_browser_analyzes_a_complete_pgn_mainline(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    page.locator("#pgn").fill(
        '[Event "Short"]\n[White "Alice"]\n[Black "Bob"]\n[Result "*"]\n\n1. e4 *\n'
    )
    page.get_by_role("button", name="Parse PGN").click()
    expect(page.locator("#game_index")).to_be_visible()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    expect(page.get_by_role("button", name="Start Game Analysis")).to_be_visible()
    page.get_by_role("button", name="Start Game Analysis").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    expect(page.locator("[data-game-ribbon]")).to_be_visible()
    expect(page.locator("[data-game-moves] [data-ply='1']")).to_be_visible()
    page.locator("[data-game-moves] [data-ply='1']").click()
    expect(page.locator("[data-game-quality]")).not_to_be_empty()
    expect(page.locator("[data-game-lines] .candidate-line")).not_to_have_count(0)
    expect(page.locator("[data-game-exact]")).not_to_be_empty()
    expect(page.locator("[data-game-indicators]")).not_to_be_empty()


def test_browser_cancels_and_continues_a_game_queue(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    page.locator("#pgn").fill(
        '[Event "Two moves"]\n[White "Alice"]\n[Black "Bob"]\n[Result "*"]\n\n1. e4 e5 *\n'
    )
    page.get_by_role("button", name="Parse PGN").click()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    page.get_by_role("button", name="Start Game Analysis").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analyzing Position", timeout=20_000
    )
    page.get_by_role("button", name="Cancel").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis cancelled"
    )
    continue_button = page.get_by_role("button", name="Continue")
    expect(continue_button).to_be_enabled()
    continue_button.click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    expect(page.locator("[data-game-moves] [data-ply]")).to_have_count(3)
    page.locator("[data-graph-ply='1']").click()
    expect(page.locator("[data-game-quality]")).not_to_be_empty()


def test_browser_applies_local_multipv_preset_and_resource_settings(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    with page.expect_navigation():
        page.locator("[data-setting-multipv]").select_option("1")
    with page.expect_navigation():
        page.locator("[data-setting-preset]").select_option("fast")
    expect(page.locator("[data-engine-runtime]")).to_contain_text(
        "checking runtime · fast"
    )
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    expect(page.locator("[data-engine-runtime]")).to_contain_text(
        "multithreaded · fast"
    )
    expect(page.locator("[data-candidate-line]")).to_have_count(1)

    with page.expect_navigation():
        page.locator("[data-setting-resource]").select_option("full")
    expect(page.locator("[data-setting-resource-warning]")).to_be_visible()
    expect(page.locator("[data-engine-provenance]")).to_have_text(
        "Stockfish 19 · Full"
    )

    with page.expect_navigation():
        page.locator("[data-setting-resource]").select_option("lite")
    with page.expect_navigation():
        page.locator("[data-setting-multipv]").select_option("3")
    with page.expect_navigation():
        page.locator("[data-setting-preset]").select_option("standard")


def test_browser_restores_a_position_workspace_after_reload_and_new_page(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.locator("#analysis-move").fill("e4")
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )

    page.reload()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Restored from the local Analysis Workspace."
    )
    expect(page.locator("[data-candidate-line]")).to_have_count(3)
    assert page.locator("#analysis-fen").input_value() == _START_FEN
    assert page.locator("#analysis-move").input_value() == "e4"

    second_page = page.context.new_page()
    second_page.set_default_timeout(30_000)
    second_page.goto(f"{live_analysis_url}/tools/analysis")
    expect(second_page.locator("[data-analysis-status]")).to_contain_text(
        "Restored from the local Analysis Workspace."
    )
    expect(second_page.locator("[data-candidate-line]")).to_have_count(3)
    second_page.close()


def test_browser_restores_a_game_workspace_from_the_get_page(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    page.locator("#pgn").fill(
        '[Event "Restore"]\n[White "Alice"]\n[Black "Bob"]\n[Result "*"]\n\n1. e4 *\n'
    )
    page.get_by_role("button", name="Parse PGN").click()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    page.get_by_role("button", name="Start Game Analysis").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )

    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    restore = page.locator("[data-workspace-restore]")
    expect(restore).to_be_visible()
    with page.expect_navigation():
        page.locator("[data-workspace-restore-button]").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Restored from the local Analysis Workspace."
    )
    expect(page.locator("[data-game-moves] [data-ply='1']")).to_be_visible()


def test_browser_reports_storage_failure_and_still_exports(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.evaluate(
        """() => {
          IDBObjectStore.prototype.put = function () {
            throw new DOMException("quota", "QuotaExceededError");
          };
        }"""
    )
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    expect(page.locator("[data-workspace-status]")).to_contain_text(
        "not being saved locally"
    )
    expect(page.locator("[data-candidate-line]")).to_have_count(3)
    with page.expect_download() as download_info:
        page.locator("[data-position-export-markdown]").click()
    markdown = Path(download_info.value.path()).read_text(encoding="utf-8")
    assert "# ChessFEN Position Analysis" in markdown
    assert "## Candidate Lines" in markdown


def test_browser_reports_a_corrupt_workspace_without_breaking_analysis(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.evaluate(
        """async () => {
          const open = indexedDB.open("chessfen-analysis", 1);
          const db = await new Promise((resolve, reject) => {
            open.onsuccess = () => resolve(open.result);
            open.onerror = () => reject(open.error);
          });
          await new Promise((resolve, reject) => {
            const tx = db.transaction("workspaces", "readwrite");
            tx.objectStore("workspaces").put({ id: "current", version: 99 });
            tx.oncomplete = resolve;
            tx.onerror = () => reject(tx.error);
          });
          db.close();
        }"""
    )
    page.reload()
    expect(page.locator("[data-workspace-status]")).to_contain_text(
        "could not be read"
    )
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )


def test_browser_invalidates_engine_results_after_settings_change(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    with page.expect_navigation():
        page.locator("[data-setting-multipv]").select_option("2")
    expect(page.locator("[data-workspace-status]")).to_contain_text(
        "discarded because engine settings changed"
    )
    expect(page.locator("[data-candidate-line]")).to_have_count(0)
    assert page.locator("#analysis-fen").input_value() == _START_FEN


def test_browser_resumes_the_first_incomplete_game_position(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    page.locator("#pgn").fill(
        '[Event "Resume"]\n[White "Alice"]\n[Black "Bob"]\n[Result "*"]\n\n1. e4 e5 *\n'
    )
    page.get_by_role("button", name="Parse PGN").click()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    page.get_by_role("button", name="Start Game Analysis").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analyzing Position 2/", timeout=30_000
    )
    page.get_by_role("button", name="Cancel").click()
    expect(page.locator("[data-game-status]")).to_contain_text("cancelled")

    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    expect(page.locator("[data-workspace-restore]")).to_be_visible()
    with page.expect_navigation():
        page.locator("[data-workspace-restore-button]").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Restored 1/3 Positions"
    )
    continue_button = page.get_by_role("button", name="Continue")
    expect(continue_button).to_be_enabled()
    continue_button.click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )


def test_browser_requires_confirmation_before_restart(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    dialogs: list[str] = []
    page.on("dialog", lambda dialog: dialogs.append(dialog.message))
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.get_by_role("button", name="Restart").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    assert any("Restart analysis" in message for message in dialogs)


def test_browser_secures_profile_and_generates_grounded_explanation(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    captured: list[dict[str, Any]] = []

    def provider(route) -> None:
        request = route.request
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        if request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        if request.url.endswith("/models"):
            route.fulfill(
                status=200,
                headers={**headers, "Content-Type": "application/json"},
                body=json.dumps({"data": [{"id": "mock-model"}]}),
            )
            return
        captured.append(
            {
                "authorization": request.headers.get("authorization"),
                "body": request.post_data_json,
            }
        )
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "summary": "控制中心，稳步发展。",
                                        "why": "Candidate Lines 支持中心控制。",
                                        "alternatives": "第二条变化同样合理。",
                                        "uncertainty": "战略判断仅基于已给出的引擎事实。",
                                    }
                                ),
                                "reasoning_content": "SECRET-RAW-REASONING",
                            }
                        }
                    ],
                    "usage": {"total_tokens": 123},
                }
            ),
        )

    page.route("https://api.openai.test/**", provider)
    page.goto(f"{live_analysis_url}/settings")
    page.locator("[data-llm-base-url]").fill("https://api.openai.test/v1")
    page.locator("[data-llm-model]").fill("mock-model")
    page.locator("[data-llm-api-key]").fill("sk-test-secret-value")
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.locator("[data-llm-passphrase-confirm]").fill("correct horse battery")
    page.locator("[data-llm-consent]").check()
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text(
        "saved locally"
    )

    stored = page.evaluate(
        """async () => {
          const open = indexedDB.open("chessfen-credentials", 1);
          const db = await new Promise((resolve, reject) => {
            open.onsuccess = () => resolve(open.result);
            open.onerror = () => reject(open.error);
          });
          const record = await new Promise((resolve, reject) => {
            const tx = db.transaction("credentials", "readonly");
            const req = tx.objectStore("credentials").get("llm");
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
          });
          db.close();
          return {
            plaintext: JSON.stringify(record).includes("sk-test-secret-value"),
            ciphertextBytes: record.ciphertext.byteLength,
            local: JSON.stringify(localStorage).includes("sk-test-secret-value"),
          };
        }"""
    )
    assert stored["plaintext"] is False
    assert stored["local"] is False
    assert stored["ciphertextBytes"] > 0

    page.locator("[data-llm-passphrase]").fill("wrong passphrase")
    page.get_by_role("button", name="Unlock").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("Unlock failed")
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Unlock").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("unlocked")

    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    expect(page.locator("[data-explanation-section]")).to_be_visible()
    page.locator("[data-explanation-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-explanation-summary]")).to_have_text(
        "控制中心，稳步发展。"
    )
    assert captured
    assert captured[0]["authorization"] == "Bearer sk-test-secret-value"
    request_body = captured[0]["body"]
    assert request_body["reasoning_effort"] == "max"
    assert request_body["max_completion_tokens"] == 16384
    assert request_body["response_format"] == {"type": "json_object"}
    assert "SECRET-RAW-REASONING" not in page.content()
    workspace = _workspace_record_text(page)
    assert "SECRET-RAW-REASONING" not in workspace
    assert "sk-test-secret-value" not in workspace

    page.reload()
    expect(page.locator("[data-explanation-summary]")).to_have_text(
        "控制中心，稳步发展。"
    )
    page.evaluate(
        """() => {
          const key = "chessfen.analysis.llm.profile.v1";
          const profile = JSON.parse(localStorage.getItem(key));
          profile.audience = "beginner";
          localStorage.setItem(key, JSON.stringify(profile));
        }"""
    )
    page.reload()
    expect(page.locator("[data-explanation-stale]")).to_be_visible()
    page.unroute("https://api.openai.test/**", provider)


def _configure_mock_profile(
    page: Page, live_analysis_url: str, base_url: str = "https://api.openai.test/v1"
) -> None:
    page.goto(f"{live_analysis_url}/settings")
    page.locator("[data-llm-base-url]").fill(base_url)
    page.locator("[data-llm-model]").fill("mock-model")
    page.locator("[data-llm-api-key]").fill("sk-test-secret-value")
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.locator("[data-llm-passphrase-confirm]").fill("correct horse battery")
    page.locator("[data-llm-consent]").check()
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")


def _configure_openrouter_profile(
    page: Page, live_analysis_url: str
) -> None:
    page.goto(f"{live_analysis_url}/settings")
    page.locator("[data-llm-provider]").select_option("openrouter")
    page.locator("[data-llm-model]").fill("openrouter-model")
    page.locator("[data-llm-api-key]").fill("sk-or-test")
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.locator("[data-llm-passphrase-confirm]").fill("correct horse battery")
    page.locator("[data-llm-consent]").check()
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")


def _configure_deepseek_profile(
    page: Page,
    live_analysis_url: str,
    model: str = "deepseek-flash",
    reasoning: bool = True,
) -> None:
    page.goto(f"{live_analysis_url}/settings")
    page.locator("[data-llm-provider]").select_option("deepseek")
    page.locator("[data-llm-model]").fill(model)
    if not reasoning:
        page.locator("[data-llm-reasoning]").uncheck()
    page.locator("[data-llm-api-key]").fill("sk-ds-test")
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.locator("[data-llm-passphrase-confirm]").fill("correct horse battery")
    page.locator("[data-llm-consent]").check()
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")


def _configure_kimi_profile(
    page: Page,
    live_analysis_url: str,
    region: str = "international",
    model: str = "kimi-k3",
    reasoning: bool = True,
) -> None:
    page.goto(f"{live_analysis_url}/settings")
    page.locator("[data-llm-provider]").select_option("kimi")
    page.locator("[data-llm-kimi-region]").select_option(region)
    page.locator("[data-llm-model]").fill(model)
    if not reasoning:
        page.locator("[data-llm-reasoning]").uncheck()
    page.locator("[data-llm-api-key]").fill("sk-kimi-test")
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.locator("[data-llm-passphrase-confirm]").fill("correct horse battery")
    page.locator("[data-llm-consent]").check()
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")


def _configure_custom_profile(
    page: Page,
    live_analysis_url: str,
    endpoint: str = "https://custom.test/v1",
    model: str = "custom-model",
    dialect: str = "openai",
    budget_field: str = "max_completion_tokens",
    effort: str = "max",
) -> None:
    page.goto(f"{live_analysis_url}/settings")
    page.locator("[data-llm-provider]").select_option("custom")
    page.locator("[data-llm-base-url]").fill(endpoint)
    page.locator("[data-llm-model]").fill(model)
    page.locator("[data-llm-custom-dialect]").select_option(dialect)
    page.locator("[data-llm-custom-budget-field]").select_option(budget_field)
    if effort:
        page.locator("[data-llm-effort]").select_option(effort)
    page.locator("[data-llm-api-key]").fill("sk-custom-secret")
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.locator("[data-llm-passphrase-confirm]").fill("correct horse battery")
    page.locator("[data-llm-consent]").check()
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")


def _credential_record_text(page: Page) -> str:
    return page.evaluate(
        """async () => {
          const open = indexedDB.open("chessfen-credentials", 1);
          const db = await new Promise((resolve, reject) => {
            open.onsuccess = () => resolve(open.result);
            open.onerror = () => reject(open.error);
          });
          const record = await new Promise((resolve, reject) => {
            const tx = db.transaction("credentials", "readonly");
            const req = tx.objectStore("credentials").get("llm");
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
          });
          db.close();
          return JSON.stringify(record);
        }"""
    )


def _workspace_record_text(page: Page) -> str:
    return page.evaluate(
        """async () => {
          const open = indexedDB.open("chessfen-analysis", 1);
          const db = await new Promise((resolve, reject) => {
            open.onsuccess = () => resolve(open.result);
            open.onerror = () => reject(open.error);
          });
          const record = await new Promise((resolve, reject) => {
            const tx = db.transaction("workspaces", "readonly");
            const req = tx.objectStore("workspaces").get("current");
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
          });
          db.close();
          return JSON.stringify(record);
        }"""
    )


def _single_ply_game_analysis() -> dict[str, Any]:
    return {
        "start_fen": _START_FEN,
        "evaluations": [
            {"kind": "cp", "value": 10, "text": "+0.10"},
            {"kind": "cp", "value": 10, "text": "+0.10"},
        ],
        "plies": [
            {
                "ply": 1,
                "number": 1,
                "side": "white",
                "uci": "e2e4",
                "san": "e4",
                "before_fen": _START_FEN,
                "after_fen": _AFTER_E4_FEN,
                "quality": "best",
                "cpl": 10,
                "mate_loss": False,
                "is_forced": False,
                "is_key": True,
                "key_reasons": ["quality"],
                "evaluation_before": {
                    "kind": "cp",
                    "value": 10,
                    "text": "+0.10",
                },
                "evaluation_after": {
                    "kind": "cp",
                    "value": 10,
                    "text": "+0.10",
                },
                "exact_facts": {},
                "indicators": {},
            }
        ],
        "key_plies": [1],
        "summary": {},
    }


@pytest.mark.parametrize(
    ("provider", "base_url", "model", "host", "key"),
    [
        (
            "openrouter",
            "https://openrouter.ai/api/v1",
            "openrouter-model",
            "openrouter.ai",
            "sk-or-test",
        ),
        (
            "deepseek",
            "https://api.deepseek.com",
            "deepseek-flash",
            "api.deepseek.com",
            "sk-ds-test",
        ),
        (
            "kimi",
            "https://api.moonshot.ai/v1",
            "kimi-k3",
            "api.moonshot.ai",
            "sk-kimi-test",
        ),
    ],
)
def test_browser_requires_new_provider_key_after_switch(
    browser_page: Page,
    live_analysis_url: str,
    provider: str,
    base_url: str,
    model: str,
    host: str,
    key: str,
) -> None:
    page = browser_page
    provider_requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            provider_requests.append(request.url)
            if request.url.startswith(f"https://{host}/")
            else None
        ),
    )
    _configure_mock_profile(page, live_analysis_url)
    expect(page.locator("[data-llm-credential-meta]")).to_contain_text(
        "api.openai.test"
    )

    page.locator("[data-llm-provider]").select_option(provider)
    expect(page.locator("[data-llm-base-url]")).to_have_value(base_url)
    expect(page.locator("[data-llm-consent]")).not_to_be_checked()
    expect(page.locator("[data-llm-confirm]")).not_to_be_checked()
    page.locator("[data-llm-model]").fill(model)
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.locator("[data-llm-consent]").check()
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")
    expect(page.locator("[data-llm-credential-meta]")).to_contain_text(
        "api.openai.test"
    )

    page.get_by_role("button", name="Unlock").click()
    expect(page.locator("[data-llm-status]")).to_contain_text(
        "Check the passphrase and Provider"
    )
    assert provider_requests == []

    page.locator("[data-llm-api-key]").fill(key)
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.locator("[data-llm-passphrase-confirm]").fill("correct horse battery")
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")
    expect(page.locator("[data-llm-credential-meta]")).to_contain_text(host)
    page.get_by_role("button", name="Unlock").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("unlocked")
    assert provider_requests == []


def test_browser_generates_a_selected_game_ply_explanation(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page

    def provider(route) -> None:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "summary": "这一手争夺中心。",
                                        "why": "候选变化支持中心控制。",
                                        "alternatives": "其他变化同样可行。",
                                        "uncertainty": "只基于引擎事实。",
                                    }
                                )
                            }
                        }
                    ]
                }
            ),
        )

    page.route("https://api.openai.test/**", provider)
    _configure_mock_profile(page, live_analysis_url)
    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    page.locator("#pgn").fill(
        '[Event "Explain"]\n[White "Alice"]\n[Black "Bob"]\n[Result "*"]\n\n1. e4 *\n'
    )
    page.get_by_role("button", name="Parse PGN").click()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    page.get_by_role("button", name="Start Game Analysis").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-game-moves] [data-ply='1']").click()
    expect(page.locator("[data-game-explanation-section]")).to_be_visible()
    page.locator("[data-game-explanation-passphrase]").fill(
        "correct horse battery"
    )
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-game-explanation-summary]")).to_have_text(
        "这一手争夺中心。"
    )
    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    expect(page.locator("[data-workspace-restore]")).to_be_visible()
    with page.expect_navigation():
        page.locator("[data-workspace-restore-button]").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Restored from the local Analysis Workspace."
    )
    page.locator("[data-game-moves] [data-ply='1']").click()
    expect(page.locator("[data-game-explanation-summary]")).to_have_text(
        "这一手争夺中心。"
    )
    page.unroute("https://api.openai.test/**", provider)


def test_browser_locks_the_llm_after_the_configured_idle_timeout(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.add_init_script("window.CHESSFEN_LLM_LOCK_MS = 150;")
    _configure_mock_profile(page, live_analysis_url)
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Unlock").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("unlocked")
    expect(page.locator("html")).to_have_attribute("data-llm-locked", "false")
    expect(page.locator("html")).to_have_attribute(
        "data-llm-locked", "true", timeout=3_000
    )
    expect(page.locator("[data-llm-status]")).to_contain_text("after inactivity")


def test_browser_batches_key_ply_explanations_and_summary(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    requests: list[dict[str, Any]] = []
    invalid_sent = False
    second_batch_failed = False
    summary_failed = False

    def provider(route) -> None:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        body = route.request.post_data_json
        requests.append(body)
        nonlocal invalid_sent, second_batch_failed, summary_failed
        request_payload = json.loads(body["messages"][1]["content"])
        content: dict[str, Any]
        if "targets" in request_payload:
            if not invalid_sent:
                invalid_sent = True
                content = {"unexpected": True}
            elif len(request_payload["targets"]) == 1 and not second_batch_failed:
                second_batch_failed = True
                route.fulfill(status=500, headers=headers)
                return
            else:
                explanations = [
                    {
                        "ply": target["ply"],
                        "summary": f"Key {target['ply']}",
                        "why": "facts",
                        "alternatives": "lines",
                        "uncertainty": "none",
                    }
                    for target in request_payload["targets"]
                ]
                content = {"explanations": explanations}
        elif not summary_failed:
            summary_failed = True
            route.fulfill(status=500, headers=headers)
            return
        else:
            content = {"summary": "Overall", "uncertainty": "limited"}
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "choices": [
                        {"message": {"content": json.dumps(content)}}
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "completion_tokens_details": {"reasoning_tokens": 2},
                        "total_tokens": 15,
                    },
                }
            ),
        )

    moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6"]
    plies = [
        {
            "ply": index + 1,
            "number": index // 2 + 1,
            "side": "white" if index % 2 == 0 else "black",
            "uci": uci,
            "san": san,
            "before_fen": _START_FEN,
            "after_fen": _AFTER_E4_FEN,
            "quality": "best",
            "cpl": 10,
            "mate_loss": False,
            "is_forced": False,
            "is_key": True,
            "key_reasons": ["quality"],
            "evaluation_before": {"kind": "cp", "value": 10, "text": "+0.10"},
            "evaluation_after": {"kind": "cp", "value": 10, "text": "+0.10"},
            "exact_facts": {},
            "indicators": {},
        }
        for index, (uci, san) in enumerate(
            zip(moves, ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"])
        )
    ]
    fake_analysis = {
        "start_fen": _START_FEN,
        "evaluations": [
            {"kind": "cp", "value": 10, "text": "+0.10"} for _ in range(7)
        ],
        "plies": plies,
        "key_plies": [1, 2, 3, 4, 5, 6],
        "summary": {},
    }

    def game_results(route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(fake_analysis),
        )

    page.route("https://api.openai.test/**", provider)
    page.route("**/tools/analysis/game/results", game_results)
    _configure_mock_profile(page, live_analysis_url)
    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    page.locator("#pgn").fill(
        '[Event "Batch"]\n[White "A"]\n[Black "B"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *'
    )
    page.get_by_role("button", name="Parse PGN").click()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    page.get_by_role("button", name="Start Game Analysis").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=60_000
    )
    page.locator("[data-game-batch-preview]").click()
    expect(page.locator("[data-game-batch-plan]")).to_contain_text(
        "6 Key Plies · 2 batch requests + 1 summary request"
    )
    page.locator("[data-game-explanation-passphrase]").fill(
        "correct horse battery"
    )
    page.get_by_role("button", name="Confirm and Generate").click()
    expect(page.locator("[data-game-batch-status]")).to_contain_text(
        "batch failed", timeout=30_000
    )
    expect(page.locator("[data-game-batch-retry]")).to_be_visible()
    page.get_by_role("button", name="Retry failed batch").click()
    expect(page.locator("[data-game-batch-status]")).to_contain_text(
        "summary failed", timeout=30_000
    )
    expect(page.locator("[data-game-summary-retry]")).to_be_visible()
    with page.expect_download() as download_info:
        page.locator("[data-game-export-markdown]").click()
    partial_markdown = Path(download_info.value.path()).read_text(
        encoding="utf-8"
    )
    assert "## Key Ply explanations" in partial_markdown
    assert "## Overall summary" not in partial_markdown
    page.get_by_role("button", name="Retry summary").click()
    expect(page.locator("[data-game-overall-summary]")).to_be_visible()
    expect(page.locator("[data-game-summary-text]")).to_have_text("Overall")
    expect(page.locator("[data-game-token-usage]")).to_contain_text(
        "total 45"
    )
    assert len(requests) == 6
    payloads = [json.loads(item["messages"][1]["content"]) for item in requests]
    assert [len(p.get("targets", [])) for p in payloads] == [5, 5, 1, 1, 0, 0]
    with page.expect_download() as download_info:
        page.locator("[data-game-export-markdown]").click()
    markdown = Path(download_info.value.path()).read_text(encoding="utf-8")
    assert "## Overall summary" in markdown
    assert "**Summary:** Overall" in markdown
    assert "**Provider:** openai" in markdown
    assert "**Model:** mock-model" in markdown


def test_browser_generates_a_position_overall_summary(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page

    def provider(route) -> None:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "summary": "局面总结",
                                        "uncertainty": "依据有限",
                                    }
                                )
                            }
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 4,
                        "completion_tokens": 3,
                        "completion_tokens_details": {"reasoning_tokens": 1},
                        "total_tokens": 7,
                    },
                }
            ),
        )

    page.route("https://api.openai.test/**", provider)
    _configure_mock_profile(page, live_analysis_url)
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-position-summary-preview]").click()
    expect(page.locator("[data-position-summary-plan]")).to_contain_text(
        "1 overall Position summary request"
    )
    page.locator("[data-explanation-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Confirm Summary Request").click()
    expect(page.locator("[data-position-summary-text]")).to_have_text("局面总结")
    expect(page.locator("[data-position-summary-usage]")).to_contain_text(
        "total 7"
    )
    with page.expect_download() as download_info:
        page.locator("[data-position-export-markdown]").click()
    markdown = Path(download_info.value.path()).read_text(encoding="utf-8")
    assert "## Position summary" in markdown
    assert "**Summary:** 局面总结" in markdown
    assert "**Provider:** openai" in markdown
    assert "**Model:** mock-model" in markdown
    page.unroute("https://api.openai.test/**", provider)


def test_browser_openrouter_requires_reasoning_capabilities_and_routes_directly(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    chat_headers: list[dict[str, str]] = []
    chat_bodies: list[dict[str, Any]] = []
    chat_attempts: list[str] = []
    fail_mode = {"mode": ""}
    same_origin_posts: list[str] = []
    page.on(
        "request",
        lambda request: (
            same_origin_posts.append(request.url)
            if request.method == "POST"
            and request.url.startswith(live_analysis_url)
            else None
        ),
    )

    def provider(route) -> None:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        if route.request.method == "OPTIONS":
            if fail_mode["mode"] == "cors":
                route.fulfill(
                    status=403, headers={"Content-Type": "text/plain"}
                )
                return
            route.fulfill(status=204, headers=headers)
            return
        if route.request.url.endswith("/models"):
            route.fulfill(
                status=200,
                headers={**headers, "Content-Type": "application/json"},
                body=json.dumps(
                    {
                        "data": [
                            {
                                "id": "openrouter-model",
                                "supported_parameters": [
                                    "reasoning",
                                    "reasoning_effort",
                                ],
                                "reasoning": {
                                    "supported_efforts": ["max", "high"]
                                },
                            }
                        ]
                    }
                ),
            )
            return
        chat_attempts.append(route.request.url)
        if fail_mode["mode"] == "abort":
            route.abort("failed")
            return
        if fail_mode["mode"] == "cors":
            route.abort("blockedbyresponse")
            return
        if fail_mode["mode"] == "routing":
            route.fulfill(
                status=400,
                headers={**headers, "Content-Type": "application/json"},
                body=json.dumps(
                    {
                        "error": {
                            "code": 400,
                            "message": "No endpoints support requested parameters",
                        }
                    }
                ),
            )
            return
        if fail_mode["mode"] == "malformed":
            route.fulfill(
                status=200,
                headers={**headers, "Content-Type": "application/json"},
                body=json.dumps(
                    {"choices": [{"message": {"content": "{not-json"}}]}
                ),
            )
            return
        chat_headers.append(
            {key.lower(): value for key, value in route.request.headers.items()}
        )
        chat_bodies.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "summary": "OpenRouter summary",
                                        "why": "facts",
                                        "alternatives": "lines",
                                        "uncertainty": "none",
                                    }
                                ),
                                "reasoning": "SECRET-OPENROUTER-REASONING",
                            }
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "completion_tokens_details": {"reasoning_tokens": 1},
                        "total_tokens": 5,
                    },
                }
            ),
        )

    page.route("https://openrouter.ai/**", provider)
    _configure_openrouter_profile(page, live_analysis_url)
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Fetch Models").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("Loaded 1 Models")
    preview = page.locator("[data-llm-preview]").inner_text()
    assert '"exclude": true' in preview
    assert '"require_parameters": true' in preview
    assert '"max_tokens": 16384' in preview
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")

    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-explanation-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-explanation-summary]")).to_have_text(
        "OpenRouter summary"
    )
    body = chat_bodies[-1]
    assert body["reasoning"] == {
        "enabled": True,
        "effort": "max",
        "exclude": True,
    }
    assert body["provider"] == {"require_parameters": True}
    assert body["max_tokens"] == 16384
    headers = chat_headers[-1]
    assert "http-referer" not in headers
    assert "x-openrouter-title" not in headers
    assert "x-title" not in headers
    expect(page.locator("[data-explanation-usage]")).to_contain_text("total 5")
    expect(page.locator("[data-explanation-usage]")).to_contain_text(
        "reasoning 1"
    )

    page.locator("[data-position-summary-preview]").click()
    expect(page.locator("[data-position-summary-plan]")).to_contain_text(
        "1 overall Position summary request"
    )
    page.locator("[data-position-summary-confirm]").click()
    expect(page.locator("[data-position-summary-text]")).to_have_text(
        "OpenRouter summary"
    )
    expect(page.locator("[data-position-summary-usage]")).to_contain_text(
        "total 5"
    )
    expect(page.locator("[data-position-summary-usage]")).to_contain_text(
        "reasoning 1"
    )
    summary_body = chat_bodies[-1]
    assert summary_body["reasoning"] == {
        "enabled": True,
        "effort": "max",
        "exclude": True,
    }
    assert summary_body["provider"] == {"require_parameters": True}
    assert summary_body["max_tokens"] == 16384

    assert "SECRET-OPENROUTER-REASONING" not in page.content()
    local_storage = page.evaluate("() => JSON.stringify(localStorage)")
    assert "SECRET-OPENROUTER-REASONING" not in local_storage
    workspace = _workspace_record_text(page)
    assert "SECRET-OPENROUTER-REASONING" not in workspace
    assert "OpenRouter summary" in workspace

    before_posts = len(same_origin_posts)
    before_bodies = len(chat_bodies)
    before_attempts = len(chat_attempts)
    expected_status = {
        "routing": "Provider rejected the request",
        "malformed": "could not be generated",
        "cors": "could not be reached from this browser",
        "abort": "could not be reached from this browser",
    }
    for index, mode in enumerate(("routing", "malformed", "cors", "abort")):
        fail_mode["mode"] = mode
        page.get_by_role("button", name="Generate Explanation").click()
        expect(page.locator("[data-explanation-status]")).to_contain_text(
            expected_status[mode]
        )
        expect(page.locator("[data-explanation-summary]")).to_have_text(
            "OpenRouter summary"
        )
        expect(page.locator("[data-position-summary-text]")).to_have_text(
            "OpenRouter summary"
        )
        assert len(chat_attempts) == before_attempts + index + 1
        assert len(chat_bodies) == before_bodies
        assert len(same_origin_posts) == before_posts
    page.unroute("https://openrouter.ai/**", provider)


def test_browser_blocks_openrouter_without_confirmed_reasoning_capabilities(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    chat_calls: list[str] = []

    def provider(route) -> None:
        chat_calls.append(route.request.url)
        route.fulfill(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Content-Type": "application/json",
            },
            body="{}",
        )

    page.route("https://openrouter.ai/**", provider)
    page.goto(f"{live_analysis_url}/settings")
    page.locator("[data-llm-provider]").select_option("openrouter")
    page.locator("[data-llm-model]").fill("unknown-model")
    page.locator("[data-llm-api-key]").fill("sk-or-test")
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.locator("[data-llm-passphrase-confirm]").fill("correct horse battery")
    page.locator("[data-llm-consent]").check()
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")

    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-explanation-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-explanation-status]")).to_contain_text(
        "Fetch Models to confirm Reasoning capabilities"
    )
    assert chat_calls == []
    page.unroute("https://openrouter.ai/**", provider)


def test_browser_recovers_openrouter_unsupported_effort_and_persists_capabilities(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page

    def provider(route) -> None:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "data": [
                        {
                            "id": "model-max",
                            "supported_parameters": ["reasoning"],
                            "reasoning": {"supported_efforts": ["max", "high"]},
                        },
                        {
                            "id": "model-high-only",
                            "supported_parameters": ["reasoning"],
                            "reasoning": {"supported_efforts": ["high"]},
                        },
                    ]
                }
            ),
        )

    page.route("https://openrouter.ai/**", provider)
    page.goto(f"{live_analysis_url}/settings")
    page.locator("[data-llm-provider]").select_option("openrouter")
    page.locator("[data-llm-model]").fill("model-high-only")
    page.locator("[data-llm-api-key]").fill("sk-or-test")
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.locator("[data-llm-passphrase-confirm]").fill("correct horse battery")
    page.locator("[data-llm-consent]").check()
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")

    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Fetch Models").click()
    expect(page.locator("[data-llm-capabilities]")).to_contain_text("high")
    expect(page.locator("[data-llm-capabilities]")).to_contain_text(
        "not supported"
    )
    expect(page.locator("[data-llm-effort] option[value='max']")).to_be_disabled()
    page.locator("[data-llm-effort]").select_option("high")
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")

    page.reload()
    expect(page.locator("[data-llm-model]")).to_have_value("model-high-only")
    expect(page.locator("[data-llm-effort]")).to_have_value("high")
    expect(page.locator("[data-llm-effort] option[value='max']")).to_be_disabled()
    expect(page.locator("[data-llm-capabilities]")).to_contain_text("high")
    page.unroute("https://openrouter.ai/**", provider)


def test_browser_openrouter_batches_and_summarizes_game_with_malformed_retry(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    requests: list[dict[str, Any]] = []
    batch_attempts = 0

    def provider(route) -> None:
        nonlocal batch_attempts
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        if route.request.url.endswith("/models"):
            route.fulfill(
                status=200,
                headers={**headers, "Content-Type": "application/json"},
                body=json.dumps(
                    {
                        "data": [
                            {
                                "id": "openrouter-model",
                                "supported_parameters": ["reasoning"],
                                "reasoning": {
                                    "supported_efforts": ["max", "high"]
                                },
                            }
                        ]
                    }
                ),
            )
            return
        body = route.request.post_data_json
        requests.append(body)
        system = body["messages"][0]["content"]
        reasoning = ""
        usage = None
        if system.startswith("Explain chess analysis"):
            content = json.dumps(
                {
                    "summary": "OpenRouter manual ply",
                    "why": "engine facts",
                    "alternatives": "candidate lines",
                    "uncertainty": "limited",
                }
            )
            reasoning = "SECRET-MANUAL-REASONING"
            usage = {
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "completion_tokens_details": {"reasoning_tokens": 1},
                "total_tokens": 5,
            }
        else:
            payload = json.loads(body["messages"][1]["content"])
            if "targets" in payload:
                batch_attempts += 1
                if batch_attempts == 1:
                    content = "{not-json"
                else:
                    content = json.dumps(
                        {
                            "explanations": [
                                {
                                    "ply": target["ply"],
                                    "summary": "OpenRouter key ply",
                                    "why": "engine facts",
                                    "alternatives": "candidate lines",
                                    "uncertainty": "limited",
                                }
                                for target in payload["targets"]
                            ]
                        }
                    )
                    reasoning = "SECRET-BATCH-REASONING"
                usage = (
                    {
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "completion_tokens_details": {
                            "reasoning_tokens": 1
                        },
                        "total_tokens": 5,
                    }
                    if batch_attempts > 1
                    else None
                )
            else:
                content = json.dumps(
                    {
                        "summary": "OpenRouter game summary",
                        "uncertainty": "limited",
                    }
                )
                reasoning = "SECRET-SUMMARY-REASONING"
                usage = {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "completion_tokens_details": {"reasoning_tokens": 1},
                    "total_tokens": 5,
                }
        message = {"content": content}
        if reasoning:
            message["reasoning"] = reasoning
        response: dict[str, Any] = {"choices": [{"message": message}]}
        if usage is not None:
            response["usage"] = usage
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(response),
        )

    fake_analysis = _single_ply_game_analysis()

    def game_results(route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(fake_analysis),
        )

    page.route("https://openrouter.ai/**", provider)
    page.route("**/tools/analysis/game/results", game_results)
    _configure_openrouter_profile(page, live_analysis_url)
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Fetch Models").click()
    expect(page.locator("[data-llm-capabilities]")).to_contain_text("high")

    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    page.locator("#pgn").fill(
        '[Event "OpenRouter"]\n[White "A"]\n[Black "B"]\n[Result "*"]\n\n1. e4 *'
    )
    page.get_by_role("button", name="Parse PGN").click()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    page.get_by_role("button", name="Start Game Analysis").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-game-moves] [data-ply='1']").click()
    page.locator("[data-game-explanation-passphrase]").fill(
        "correct horse battery"
    )
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-game-explanation-summary]")).to_have_text(
        "OpenRouter manual ply"
    )
    expect(page.locator("[data-game-explanation-usage]")).to_contain_text(
        "total 5"
    )
    expect(page.locator("[data-game-explanation-usage]")).to_contain_text(
        "reasoning 1"
    )

    page.locator("[data-game-batch-preview]").click()
    expect(page.locator("[data-game-batch-plan]")).to_contain_text(
        "1 Key Plies · 1 batch requests + 1 summary request"
    )
    page.locator("[data-game-explanation-passphrase]").fill(
        "correct horse battery"
    )
    page.get_by_role("button", name="Confirm and Generate").click()
    expect(page.locator("[data-game-batch-status]")).to_contain_text(
        "Key Ply explanations and summary complete.", timeout=30_000
    )
    expect(page.locator("[data-game-summary-text]")).to_have_text(
        "OpenRouter game summary"
    )
    expect(page.locator("[data-game-token-usage]")).to_contain_text("total 10")
    expect(page.locator("[data-game-token-usage]")).to_contain_text(
        "reasoning 2"
    )
    assert batch_attempts == 2
    assert len(requests) == 4
    assert requests[0]["messages"][0]["content"].startswith(
        "Explain chess analysis"
    )
    manual_payload = json.loads(
        requests[0]["messages"][1]["content"].split("\n", 1)[1]
    )
    assert manual_payload["quality"] == "best"
    assert manual_payload["evaluation_before"]["value"] == 10
    payloads = [
        json.loads(item["messages"][1]["content"]) for item in requests[1:]
    ]
    assert [len(payload.get("targets", [])) for payload in payloads] == [1, 1, 0]
    target = payloads[-2]["targets"][0]
    assert target["quality"] == "best"
    assert target["evaluation_before"]["value"] == 10
    assert "key_plies" in payloads[-1]
    for body in requests:
        assert body["reasoning"] == {
            "enabled": True,
            "effort": "max",
            "exclude": True,
        }
        assert body["provider"] == {"require_parameters": True}
        assert body["max_tokens"] == 16384
    page.locator("[data-game-moves] [data-ply='1']").click()
    expect(page.locator("[data-game-explanation-summary]")).to_have_text(
        "OpenRouter key ply"
    )
    assert "SECRET-MANUAL-REASONING" not in page.content()
    assert "SECRET-BATCH-REASONING" not in page.content()
    assert "SECRET-SUMMARY-REASONING" not in page.content()
    workspace = _workspace_record_text(page)
    for secret in (
        "SECRET-MANUAL-REASONING",
        "SECRET-BATCH-REASONING",
        "SECRET-SUMMARY-REASONING",
    ):
        assert secret not in workspace
    assert "OpenRouter game summary" in workspace
    page.unroute("https://openrouter.ai/**", provider)
    page.unroute("**/tools/analysis/game/results", game_results)


def test_browser_deepseek_preset_serializes_reasoning_usage_and_failures(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    chat_bodies: list[dict[str, Any]] = []
    chat_headers: list[dict[str, str]] = []
    chat_attempts: list[str] = []
    provider_requests: list[str] = []
    same_origin_posts: list[str] = []
    fail_mode = {"value": ""}
    page.on(
        "request",
        lambda request: (
            same_origin_posts.append(request.url)
            if request.method == "POST"
            and request.url.startswith(live_analysis_url)
            else None
        ),
    )

    def provider(route) -> None:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        provider_requests.append(route.request.url)
        if route.request.method == "OPTIONS":
            if fail_mode["value"] == "cors":
                route.fulfill(
                    status=403, headers={"Content-Type": "text/plain"}
                )
                return
            route.fulfill(status=204, headers=headers)
            return
        if route.request.url.endswith("/models"):
            route.fulfill(
                status=200,
                headers={**headers, "Content-Type": "application/json"},
                body=json.dumps(
                    {
                        "data": [
                            {"id": "deepseek-flash"},
                            {"id": "deepseek-v4-pro"},
                        ]
                    }
                ),
            )
            return
        chat_attempts.append(route.request.url)
        chat_headers.append(
            {key.lower(): value for key, value in route.request.headers.items()}
        )
        mode = fail_mode["value"]
        if mode == "abort":
            route.abort("failed")
            return
        if mode == "cors":
            route.abort("blockedbyresponse")
            return
        content = json.dumps(
            {
                "summary": "DeepSeek summary",
                "why": "engine facts",
                "alternatives": "candidate lines",
                "uncertainty": "limited",
            }
        )
        finish_reason = "stop"
        if mode == "malformed":
            content = "{not-json"
        elif mode == "empty":
            content = ""
        elif mode == "length":
            content = '{"summary":"partial"'
            finish_reason = "length"
        elif mode == "incomplete":
            content = ""
            finish_reason = "insufficient_system_resource"
        else:
            chat_bodies.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": finish_reason,
                            "message": {
                                "content": content,
                                "reasoning_content": (
                                    "SECRET-DEEPSEEK-REASONING"
                                ),
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 6,
                        "completion_tokens_details": {"reasoning_tokens": 4},
                        "total_tokens": 9,
                    },
                }
            ),
        )

    page.route("https://api.deepseek.com/**", provider)
    _configure_deepseek_profile(page, live_analysis_url)
    expect(page.locator("[data-llm-base-url]")).to_have_value(
        "https://api.deepseek.com"
    )
    expect(page.locator("[data-llm-compatibility]")).to_be_visible()
    expect(page.locator("[data-llm-compatibility]")).to_contain_text(
        "unverified"
    )
    assert chat_attempts == []
    assert provider_requests == []

    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Fetch Models").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("Loaded 2 Models")
    expect(page.locator("[data-llm-capabilities]")).to_contain_text(
        "low, high, max"
    )
    expect(page.locator("[data-llm-effort] option[value='xhigh']")).to_be_disabled()
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview["thinking"] == {"type": "enabled"}
    assert preview["reasoning_effort"] == "max"
    assert preview["max_tokens"] == 16384
    assert "max_completion_tokens" not in preview
    page.get_by_role("button", name="Test Connection").click()
    expect(page.locator("[data-llm-status]")).to_contain_text(
        "Connection test succeeded"
    )
    assert chat_attempts == []

    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-explanation-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-explanation-summary]")).to_have_text(
        "DeepSeek summary"
    )
    body = chat_bodies[-1]
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "max"
    assert body["max_tokens"] == 16384
    assert "max_completion_tokens" not in body
    assert body["response_format"] == {"type": "json_object"}
    assert chat_headers[0]["authorization"] == "Bearer sk-ds-test"
    expect(page.locator("[data-explanation-usage]")).to_contain_text(
        "completion 6"
    )
    expect(page.locator("[data-explanation-usage]")).to_contain_text(
        "reasoning 4"
    )
    expect(page.locator("[data-explanation-usage]")).to_contain_text("total 9")
    assert "SECRET-DEEPSEEK-REASONING" not in page.content()
    workspace = _workspace_record_text(page)
    assert "SECRET-DEEPSEEK-REASONING" not in workspace
    assert "DeepSeek summary" in workspace

    before_attempts = len(chat_attempts)
    before_bodies = len(chat_bodies)
    before_posts = len(same_origin_posts)
    for index, mode in enumerate(
        ("malformed", "empty", "length", "incomplete", "cors", "abort")
    ):
        fail_mode["value"] = mode
        page.get_by_role("button", name="Generate Explanation").click()
        expect(page.locator("[data-explanation-status]")).to_contain_text(
            "could not be reached from this browser"
            if mode in {"cors", "abort"}
            else "could not be generated"
        )
        expect(page.locator("[data-explanation-summary]")).to_have_text(
            "DeepSeek summary"
        )
        assert len(chat_attempts) == before_attempts + index + 1
        assert len(chat_bodies) == before_bodies
        assert len(same_origin_posts) == before_posts
    page.unroute("https://api.deepseek.com/**", provider)


def test_browser_deepseek_disables_optional_reasoning_and_blocks_unverified_off(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    chat_bodies: list[dict[str, Any]] = []
    same_origin_posts: list[str] = []
    page.on(
        "request",
        lambda request: (
            same_origin_posts.append(request.url)
            if request.method == "POST"
            and request.url.startswith(live_analysis_url)
            else None
        ),
    )

    def provider(route) -> None:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        if route.request.url.endswith("/models"):
            route.fulfill(
                status=200,
                headers={**headers, "Content-Type": "application/json"},
                body=json.dumps({"data": [{"id": "deepseek-flash"}]}),
            )
            return
        chat_bodies.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json.dumps(
                                    {
                                        "summary": "DeepSeek no-thinking summary",
                                        "why": "engine facts",
                                        "alternatives": "candidate lines",
                                        "uncertainty": "limited",
                                    }
                                ),
                                "reasoning_content": None,
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    },
                }
            ),
        )

    page.route("https://api.deepseek.com/**", provider)
    _configure_deepseek_profile(
        page, live_analysis_url, "deepseek-flash", reasoning=False
    )
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in preview

    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-explanation-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-explanation-summary]")).to_have_text(
        "DeepSeek no-thinking summary"
    )
    body = chat_bodies[-1]
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body
    assert body["max_tokens"] == 16384

    before_chats = len(chat_bodies)
    before_posts = len(same_origin_posts)
    page.goto(f"{live_analysis_url}/settings")
    page.locator("[data-llm-model]").fill("deepseek-future")
    expect(page.locator("[data-llm-capabilities]")).to_contain_text(
        "cannot be disabled for this unverified Model"
    )
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")

    page.goto(f"{live_analysis_url}/tools/analysis")
    expect(page.locator("[data-analysis-status]")).to_contain_text("Restored")
    page.locator("[data-explanation-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-explanation-status]")).to_contain_text(
        "cannot be disabled for this unverified Model"
    )
    assert len(chat_bodies) == before_chats
    assert len(same_origin_posts) == before_posts
    page.unroute("https://api.deepseek.com/**", provider)


def test_browser_deepseek_batches_summarizes_and_restores_game(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    requests: list[dict[str, Any]] = []

    def provider(route) -> None:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        if route.request.url.endswith("/models"):
            route.fulfill(
                status=200,
                headers={**headers, "Content-Type": "application/json"},
                body=json.dumps({"data": [{"id": "deepseek-flash"}]}),
            )
            return
        body = route.request.post_data_json
        requests.append(body)
        payload = json.loads(body["messages"][1]["content"])
        if "targets" in payload:
            content = json.dumps(
                {
                    "explanations": [
                        {
                            "ply": target["ply"],
                            "summary": "DeepSeek key ply",
                            "why": "engine facts",
                            "alternatives": "candidate lines",
                            "uncertainty": "limited",
                        }
                        for target in payload["targets"]
                    ]
                }
            )
            reasoning = "SECRET-DEEPSEEK-BATCH-REASONING"
        else:
            content = json.dumps(
                {
                    "summary": "DeepSeek game summary",
                    "uncertainty": "limited",
                }
            )
            reasoning = "SECRET-DEEPSEEK-SUMMARY-REASONING"
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": content,
                                "reasoning_content": reasoning,
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 6,
                        "completion_tokens_details": {"reasoning_tokens": 4},
                        "total_tokens": 9,
                    },
                }
            ),
        )

    fake_analysis = _single_ply_game_analysis()

    def game_results(route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(fake_analysis),
        )

    page.route("https://api.deepseek.com/**", provider)
    page.route("**/tools/analysis/game/results", game_results)
    _configure_deepseek_profile(page, live_analysis_url)
    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    page.locator("#pgn").fill(
        '[Event "DeepSeek"]\n[White "A"]\n[Black "B"]\n[Result "*"]\n\n1. e4 *'
    )
    page.get_by_role("button", name="Parse PGN").click()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    page.get_by_role("button", name="Start Game Analysis").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-game-batch-preview]").click()
    page.locator("[data-game-explanation-passphrase]").fill(
        "correct horse battery"
    )
    page.get_by_role("button", name="Confirm and Generate").click()
    expect(page.locator("[data-game-batch-status]")).to_contain_text(
        "Key Ply explanations and summary complete.", timeout=30_000
    )
    expect(page.locator("[data-game-summary-text]")).to_have_text(
        "DeepSeek game summary"
    )
    expect(page.locator("[data-game-token-usage]")).to_contain_text("total 18")
    expect(page.locator("[data-game-token-usage]")).to_contain_text(
        "reasoning 8"
    )
    assert len(requests) == 2
    payloads = [json.loads(item["messages"][1]["content"]) for item in requests]
    assert len(payloads[0]["targets"]) == 1
    assert payloads[0]["targets"][0]["quality"] == "best"
    assert payloads[0]["targets"][0]["evaluation_before"]["value"] == 10
    assert "key_plies" in payloads[1]
    for body in requests:
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "max"
        assert body["max_tokens"] == 16384
        assert "max_completion_tokens" not in body
    assert "SECRET-DEEPSEEK-BATCH-REASONING" not in page.content()
    assert "SECRET-DEEPSEEK-SUMMARY-REASONING" not in page.content()
    workspace = _workspace_record_text(page)
    assert "SECRET-DEEPSEEK-BATCH-REASONING" not in workspace
    assert "SECRET-DEEPSEEK-SUMMARY-REASONING" not in workspace
    assert "DeepSeek game summary" in workspace

    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    expect(page.locator("[data-workspace-restore]")).to_be_visible()
    with page.expect_navigation():
        page.locator("[data-workspace-restore-button]").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Restored from the local Analysis Workspace."
    )
    page.locator("[data-game-moves] [data-ply='1']").click()
    expect(page.locator("[data-game-explanation-summary]")).to_have_text(
        "DeepSeek key ply"
    )
    page.evaluate(
        """() => {
          const key = "chessfen.analysis.llm.profile.v1";
          const profile = JSON.parse(localStorage.getItem(key));
          profile.audience = "beginner";
          localStorage.setItem(key, JSON.stringify(profile));
        }"""
    )
    page.locator("[data-game-moves] [data-ply='1']").click()
    expect(page.locator("[data-game-explanation-stale]")).to_be_visible()
    page.unroute("https://api.deepseek.com/**", provider)
    page.unroute("**/tools/analysis/game/results", game_results)


def test_browser_deepseek_blocks_unsupported_reasoning_effort(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/settings")
    result = page.evaluate(
        """async () => {
          const module = await import("/static/js/analysis-llm.js");
          return module.providerRequestParameters({
            provider: "deepseek",
            model: "deepseek-flash",
            reasoningEnabled: true,
            reasoningEffort: "xhigh",
            maxTokens: 16384,
          });
        }"""
    )
    assert result == {"ok": False, "code": "reasoning_effort_unsupported"}


def test_browser_kimi_preset_regions_and_k3_request_contract(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    provider_requests: list[str] = []
    chat_bodies: list[dict[str, Any]] = []
    chat_headers: list[dict[str, str]] = []
    chat_attempts: list[str] = []
    same_origin_posts: list[str] = []
    fail_mode = {"value": ""}
    page.on(
        "request",
        lambda request: (
            same_origin_posts.append(request.url)
            if request.method == "POST"
            and request.url.startswith(live_analysis_url)
            else None
        ),
    )

    def provider(route) -> None:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        provider_requests.append(route.request.url)
        if route.request.method == "OPTIONS":
            if fail_mode["value"] == "cors":
                route.fulfill(
                    status=403, headers={"Content-Type": "text/plain"}
                )
                return
            route.fulfill(status=204, headers=headers)
            return
        if route.request.url.endswith("/models"):
            route.fulfill(
                status=200,
                headers={**headers, "Content-Type": "application/json"},
                body=json.dumps(
                    {
                        "data": [
                            {"id": "kimi-k3", "supports_reasoning": True},
                            {"id": "kimi-k3.1-pro", "supports_reasoning": True},
                            {"id": "kimi-k2.6", "supports_reasoning": True},
                        ]
                    }
                ),
            )
            return
        chat_attempts.append(route.request.url)
        chat_headers.append(
            {key.lower(): value for key, value in route.request.headers.items()}
        )
        mode = fail_mode["value"]
        if mode == "abort":
            route.abort("failed")
            return
        if mode == "cors":
            route.abort("blockedbyresponse")
            return
        content = json.dumps(
            {
                "summary": "Kimi summary",
                "why": "engine facts",
                "alternatives": "candidate lines",
                "uncertainty": "limited",
            }
        )
        finish_reason = "stop"
        if mode == "malformed":
            content = "{not-json"
        elif mode == "empty":
            content = ""
        elif mode == "length":
            content = '{"summary":"partial"'
            finish_reason = "length"
        else:
            chat_bodies.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": finish_reason,
                            "message": {
                                "content": content,
                                "reasoning_content": "SECRET-KIMI-REASONING",
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 6,
                        "completion_tokens_details": {"reasoning_tokens": 4},
                        "total_tokens": 9,
                    },
                }
            ),
        )

    page.route("https://api.moonshot.ai/**", provider)
    page.route("https://api.moonshot.cn/**", provider)
    _configure_kimi_profile(page, live_analysis_url, "cn", "kimi-k3")
    expect(page.locator("[data-llm-base-url]")).to_have_value(
        "https://api.moonshot.cn/v1"
    )
    expect(page.locator("[data-kimi-region-field]")).to_be_visible()
    expect(page.locator("[data-llm-compatibility]")).to_contain_text(
        "unverified"
    )
    assert chat_attempts == []
    assert provider_requests == []

    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Fetch Models").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("Loaded 3 Models")
    expect(page.locator("[data-llm-capabilities]")).to_contain_text(
        "low, high, max"
    )
    expect(page.locator("[data-llm-reasoning]")).to_be_checked()
    expect(page.locator("[data-llm-reasoning]")).to_be_disabled()
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview["reasoning_effort"] == "max"
    assert "thinking" not in preview
    assert preview["max_completion_tokens"] == 16384
    assert "max_tokens" not in preview
    page.get_by_role("button", name="Test Connection").click()
    expect(page.locator("[data-llm-status]")).to_contain_text(
        "Connection test succeeded"
    )
    assert chat_attempts == []

    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-explanation-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-explanation-summary]")).to_have_text(
        "Kimi summary"
    )
    body = chat_bodies[-1]
    assert body["reasoning_effort"] == "max"
    assert "thinking" not in body
    assert body["max_completion_tokens"] == 16384
    assert "max_tokens" not in body
    assert body["response_format"] == {"type": "json_object"}
    assert chat_headers[0]["authorization"] == "Bearer sk-kimi-test"
    expect(page.locator("[data-explanation-usage]")).to_contain_text("total 9")
    expect(page.locator("[data-explanation-usage]")).to_contain_text(
        "reasoning 4"
    )
    assert "SECRET-KIMI-REASONING" not in page.content()
    assert "SECRET-KIMI-REASONING" not in _workspace_record_text(page)

    before_attempts = len(chat_attempts)
    before_bodies = len(chat_bodies)
    before_posts = len(same_origin_posts)
    for index, mode in enumerate(("malformed", "empty", "length", "cors", "abort")):
        fail_mode["value"] = mode
        page.get_by_role("button", name="Generate Explanation").click()
        expect(page.locator("[data-explanation-status]")).to_contain_text(
            "could not be reached from this browser"
            if mode in {"cors", "abort"}
            else "could not be generated"
        )
        expect(page.locator("[data-explanation-summary]")).to_have_text(
            "Kimi summary"
        )
        assert len(chat_attempts) == before_attempts + index + 1
        assert len(chat_bodies) == before_bodies
        assert len(same_origin_posts) == before_posts
    page.unroute("https://api.moonshot.ai/**", provider)
    page.unroute("https://api.moonshot.cn/**", provider)


def test_browser_kimi_model_contracts_and_unknown_block(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page

    def provider(route) -> None:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "data": [
                        {
                            "id": "kimi-future",
                            "supports_reasoning": True,
                        }
                    ]
                }
            ),
        )

    page.route("https://api.moonshot.ai/**", provider)
    _configure_kimi_profile(page, live_analysis_url, "international", "kimi-k2.6")
    expect(page.locator("[data-llm-effort]")).to_be_disabled()
    expect(page.locator("[data-llm-capabilities]")).to_contain_text(
        "not applicable"
    )
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in preview
    assert preview["max_completion_tokens"] == 16384

    page.locator("[data-llm-reasoning]").uncheck()
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in preview

    page.locator("[data-llm-model]").fill("kimi-k3")
    expect(page.locator("[data-llm-reasoning]")).to_be_checked()
    expect(page.locator("[data-llm-reasoning]")).to_be_disabled()
    expect(page.locator("[data-llm-effort]")).to_be_enabled()
    expect(page.locator("[data-llm-capabilities]")).to_contain_text(
        "always enabled"
    )
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview["reasoning_effort"] == "max"
    assert "thinking" not in preview

    page.locator("[data-llm-model]").fill("kimi-k2.7-code")
    expect(page.locator("[data-llm-reasoning]")).to_be_checked()
    expect(page.locator("[data-llm-reasoning]")).to_be_disabled()
    expect(page.locator("[data-llm-effort]")).to_be_disabled()
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert "thinking" not in preview
    assert "reasoning_effort" not in preview
    assert preview["max_completion_tokens"] == 16384

    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Fetch Models").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("Loaded 1 Models")
    page.locator("[data-llm-model]").fill("kimi-future")
    expect(page.locator("[data-llm-capabilities]")).to_contain_text(
        "not in the verified capability matrix"
    )
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview == {"error": "kimi_model_unknown"}

    contracts = page.evaluate(
        """async () => {
          const module = await import("/static/js/analysis-llm.js");
          const base = {
            provider: "kimi",
            maxTokens: 16384,
          };
          return {
            k3: module.providerRequestParameters({
              ...base,
              model: "kimi-k3",
              reasoningEnabled: true,
              reasoningEffort: "max",
            }),
            k31: module.providerRequestParameters({
              ...base,
              model: "kimi-k3.1-pro",
              reasoningEnabled: true,
              reasoningEffort: "max",
            }),
            k31High: module.providerRequestParameters({
              ...base,
              model: "kimi-k3.1-pro-highspeed",
              reasoningEnabled: true,
              reasoningEffort: "max",
            }),
            k27High: module.providerRequestParameters({
              ...base,
              model: "kimi-k2.7-code-highspeed",
              reasoningEnabled: true,
              reasoningEffort: "max",
            }),
            k3Off: module.providerRequestParameters({
              ...base,
              model: "kimi-k3",
              reasoningEnabled: false,
              reasoningEffort: "max",
            }),
            k26: module.providerRequestParameters({
              ...base,
              model: "kimi-k2.6",
              reasoningEnabled: true,
              reasoningEffort: "max",
            }),
            k26Off: module.providerRequestParameters({
              ...base,
              model: "kimi-k2.6",
              reasoningEnabled: false,
              reasoningEffort: "max",
            }),
            unknown: module.providerRequestParameters({
              ...base,
              model: "kimi-future",
              reasoningEnabled: true,
              reasoningEffort: "max",
            }),
            sources: {
              international: module.kimiReasoningCapabilities(
                "kimi-k3",
                "international"
              ).source,
              cn: module.kimiReasoningCapabilities("kimi-k3", "cn").source,
            },
          };
        }"""
    )
    assert contracts["k3"]["body"]["reasoning_effort"] == "max"
    assert "thinking" not in contracts["k3"]["body"]
    assert contracts["k31"]["body"]["reasoning_effort"] == "max"
    assert contracts["k31High"]["body"]["reasoning_effort"] == "max"
    assert "thinking" not in contracts["k31High"]["body"]
    assert contracts["k27High"]["body"] == {
        "model": "kimi-k2.7-code-highspeed",
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 16384,
    }
    assert contracts["k3Off"] == {
        "ok": False,
        "code": "reasoning_mandatory",
    }
    assert contracts["k26"]["body"]["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in contracts["k26"]["body"]
    assert contracts["k26Off"]["body"]["thinking"] == {"type": "disabled"}
    assert contracts["unknown"] == {
        "ok": False,
        "code": "kimi_model_unknown",
    }
    assert "platform.kimi.ai" in contracts["sources"]["international"]
    assert "platform.kimi.com" in contracts["sources"]["cn"]
    page.unroute("https://api.moonshot.ai/**", provider)


def test_browser_kimi_region_switch_isolates_credentials_and_models(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    provider_requests: list[str] = []

    def provider(route) -> None:
        provider_requests.append(route.request.url)
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        models = (
            [{"id": "kimi-k3"}]
            if "api.moonshot.cn" in route.request.url
            else [{"id": "kimi-k3.1-pro"}]
        )
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps({"data": models}),
        )

    page.route("https://api.moonshot.cn/**", provider)
    page.route("https://api.moonshot.ai/**", provider)
    _configure_kimi_profile(page, live_analysis_url, "cn", "kimi-k3")
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Fetch Models").click()
    expect(
        page.locator("[data-llm-models] option[value='kimi-k3']")
    ).to_have_count(1)

    page.locator("[data-llm-kimi-region]").select_option("international")
    expect(page.locator("[data-llm-base-url]")).to_have_value(
        "https://api.moonshot.ai/v1"
    )
    expect(page.locator("[data-llm-consent]")).not_to_be_checked()
    expect(page.locator("[data-llm-confirm]")).not_to_be_checked()
    expect(page.locator("[data-llm-models] option")).to_have_count(0)
    expect(page.locator("[data-llm-credential-meta]")).to_contain_text(
        "api.moonshot.cn"
    )

    before_requests = len(provider_requests)
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Unlock").click()
    expect(page.locator("[data-llm-status]")).to_contain_text(
        "Confirm privacy consent and the exact request parameters"
    )
    assert len(provider_requests) == before_requests
    page.locator("[data-llm-consent]").check()
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Unlock").click()
    expect(page.locator("[data-llm-status]")).to_contain_text(
        "Check the passphrase and Provider"
    )
    assert len(provider_requests) == before_requests

    page.locator("[data-llm-api-key]").fill("sk-kimi-intl")
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.locator("[data-llm-passphrase-confirm]").fill("correct horse battery")
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")
    expect(page.locator("[data-llm-credential-meta]")).to_contain_text(
        "api.moonshot.ai"
    )
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Unlock").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("unlocked")

    before_requests = len(provider_requests)
    page.get_by_role("button", name="Fetch Models").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("Loaded 1 Models")
    expect(
        page.locator("[data-llm-models] option[value='kimi-k3.1-pro']")
    ).to_have_count(1)
    assert all(
        "api.moonshot.ai" in url
        for url in provider_requests[before_requests:]
    )
    page.unroute("https://api.moonshot.cn/**", provider)
    page.unroute("https://api.moonshot.ai/**", provider)


@pytest.mark.parametrize(
    ("region", "base_url", "model"),
    [
        ("international", "https://api.moonshot.ai/v1", "kimi-k3"),
        ("cn", "https://api.moonshot.cn/v1", "kimi-k2.6"),
    ],
)
def test_browser_kimi_batches_and_summarizes_in_both_regions(
    browser_page: Page,
    live_analysis_url: str,
    region: str,
    base_url: str,
    model: str,
) -> None:
    page = browser_page
    requests: list[dict[str, Any]] = []

    def provider(route) -> None:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        if route.request.url.endswith("/models"):
            route.fulfill(
                status=200,
                headers={**headers, "Content-Type": "application/json"},
                body=json.dumps({"data": [{"id": model}]}),
            )
            return
        body = route.request.post_data_json
        requests.append(body)
        payload = json.loads(body["messages"][1]["content"])
        if "targets" in payload:
            content = json.dumps(
                {
                    "explanations": [
                        {
                            "ply": target["ply"],
                            "summary": "Kimi key ply",
                            "why": "engine facts",
                            "alternatives": "candidate lines",
                            "uncertainty": "limited",
                        }
                        for target in payload["targets"]
                    ]
                }
            )
            reasoning = "SECRET-KIMI-BATCH-REASONING"
        else:
            content = json.dumps(
                {
                    "summary": "Kimi game summary",
                    "uncertainty": "limited",
                }
            )
            reasoning = "SECRET-KIMI-SUMMARY-REASONING"
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": content,
                                "reasoning_content": reasoning,
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 6,
                        "completion_tokens_details": {"reasoning_tokens": 4},
                        "total_tokens": 9,
                    },
                }
            ),
        )

    fake_analysis = _single_ply_game_analysis()

    def game_results(route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(fake_analysis),
        )

    host = base_url.removeprefix("https://").split("/")[0]
    page.route(f"https://{host}/**", provider)
    page.route("**/tools/analysis/game/results", game_results)
    _configure_kimi_profile(page, live_analysis_url, region, model)
    expect(page.locator("[data-llm-base-url]")).to_have_value(base_url)

    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    page.locator("#pgn").fill(
        '[Event "Kimi"]\n[White "A"]\n[Black "B"]\n[Result "*"]\n\n1. e4 *'
    )
    page.get_by_role("button", name="Parse PGN").click()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    page.get_by_role("button", name="Start Game Analysis").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analyzing", timeout=30_000
    )
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-game-batch-preview]").click()
    page.locator("[data-game-explanation-passphrase]").fill(
        "correct horse battery"
    )
    page.get_by_role("button", name="Confirm and Generate").click()
    expect(page.locator("[data-game-batch-status]")).to_contain_text(
        "Key Ply explanations and summary complete.", timeout=30_000
    )
    expect(page.locator("[data-game-summary-text]")).to_have_text(
        "Kimi game summary"
    )
    expect(page.locator("[data-game-token-usage]")).to_contain_text("total 18")
    expect(page.locator("[data-game-token-usage]")).to_contain_text(
        "reasoning 8"
    )
    assert len(requests) == 2
    payloads = [json.loads(item["messages"][1]["content"]) for item in requests]
    assert len(payloads[0]["targets"]) == 1
    assert payloads[0]["targets"][0]["quality"] == "best"
    assert payloads[0]["targets"][0]["evaluation_before"]["value"] == 10
    assert "key_plies" in payloads[1]
    for body in requests:
        assert body["max_completion_tokens"] == 16384
        assert "max_tokens" not in body
        if model == "kimi-k3":
            assert body["reasoning_effort"] == "max"
            assert "thinking" not in body
        else:
            assert body["thinking"] == {"type": "enabled"}
            assert "reasoning_effort" not in body
    assert "SECRET-KIMI-BATCH-REASONING" not in page.content()
    assert "SECRET-KIMI-SUMMARY-REASONING" not in page.content()
    workspace = _workspace_record_text(page)
    assert "SECRET-KIMI-BATCH-REASONING" not in workspace
    assert "SECRET-KIMI-SUMMARY-REASONING" not in workspace
    assert "Kimi game summary" in workspace
    page.locator("[data-game-moves] [data-ply='1']").click()
    expect(page.locator("[data-game-explanation-summary]")).to_have_text(
        "Kimi key ply"
    )
    page.unroute(f"https://{host}/**", provider)
    page.unroute("**/tools/analysis/game/results", game_results)


def test_browser_custom_provider_complete_path_and_endpoint_isolation(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    provider_requests: list[str] = []
    chat_bodies: list[dict[str, Any]] = []
    chat_headers: list[dict[str, str]] = []
    chat_attempts: list[str] = []
    same_origin_posts: list[str] = []
    other_requests: list[str] = []
    console_messages: list[str] = []
    fail_mode = {"value": ""}
    page.on(
        "request",
        lambda request: (
            same_origin_posts.append(request.url)
            if request.method == "POST"
            and request.url.startswith(live_analysis_url)
            else None
        ),
    )
    page.on(
        "request",
        lambda request: (
            other_requests.append(request.url)
            if request.url.startswith("https://custom.test/v2")
            else None
        ),
    )
    page.on(
        "console",
        lambda message: console_messages.append(message.text),
    )

    def provider(route) -> None:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        provider_requests.append(route.request.url)
        if route.request.method == "OPTIONS":
            if fail_mode["value"] == "cors":
                route.fulfill(
                    status=403, headers={"Content-Type": "text/plain"}
                )
                return
            route.fulfill(status=204, headers=headers)
            return
        if route.request.url.endswith("/models"):
            route.fulfill(
                status=503,
                headers={**headers, "Content-Type": "application/json"},
                body=json.dumps({"error": {"message": "unavailable"}}),
            )
            return
        chat_attempts.append(route.request.url)
        chat_headers.append(
            {key.lower(): value for key, value in route.request.headers.items()}
        )
        mode = fail_mode["value"]
        if mode == "abort":
            route.abort("failed")
            return
        if mode == "cors":
            route.abort("blockedbyresponse")
            return
        content = json.dumps(
            {
                "summary": (
                    "Custom no usage"
                    if mode == "no_usage"
                    else "Custom summary"
                ),
                "why": "engine facts",
                "alternatives": "candidate lines",
                "uncertainty": "limited",
            }
        )
        if mode == "malformed":
            content = "{not-json"
        else:
            chat_bodies.append(route.request.post_data_json)
        response: dict[str, Any] = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": content,
                        "reasoning_content": "SECRET-CUSTOM-REASONING",
                    },
                }
            ]
        }
        if mode != "no_usage":
            response["usage"] = {
                "prompt_tokens": 3,
                "completion_tokens": 6,
                "completion_tokens_details": {"reasoning_tokens": 4},
                "total_tokens": 9,
            }
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(response),
        )

    page.route("https://custom.test/**", provider)
    page.goto(f"{live_analysis_url}/settings")
    page.locator("[data-llm-provider]").select_option("custom")
    expect(page.locator("[data-llm-base-url]")).to_have_value("")
    expect(page.locator("[data-custom-dialect-field]")).to_be_visible()
    expect(page.locator("[data-custom-budget-field]")).to_be_visible()
    expect(page.locator("[data-llm-compatibility]")).to_contain_text(
        "unverified"
    )
    assert provider_requests == []

    page.locator("[data-llm-base-url]").fill("http://custom.test/v1")
    page.locator("[data-llm-model]").fill("custom-model")
    page.locator("[data-llm-consent]").check()
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text(
        "Provide an HTTPS Base URL"
    )

    page.locator("[data-llm-base-url]").fill("https://custom.test/v1")
    expect(page.locator("[data-llm-destination]")).to_contain_text(
        "https://custom.test/v1"
    )
    page.locator("[data-llm-model]").fill("custom-model")
    page.locator("[data-llm-api-key]").fill("sk-custom-secret")
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.locator("[data-llm-passphrase-confirm]").fill("correct horse battery")
    page.locator("[data-llm-consent]").check()
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")
    page.get_by_role("button", name="Fetch Models").click()
    expect(page.locator("[data-llm-status]")).to_contain_text(
        "Model list could not be retrieved"
    )
    expect(page.locator("[data-llm-model]")).to_have_value("custom-model")
    page.get_by_role("button", name="Test Connection").click()
    expect(page.locator("[data-llm-status]")).to_contain_text(
        "Connection test failed"
    )
    expect(page.locator("[data-llm-model]")).to_have_value("custom-model")

    page.locator("[data-llm-custom-dialect]").select_option("openai")
    page.locator("[data-llm-custom-budget-field]").select_option(
        "max_completion_tokens"
    )
    page.locator("[data-llm-effort]").select_option("max")
    page.locator("[data-llm-confirm]").check()
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview == {
        "model": "custom-model",
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 16384,
        "reasoning_effort": "max",
    }
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("saved locally")

    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-explanation-passphrase]").fill("correct horse battery")
    before_posts = len(same_origin_posts)
    before_attempts = len(chat_attempts)
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-explanation-summary]")).to_have_text(
        "Custom summary"
    )
    body = chat_bodies[-1]
    assert body["reasoning_effort"] == "max"
    assert body["max_completion_tokens"] == 16384
    assert "max_tokens" not in body
    assert "thinking" not in body
    assert chat_headers[0]["authorization"] == "Bearer sk-custom-secret"
    expect(page.locator("[data-explanation-usage]")).to_contain_text("total 9")
    expect(page.locator("[data-explanation-usage]")).to_contain_text(
        "reasoning 4"
    )
    assert "SECRET-CUSTOM-REASONING" not in page.content()
    assert "sk-custom-secret" not in page.content()
    assert "sk-custom-secret" not in page.evaluate(
        "() => JSON.stringify(localStorage)"
    )
    assert "sk-custom-secret" not in _credential_record_text(page)
    assert "sk-custom-secret" not in _workspace_record_text(page)
    assert "SECRET-CUSTOM-REASONING" not in _workspace_record_text(page)
    assert "sk-custom-secret" not in "\n".join(console_messages)
    assert len(same_origin_posts) == before_posts
    assert len(chat_attempts) == before_attempts + 1

    fail_mode["value"] = "no_usage"
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-explanation-summary]")).to_have_text(
        "Custom no usage"
    )
    expect(page.locator("[data-explanation-usage]")).to_have_text("")

    fail_mode["value"] = "malformed"
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-explanation-status]")).to_contain_text(
        "could not be generated"
    )
    expect(page.locator("[data-explanation-summary]")).to_have_text(
        "Custom no usage"
    )

    fail_mode["value"] = "cors"
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-explanation-status]")).to_contain_text(
        "could not be reached from this browser"
    )
    expect(page.locator("[data-explanation-summary]")).to_have_text(
        "Custom no usage"
    )
    assert len(same_origin_posts) == before_posts

    page.goto(f"{live_analysis_url}/settings")
    expect(page.locator("[data-llm-credential-meta]")).to_contain_text(
        "custom.test/v1"
    )
    page.locator("[data-llm-base-url]").fill("https://custom.test/v2")
    expect(page.locator("[data-llm-destination]")).to_contain_text(
        "https://custom.test/v2"
    )
    expect(page.locator("[data-llm-consent]")).not_to_be_checked()
    expect(page.locator("[data-llm-confirm]")).not_to_be_checked()
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Unlock").click()
    expect(page.locator("[data-llm-status]")).to_contain_text(
        "Confirm privacy consent and the exact request parameters"
    )
    page.locator("[data-llm-consent]").check()
    page.locator("[data-llm-confirm]").check()
    page.get_by_role("button", name="Unlock").click()
    expect(page.locator("[data-llm-status]")).to_contain_text(
        "Check the passphrase and Provider"
    )
    assert other_requests == []

    page.locator("[data-llm-api-key]").fill("sk-custom-other")
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.locator("[data-llm-passphrase-confirm]").fill("correct horse battery")
    page.get_by_role("button", name="Save LLM Profile").click()
    expect(page.locator("[data-llm-credential-meta]")).to_contain_text(
        "custom.test/v2"
    )
    page.locator("[data-llm-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Unlock").click()
    expect(page.locator("[data-llm-status]")).to_contain_text("unlocked")
    assert other_requests == []
    page.unroute("https://custom.test/**", provider)


def test_browser_custom_provider_blocks_unresolved_dialect_controls(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/settings")
    page.locator("[data-llm-provider]").select_option("custom")
    page.locator("[data-llm-base-url]").fill("https://custom.test/v1")
    page.locator("[data-llm-model]").fill("custom-model")

    expect(page.locator("[data-llm-effort]")).to_be_disabled()
    expect(page.locator("[data-llm-capabilities]")).to_contain_text(
        "Select a Reasoning dialect"
    )
    expect(page.locator("[data-llm-capabilities]")).to_contain_text(
        "generation token field"
    )
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview == {"error": "custom_dialect_unresolved"}

    page.locator("[data-llm-custom-budget-field]").select_option("max_tokens")
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview == {"error": "custom_dialect_unresolved"}

    page.locator("[data-llm-custom-dialect]").select_option("none")
    expect(page.locator("[data-llm-capabilities]")).to_contain_text(
        "This dialect sends no Reasoning fields"
    )
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview == {"error": "reasoning_dialect_conflict"}

    page.locator("[data-llm-reasoning]").uncheck()
    expect(page.locator("[data-llm-effort]")).to_be_disabled()
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview == {
        "model": "custom-model",
        "response_format": {"type": "json_object"},
        "max_tokens": 16384,
    }

    page.locator("[data-llm-custom-dialect]").select_option("thinking")
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview["thinking"] == {"type": "disabled"}
    page.locator("[data-llm-reasoning]").check()
    expect(page.locator("[data-llm-effort]")).to_be_disabled()
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview["thinking"] == {"type": "enabled"}

    page.locator("[data-llm-custom-dialect]").select_option("openai")
    expect(page.locator("[data-llm-effort]")).to_be_enabled()
    expect(page.locator("[data-llm-capabilities]")).to_contain_text(
        "Choose an explicit Reasoning effort"
    )
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview == {"error": "reasoning_effort_unresolved"}

    page.locator("[data-llm-effort]").select_option("high")
    page.locator("[data-llm-custom-budget-field]").select_option(
        "max_completion_tokens"
    )
    preview = json.loads(page.locator("[data-llm-preview]").inner_text())
    assert preview == {
        "model": "custom-model",
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 16384,
        "reasoning_effort": "high",
    }


def test_browser_custom_blocks_unresolved_generation(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    provider_requests: list[str] = []

    def provider(route) -> None:
        provider_requests.append(route.request.url)
        route.fulfill(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Content-Type": "application/json",
            },
            body=json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "summary": "must not run",
                                        "why": "facts",
                                        "alternatives": "lines",
                                        "uncertainty": "none",
                                    }
                                )
                            }
                        }
                    ]
                }
            ),
        )

    page.route("https://custom.test/**", provider)
    _configure_custom_profile(
        page,
        live_analysis_url,
        dialect="",
        budget_field="",
        effort="",
    )
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-explanation-passphrase]").fill("correct horse battery")
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-explanation-status]")).to_contain_text(
        "Select a Reasoning dialect"
    )
    assert provider_requests == []
    page.unroute("https://custom.test/**", provider)


def test_browser_custom_uses_real_cross_origin_provider(
    browser_page: Page,
    live_analysis_url: str,
    local_cross_origin_llm: dict[str, Any],
) -> None:
    page = browser_page
    local = local_cross_origin_llm
    local["state"]["mode"] = "success"
    local["state"]["requests"].clear()
    same_origin_posts: list[str] = []
    page.on(
        "request",
        lambda request: (
            same_origin_posts.append(request.url)
            if request.method == "POST"
            and request.url.startswith(live_analysis_url)
            else None
        ),
    )
    _configure_custom_profile(
        page, live_analysis_url, endpoint=local["url"]
    )
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-explanation-passphrase]").fill("correct horse battery")
    before_posts = len(same_origin_posts)
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-explanation-summary]")).to_have_text(
        "Custom local summary"
    )
    posts = [
        request
        for request in local["state"]["requests"]
        if request["method"] == "POST"
    ]
    assert len(posts) == 1
    assert posts[0]["headers"]["authorization"] == "Bearer sk-custom-secret"
    body = json.loads(posts[0]["body"])
    assert body["reasoning_effort"] == "max"
    assert body["max_completion_tokens"] == 16384
    assert len(same_origin_posts) == before_posts

    local["state"]["mode"] = "cors"
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-explanation-status]")).to_contain_text(
        "could not be reached from this browser"
    )
    expect(page.locator("[data-explanation-summary]")).to_have_text(
        "Custom local summary"
    )
    posts = [
        request
        for request in local["state"]["requests"]
        if request["method"] == "POST"
    ]
    assert len(posts) == 1
    assert len(same_origin_posts) == before_posts


def test_browser_game_exports_pgn_and_markdown(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    fake_analysis = _single_ply_game_analysis()
    fake_analysis["plies"][0]["evaluation_after"] = {
        "kind": "cp",
        "value": -50,
        "text": "-0.50",
    }

    def game_results(route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(fake_analysis),
        )

    page.route("**/tools/analysis/game/results", game_results)
    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    page.locator("#pgn").fill(
        '[Event "Export"]\n[White "Alice"]\n[Black "Bob"]\n[Result "*"]\n\n'
        "1. e4 {keep me} (1. d4 d5) *"
    )
    page.get_by_role("button", name="Parse PGN").click()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    page.get_by_role("button", name="Start Game Analysis").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )

    with page.expect_download() as download_info:
        page.locator("[data-game-export-pgn]").click()
    download = download_info.value
    assert download.suggested_filename.endswith(".pgn")
    pgn_content = Path(download.path()).read_text(encoding="utf-8")
    exported = chess.pgn.read_game(io.StringIO(pgn_content))
    assert exported is not None
    assert exported.headers["Event"] == "Export"
    first = exported.next()
    assert first is not None
    assert first.san() == "e4"
    assert len(exported.variations) == 2
    assert exported.variations[1].san() == "d4"
    assert "keep me" in (first.comment or "")
    assert "[%eval -0.50]" in (first.comment or "")
    assert "Quality: Best." in (first.comment or "")
    assert 1 in first.nags

    with page.expect_download() as download_info:
        page.locator("[data-game-export-markdown]").click()
    markdown_download = download_info.value
    assert markdown_download.suggested_filename.endswith(".md")
    markdown = Path(markdown_download.path()).read_text(encoding="utf-8")
    assert "# ChessFEN Game Analysis" in markdown
    assert "## Input PGN" in markdown
    assert "## Mainline evaluation" in markdown
    assert "## Candidate Lines" in markdown
    assert "Stockfish 19" in markdown
    assert "keep me" in markdown
    assert "sk-" not in markdown
    page.locator("[data-game-export-explanations]").uncheck()
    with page.expect_download() as download_info:
        page.locator("[data-game-export-markdown]").click()
    without_explanations = Path(download_info.value.path()).read_text(
        encoding="utf-8"
    )
    assert "## Key Ply explanations" not in without_explanations
    assert "**Generated Explanations:** Omitted" in without_explanations
    expect(page.locator("[data-game-export-status]")).to_contain_text(
        "Export ready."
    )
    page.unroute("**/tools/analysis/game/results", game_results)


@pytest.mark.parametrize(
    ("language", "heading"),
    [
        ("en", "# ChessFEN Game Analysis"),
        ("zh", "# ChessFEN 棋局分析"),
        ("de", "# ChessFEN-Partieanalyse"),
    ],
)
def test_browser_markdown_export_is_localized(
    browser_page: Page, live_analysis_url: str, language: str, heading: str
) -> None:
    page = browser_page
    fake_analysis = _single_ply_game_analysis()

    def game_results(route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(fake_analysis),
        )

    page.route("**/tools/analysis/game/results", game_results)
    page.goto(
        f"{live_analysis_url}/lang/{language}?next=/tools/analysis/pgn"
    )
    page.locator("#pgn").fill('[White "A"]\n[Black "B"]\n\n1. e4 *')
    page.locator('.pgn-form button[name="mode"][value="parse"]').click()
    page.locator('.pgn-form button[name="mode"][value="open"]').click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    page.locator("[data-game-start]").click()
    expect(page.locator("[data-game-export-pgn]")).to_be_visible(
        timeout=30_000
    )
    with page.expect_download() as download_info:
        page.locator("[data-game-export-markdown]").click()
    markdown = Path(download_info.value.path()).read_text(encoding="utf-8")
    assert heading in markdown
    for fragment in {
        "en": [
            "**Engine:** Stockfish 19",
            "**Search preset:** standard",
            "**MultiPV:** 3",
            "**Provider:** Not available",
            "**Model:** Not available",
        ],
        "zh": [
            "**引擎:** Stockfish 19",
            "**搜索档位:** standard",
            "**MultiPV:** 3",
            "**Provider:** 暂无",
            "**模型:** 暂无",
        ],
        "de": [
            "**Engine:** Stockfish 19",
            "**Suchprofil:** standard",
            "**MultiPV:** 3",
            "**Provider:** Nicht verfügbar",
            "**Modell:** Nicht verfügbar",
        ],
    }[language]:
        assert fragment in markdown
    assert "sk-" not in markdown
    with page.expect_download() as download_info:
        page.locator("[data-game-export-pgn]").click()
    pgn_content = Path(download_info.value.path()).read_text(encoding="utf-8")
    assert "sk-" not in pgn_content
    assert "Quality: Best." in pgn_content
    page.unroute("**/tools/analysis/game/results", game_results)


def test_browser_position_exports_pgn_and_markdown(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )

    with page.expect_download() as download_info:
        page.locator("[data-position-export-pgn]").click()
    pgn_content = Path(download_info.value.path()).read_text(encoding="utf-8")
    exported = chess.pgn.read_game(io.StringIO(pgn_content))
    assert exported is not None
    assert exported.headers["SetUp"] == "1"
    assert exported.headers["FEN"] == _START_FEN
    assert exported.board().fen() == _START_FEN
    assert exported.next() is not None
    assert len(exported.variations) == 3

    with page.expect_download() as download_info:
        page.locator("[data-position-export-markdown]").click()
    markdown = Path(download_info.value.path()).read_text(encoding="utf-8")
    assert "# ChessFEN Position Analysis" in markdown
    assert "## Input Full FEN" in markdown
    assert _START_FEN in markdown
    assert "## Candidate Lines" in markdown
    assert "Stockfish 19" in markdown
    assert "sk-" not in markdown
    expect(page.locator("[data-position-export-status]")).to_contain_text(
        "Export ready."
    )


def test_browser_export_includes_optional_explanations_without_secrets(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page

    def provider(route) -> None:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        }
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "summary": "Export explanation summary.",
                                        "why": "Engine facts.",
                                        "alternatives": "Candidate Lines.",
                                        "uncertainty": "Limited evidence.",
                                    }
                                ),
                                "reasoning_content": "SECRET-EXPORT-REASONING",
                            }
                        }
                    ],
                    "usage": {"total_tokens": 12},
                }
            ),
        )

    fake_analysis = _single_ply_game_analysis()

    def game_results(route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(fake_analysis),
        )

    page.route("https://api.openai.test/**", provider)
    page.route("**/tools/analysis/game/results", game_results)
    _configure_mock_profile(page, live_analysis_url)
    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    page.locator("#pgn").fill(
        '[Event "Export"]\n[White "A"]\n[Black "B"]\n\n1. e4 *'
    )
    page.get_by_role("button", name="Parse PGN").click()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    page.get_by_role("button", name="Start Game Analysis").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-game-moves] [data-ply='1']").click()
    page.locator("[data-game-explanation-passphrase]").fill(
        "correct horse battery"
    )
    page.get_by_role("button", name="Generate Explanation").click()
    expect(page.locator("[data-game-explanation-summary]")).to_have_text(
        "Export explanation summary."
    )

    with page.expect_download() as download_info:
        page.locator("[data-game-export-pgn]").click()
    included = Path(download_info.value.path()).read_text(encoding="utf-8")
    assert "Export explanation summary." in included
    assert "[%eval" in included
    assert "sk-test-secret-value" not in included
    assert "correct horse battery" not in included
    assert "privacy consent" not in included.lower()
    assert "SECRET-EXPORT-REASONING" not in included

    page.locator("[data-game-export-explanations]").uncheck()
    with page.expect_download() as download_info:
        page.locator("[data-game-export-pgn]").click()
    omitted = Path(download_info.value.path()).read_text(encoding="utf-8")
    assert "Export explanation summary." not in omitted
    assert "[%eval" in omitted

    page.locator("[data-game-export-explanations]").check()
    with page.expect_download() as download_info:
        page.locator("[data-game-export-markdown]").click()
    markdown = Path(download_info.value.path()).read_text(encoding="utf-8")
    assert "Export explanation summary." in markdown
    assert "**Provider:** openai" in markdown
    assert "**Model:** mock-model" in markdown
    assert "sk-test-secret-value" not in markdown
    assert "correct horse battery" not in markdown
    assert "SECRET-EXPORT-REASONING" not in markdown
    page.unroute("https://api.openai.test/**", provider)
    page.unroute("**/tools/analysis/game/results", game_results)


def test_browser_custom_batches_and_summarizes_game(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    requests: list[dict[str, Any]] = []

    def provider(route) -> None:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "0",
        }
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        if route.request.url.endswith("/models"):
            route.fulfill(
                status=200,
                headers={**headers, "Content-Type": "application/json"},
                body=json.dumps({"data": [{"id": "custom-model"}]}),
            )
            return
        body = route.request.post_data_json
        requests.append(body)
        payload = json.loads(body["messages"][1]["content"])
        if "targets" in payload:
            content = json.dumps(
                {
                    "explanations": [
                        {
                            "ply": target["ply"],
                            "summary": "Custom key ply",
                            "why": "engine facts",
                            "alternatives": "candidate lines",
                            "uncertainty": "limited",
                        }
                        for target in payload["targets"]
                    ]
                }
            )
            reasoning = "SECRET-CUSTOM-BATCH-REASONING"
        else:
            content = json.dumps(
                {
                    "summary": "Custom game summary",
                    "uncertainty": "limited",
                }
            )
            reasoning = "SECRET-CUSTOM-SUMMARY-REASONING"
        route.fulfill(
            status=200,
            headers={**headers, "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": content,
                                "reasoning_content": reasoning,
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 6,
                        "completion_tokens_details": {"reasoning_tokens": 4},
                        "total_tokens": 9,
                    },
                }
            ),
        )

    fake_analysis = _single_ply_game_analysis()

    def game_results(route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(fake_analysis),
        )

    page.route("https://custom.test/**", provider)
    page.route("**/tools/analysis/game/results", game_results)
    _configure_custom_profile(
        page,
        live_analysis_url,
        dialect="openai",
        budget_field="max_tokens",
        effort="high",
    )
    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    page.locator("#pgn").fill(
        '[Event "Custom"]\n[White "A"]\n[Black "B"]\n[Result "*"]\n\n1. e4 *'
    )
    page.get_by_role("button", name="Parse PGN").click()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    page.get_by_role("button", name="Start Game Analysis").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analyzing", timeout=30_000
    )
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    page.locator("[data-game-batch-preview]").click()
    page.locator("[data-game-explanation-passphrase]").fill(
        "correct horse battery"
    )
    page.get_by_role("button", name="Confirm and Generate").click()
    expect(page.locator("[data-game-batch-status]")).to_contain_text(
        "Key Ply explanations and summary complete.", timeout=30_000
    )
    expect(page.locator("[data-game-summary-text]")).to_have_text(
        "Custom game summary"
    )
    expect(page.locator("[data-game-token-usage]")).to_contain_text("total 18")
    assert len(requests) == 2
    payloads = [json.loads(item["messages"][1]["content"]) for item in requests]
    assert len(payloads[0]["targets"]) == 1
    assert "key_plies" in payloads[1]
    for body in requests:
        assert body["reasoning_effort"] == "high"
        assert body["max_tokens"] == 16384
        assert "max_completion_tokens" not in body
    assert "SECRET-CUSTOM-BATCH-REASONING" not in page.content()
    assert "SECRET-CUSTOM-SUMMARY-REASONING" not in page.content()
    workspace = _workspace_record_text(page)
    assert "SECRET-CUSTOM-BATCH-REASONING" not in workspace
    assert "SECRET-CUSTOM-SUMMARY-REASONING" not in workspace
    assert "Custom game summary" in workspace
    page.locator("[data-game-moves] [data-ply='1']").click()
    expect(page.locator("[data-game-explanation-summary]")).to_have_text(
        "Custom key ply"
    )
    page.unroute("https://custom.test/**", provider)
    page.unroute("**/tools/analysis/game/results", game_results)


def test_browser_cross_browser_primary_workflows(
    cross_browser_page: tuple[str, Page], live_analysis_url: str
) -> None:
    name, page = cross_browser_page
    page.goto(f"{live_analysis_url}/settings")
    expect(page.locator("[data-llm-provider]")).to_be_visible()
    expect(page.get_by_role("button", name="Save LLM Profile")).to_be_visible()

    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    expect(page.locator("[data-candidate-line]")).to_have_count(3)
    with page.expect_download() as download_info:
        page.locator("[data-position-export-markdown]").click()
    markdown = Path(download_info.value.path()).read_text(encoding="utf-8")
    assert "# ChessFEN Position Analysis" in markdown, name

    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    page.locator("#pgn").fill('[White "A"]\n[Black "B"]\n\n1. e4 *')
    page.get_by_role("button", name="Parse PGN").click()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    page.get_by_role("button", name="Start Game Analysis").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    expect(page.locator("[data-game-moves] [data-ply='1']")).to_be_visible()
    expect(page.locator("[data-game-export-pgn]")).to_be_visible()


@pytest.mark.parametrize("language", ["zh", "de"])
def test_browser_localized_mobile_layout_has_no_overflow(
    browser_page: Page, live_analysis_url: str, language: str
) -> None:
    page = browser_page
    page.set_viewport_size({"width": 390, "height": 844})
    for path in ("/tools/analysis", "/settings", "/tools/analysis/pgn"):
        page.goto(f"{live_analysis_url}/lang/{language}?next={path}")
        expect(page.get_by_role("heading", level=1)).to_have_count(1)
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
        ), f"{language} {path}"


def test_browser_multithread_stockfish_worker_proof(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    result = page.evaluate(
        """async () => {
          const capabilities = {
            secure: window.isSecureContext,
            isolated: window.crossOriginIsolated,
            sharedArrayBuffer: typeof SharedArrayBuffer !== "undefined",
            worker: typeof Worker !== "undefined",
          };
          const worker = new Worker(
            "/static/vendor/stockfish/stockfish-19-lite-multi.js"
          );
          const transcript = [];
          const workerErrors = [];
          worker.onerror = (event) => workerErrors.push(event.message);
          const waitFor = (needle, timeout = 30000) =>
            new Promise((resolve, reject) => {
              const timer = setTimeout(
                () => reject(new Error(`timeout: ${needle}`)),
                timeout
              );
              const inspect = (event) => {
                const text =
                  typeof event.data === "string"
                    ? event.data
                    : event.data?.data || "";
                transcript.push(text);
                if (text.startsWith(needle)) {
                  clearTimeout(timer);
                  worker.removeEventListener("message", inspect);
                  resolve(text);
                }
              };
              worker.addEventListener("message", inspect);
            });
          try {
            worker.postMessage("uci");
            const uciok = await waitFor("uciok");
            worker.postMessage("setoption name Threads value 2");
            worker.postMessage("setoption name Hash value 32");
            worker.postMessage("isready");
            const readyok = await waitFor("readyok");
            worker.postMessage("position startpos");
            worker.postMessage("go depth 5");
            const bestmove = await waitFor("bestmove");
            worker.terminate();
            return {
              capabilities,
              uciok,
              readyok,
              bestmove,
              transcript,
              workerErrors,
            };
          } catch (error) {
            worker.terminate();
            return {
              capabilities,
              error: String(error),
              transcript,
              workerErrors,
            };
          }
        }"""
    )
    assert result["capabilities"] == {
        "secure": True,
        "isolated": True,
        "sharedArrayBuffer": True,
        "worker": True,
    }
    assert "error" not in result, result
    assert result["uciok"] == "uciok"
    assert result["readyok"] == "readyok"
    assert result["bestmove"].startswith("bestmove ")
    for path in ("/", "/settings"):
        page.goto(f"{live_analysis_url}{path}")
        assert page.evaluate("() => window.crossOriginIsolated") is False


def test_browser_engine_uses_single_thread_without_isolation(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    multithread_requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            multithread_requests.append(request.url)
            if "stockfish-19-lite-multi" in request.url
            else None
        ),
    )

    def no_isolation(route) -> None:
        response = route.fetch()
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower()
            not in {
                "cross-origin-opener-policy",
                "cross-origin-embedder-policy",
            }
        }
        route.fulfill(response=response, headers=headers)

    page.route(f"{live_analysis_url}/tools/analysis", no_isolation)
    page.goto(f"{live_analysis_url}/tools/analysis")
    assert page.evaluate("() => window.crossOriginIsolated") is False
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    expect(page.locator("[data-engine-runtime]")).to_contain_text(
        "single-threaded"
    )
    assert multithread_requests == []
    page.unroute(f"{live_analysis_url}/tools/analysis", no_isolation)


def test_browser_engine_falls_back_when_multithread_init_fails(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.add_init_script(
        """(() => {
          const NativeWorker = window.Worker;
          window.Worker = class extends NativeWorker {
            constructor(url, options) {
              super(url, options);
              if (String(url).includes("stockfish-19-lite-multi.js")) {
                setTimeout(
                  () => this.dispatchEvent(
                    new ErrorEvent("error", { message: "simulated init" })
                  ),
                  0
                );
              }
            }
          };
        })()"""
    )
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    expect(page.locator("[data-engine-runtime]")).to_contain_text(
        "single-threaded"
    )
    expect(page.locator("[data-candidate-line]")).to_have_count(3)
    expect(page.locator("[data-workspace-status]")).to_contain_text(
        "Multithreaded Stockfish was unavailable"
    )
    workspace = json.loads(_workspace_record_text(page))
    assert workspace["engine"]["fallback"] is True
    page.reload()
    expect(page.locator("[data-analysis-status]")).to_contain_text("Restored")
    expect(page.locator("[data-engine-runtime]")).to_contain_text(
        "single-threaded"
    )


def test_browser_engine_mid_run_fallback_preserves_completed_positions(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.add_init_script(
        """(() => {
          const NativeWorker = window.Worker;
          window.__engineSearches = 0;
          window.Worker = class extends NativeWorker {
            constructor(url, options) {
              super(url, options);
              if (!String(url).includes("stockfish-19-lite-multi.js")) return;
              const nativePost = this.postMessage.bind(this);
              this.postMessage = (message, ...args) => {
                nativePost(message, ...args);
                if (typeof message === "string" && message.startsWith("go ")) {
                  window.__engineSearches += 1;
                  if (window.__engineSearches === 2) {
                    setTimeout(
                      () => this.dispatchEvent(
                        new ErrorEvent("error", { message: "simulated search" })
                      ),
                      100
                    );
                  }
                }
              };
            }
          };
        })()"""
    )
    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    page.locator("#pgn").fill('[White "A"]\n[Black "B"]\n\n1. e4 e5 *')
    page.get_by_role("button", name="Parse PGN").click()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    page.get_by_role("button", name="Start Game Analysis").click()
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=60_000
    )
    expect(page.locator("[data-engine-runtime]")).to_contain_text(
        "mixed runtime"
    )
    expect(page.locator("[data-game-moves] [data-ply='2']")).to_be_visible()
    page.locator("[data-game-moves] [data-ply='1']").click()
    expect(page.locator("[data-game-lines] .candidate-line")).to_have_count(3)
    expect(page.locator("[data-game-evaluation]")).not_to_be_empty()
    workspace = json.loads(_workspace_record_text(page))
    assert workspace["engine"]["mixed"] is True
    assert workspace["engine"]["runtime"] == "single-threaded"


def test_browser_engine_stops_after_repeated_init_failures(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.add_init_script(
        """(() => {
          const NativeWorker = window.Worker;
          window.__workerStarts = 0;
          window.Worker = class extends NativeWorker {
            constructor(url, options) {
              super(url, options);
              window.__workerStarts += 1;
              setTimeout(
                () => this.dispatchEvent(
                  new ErrorEvent("error", { message: "simulated failure" })
                ),
                0
              );
            }
          };
        })()"""
    )
    page.goto(f"{live_analysis_url}/tools/analysis")
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-error]")).to_contain_text(
        "Stockfish could not start or stopped unexpectedly. Reload the page and check that this browser supports Web Workers and WebAssembly."
    )
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis stopped"
    )
    assert page.evaluate("() => window.__workerStarts") <= 2


def test_browser_analysis_accessibility_and_mobile_layout(
    browser_page: Page, live_analysis_url: str
) -> None:
    page = browser_page
    page.goto(f"{live_analysis_url}/tools/analysis")
    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    expect(page.get_by_label("Full FEN")).to_be_visible()
    expect(page.get_by_label("Actual move")).to_be_visible()
    expect(page.get_by_label("Search preset")).to_be_visible()
    expect(page.locator("#analysis-multipv")).to_be_visible()
    expect(page.get_by_label("Engine resource")).to_be_visible()
    status = page.locator("[data-analysis-status]")
    expect(status).to_have_attribute("role", "status")
    expect(status).to_have_attribute("aria-live", "polite")
    page.keyboard.press("Tab")
    focus = page.evaluate(
        """() => {
          const element = document.activeElement;
          const style = getComputedStyle(element);
          return {
            outlineStyle: style.outlineStyle,
            outlineWidth: style.outlineWidth,
            boxShadow: style.boxShadow,
          };
        }"""
    )
    assert focus["outlineStyle"] == "solid"
    assert focus["outlineWidth"] != "0px"

    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    expect(page.locator("[data-analysis-start]")).to_be_in_viewport()

    page.goto(f"{live_analysis_url}/settings")
    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    expect(page.locator("#llm-provider")).to_be_visible()
    expect(page.locator("#llm-base-url")).to_be_visible()
    expect(page.locator("#llm-model")).to_be_visible()
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
    )

    page.goto(f"{live_analysis_url}/tools/analysis/pgn")
    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    page.locator("#pgn").fill('[White "A"]\n[Black "B"]\n\n1. e4 *')
    page.get_by_role("button", name="Parse PGN").click()
    page.get_by_role("button", name="Open Game").click()
    page.wait_for_load_state("load")
    page.wait_for_selector("[data-game-root][data-game-ready='true']")
    start = page.locator("[data-game-start]")
    start.focus()
    page.keyboard.press("Enter")
    expect(page.locator("[data-game-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
    )
    move = page.locator("[data-game-moves] [data-ply='1']")
    assert re.search(
        "Best|Excellent|Good|Inaccuracy|Mistake|Blunder|Forced",
        move.inner_text(),
    )
    page.locator("[data-game-export-pgn]").scroll_into_view_if_needed()
    expect(page.locator("[data-game-export-pgn]")).to_be_in_viewport()
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
