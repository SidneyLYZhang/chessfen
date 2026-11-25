from __future__ import annotations

import json
import re
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

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
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def browser_page(browser) -> Iterator[Page]:
    context = browser.new_context(locale="en-US")
    page = context.new_page()
    page.set_default_timeout(30_000)
    page.on("dialog", lambda dialog: dialog.accept())
    yield page
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
        "single-threaded · fast"
    )
    page.locator("#analysis-fen").fill(_START_FEN)
    page.get_by_role("button", name="Start analysis").click()
    expect(page.locator("[data-analysis-status]")).to_contain_text(
        "Analysis complete", timeout=30_000
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


def test_browser_reports_workspace_storage_failure_without_losing_results(
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
    page.get_by_role("button", name="Retry summary").click()
    expect(page.locator("[data-game-overall-summary]")).to_be_visible()
    expect(page.locator("[data-game-summary-text]")).to_have_text("Overall")
    expect(page.locator("[data-game-token-usage]")).to_contain_text(
        "total 45"
    )
    assert len(requests) == 6
    payloads = [json.loads(item["messages"][1]["content"]) for item in requests]
    assert [len(p.get("targets", [])) for p in payloads] == [5, 5, 1, 1, 0, 0]


def test_browser_generates_a_position_overall_summary(
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
            route.fulfill(
                status=200,
                headers={"Content-Type": "application/json"},
                body=json.dumps(
                    {"choices": [{"message": {"content": "{}"}}]}
                ),
            )
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
        "cors": "could not be generated",
        "abort": "could not be generated",
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
        }
        provider_requests.append(route.request.url)
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
            route.fulfill(
                status=200,
                headers={"Content-Type": "application/json"},
                body=json.dumps(
                    {"choices": [{"message": {"content": "{}"}}]}
                ),
            )
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
            "could not be generated"
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
