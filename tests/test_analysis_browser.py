from __future__ import annotations

import json
import re
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
