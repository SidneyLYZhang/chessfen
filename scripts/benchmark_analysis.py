from __future__ import annotations

import argparse
import json
import os
import platform
import random
import socket
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.pgn
import uvicorn
from playwright.sync_api import Page, sync_playwright

from chessfen.app import create_app
from chessfen.settings import Settings


@dataclass(frozen=True)
class BenchmarkResult:
    browser: str
    browser_version: str
    platform: str
    cpu_count: int
    plies: int
    cross_origin_isolated: bool
    engine_runtime: str
    analysis_seconds: float
    cancel_feedback_ms: float
    engine_stop_ms: float


def _generate_pgn(plies: int) -> str:
    for seed in range(100):
        rng = random.Random(seed)
        board = chess.Board()
        game = chess.pgn.Game()
        node: chess.pgn.GameNode = game
        for _index in range(plies):
            legal = sorted(board.legal_moves, key=lambda move: move.uci())
            viable = []
            for move in legal:
                board.push(move)
                finished = board.is_game_over()
                board.pop()
                if not finished:
                    viable.append(move)
            if not viable:
                break
            move = viable[rng.randrange(len(viable))]
            node = node.add_variation(move)
            board.push(move)
        if len(list(game.mainline_moves())) == plies:
            game.headers.update(
                {
                    "Event": f"ChessFEN {plies}-Ply Benchmark",
                    "White": "Benchmark",
                    "Black": "Engine",
                }
            )
            return str(game)
    raise RuntimeError(f"Could not generate a {plies}-Ply benchmark Game")


def _start_server(database: Path) -> tuple[str, uvicorn.Server, threading.Thread]:
    app = create_app(
        Settings(
            database_url=f"sqlite:///{database.as_posix()}",
            secret_key="benchmark-secret",
            admin_password="benchmark-admin",
        )
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
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
                    return base_url, server, thread
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("Benchmark server did not become ready")


def _wait_for_text(page: Page, selector: str, text: str, timeout: int) -> None:
    page.wait_for_function(
        """([selector, text]) =>
          document.querySelector(selector)?.textContent.includes(text)""",
        arg=[selector, text],
        timeout=timeout * 1000,
    )


def _configure_standard(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/tools/analysis")
    page.evaluate(
        """() => localStorage.setItem(
          "chessfen.analysis.settings.v1",
          JSON.stringify({ preset: "standard", multipv: 3, resource: "lite" })
        )"""
    )
    page.reload()


def run_benchmark(plies: int, timeout_seconds: int) -> BenchmarkResult:
    pgn = _generate_pgn(plies)
    with tempfile.TemporaryDirectory() as directory:
        base_url, server, thread = _start_server(Path(directory) / "benchmark.db")
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context(locale="en-US")
                page = context.new_page()
                page.set_default_timeout(timeout_seconds * 1000)
                page.on("dialog", lambda dialog: dialog.accept())
                _configure_standard(page, base_url)
                isolated = page.evaluate(
                    """() => ({
                      crossOriginIsolated: window.crossOriginIsolated,
                      sharedArrayBuffer: typeof SharedArrayBuffer !== "undefined",
                    })"""
                )
                page.goto(f"{base_url}/tools/analysis/pgn")
                page.locator("#pgn").fill(pgn)
                page.get_by_role("button", name="Parse PGN").click()
                page.get_by_role("button", name="Open Game").click()
                page.wait_for_load_state("load")
                page.wait_for_selector(
                    "[data-game-root][data-game-ready='true']"
                )
                started = time.monotonic()
                page.get_by_role("button", name="Start Game Analysis").click()
                page.locator("[data-game-export-pgn]").wait_for(
                    state="visible", timeout=timeout_seconds * 1000
                )
                analysis_seconds = time.monotonic() - started
                engine_runtime = page.locator(
                    "[data-engine-runtime]"
                ).inner_text()

                page.goto(f"{base_url}/tools/analysis")
                page.locator("#analysis-fen").fill(
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
                )
                page.get_by_role("button", name="Start analysis").click()
                _wait_for_text(
                    page, "[data-analysis-status]", "Analyzing", timeout_seconds
                )
                page.evaluate(
                    """() => {
                      window.__cancelTimes = {};
                      const status = document.querySelector(
                        "[data-analysis-status]"
                      );
                      const record = () => {
                        const start = window.__cancelTimes.start;
                        if (start === undefined) return;
                        const now = performance.now();
                        const text = status?.textContent || "";
                        if (
                          text.includes("Stopping") &&
                          window.__cancelTimes.feedback === undefined
                        ) {
                          window.__cancelTimes.feedback = now - start;
                        }
                        if (
                          text.includes("Analysis cancelled") &&
                          window.__cancelTimes.done === undefined
                        ) {
                          window.__cancelTimes.done = now - start;
                        }
                      };
                      window.__cancelObserver = new MutationObserver(record);
                      window.__cancelObserver.observe(status, {
                        childList: true,
                        characterData: true,
                        subtree: true,
                      });
                    }"""
                )
                page.evaluate(
                    """() => {
                      window.__cancelTimes.start = performance.now();
                      document.querySelector("[data-analysis-cancel]").click();
                    }"""
                )
                page.wait_for_function(
                    """() => document.querySelector("[data-analysis-status]")
                      ?.textContent.includes("Analysis cancelled")""",
                    timeout=timeout_seconds * 1000,
                )
                cancel_times = page.evaluate(
                    "() => window.__cancelTimes"
                )
                cancel_feedback_ms = cancel_times.get(
                    "feedback", cancel_times.get("done", 0)
                )
                engine_stop_ms = cancel_times.get("done", 0)
                version = browser.version
                context.close()
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
        return BenchmarkResult(
            browser="chromium",
            browser_version=version,
            platform=f"{platform.system()} {platform.machine()}",
            cpu_count=os.cpu_count() or 0,
            plies=plies,
            cross_origin_isolated=bool(isolated["crossOriginIsolated"])
            and bool(isolated["sharedArrayBuffer"]),
            engine_runtime=engine_runtime,
            analysis_seconds=analysis_seconds,
            cancel_feedback_ms=cancel_feedback_ms,
            engine_stop_ms=engine_stop_ms,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plies", type=int, default=60)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    result = run_benchmark(args.plies, args.timeout)
    print(json.dumps(result.__dict__, indent=2))


if __name__ == "__main__":
    main()
