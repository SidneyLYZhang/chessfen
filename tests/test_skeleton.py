import pytest
from fastapi.testclient import TestClient

from chessfen.app import create_app


def make_client() -> TestClient:
    return TestClient(create_app())


def test_health_reports_ok() -> None:
    response = make_client().get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("accept_language", "expected_locale", "expected_tagline"),
    [
        (None, "zh", "识别、解析并生成国际象棋局面"),
        (
            "en-GB,en;q=0.9,de;q=0.8",
            "en",
            "Recognize, parse, and render chess positions",
        ),
        (
            "de-DE;q=0.9,en;q=0.8",
            "de",
            "Schachstellungen erkennen, parsen und erzeugen",
        ),
        ("fr-FR,fr;q=0.9", "zh", "识别、解析并生成国际象棋局面"),
    ],
)
def test_home_locale_comes_from_accept_language(
    accept_language: str | None, expected_locale: str, expected_tagline: str
) -> None:
    headers = {"Accept-Language": accept_language} if accept_language else {}
    response = make_client().get("/", headers=headers)

    assert response.status_code == 200
    assert f'<html lang="{expected_locale}">' in response.text
    assert expected_tagline in response.text


def test_language_toggle_sets_cookie_and_persists() -> None:
    client = make_client()

    response = client.get("/lang/en?next=/", follow_redirects=False)

    assert response.status_code in (302, 303)
    assert response.headers["location"] == "/"
    assert "lang=en" in response.headers["set-cookie"]

    page = client.get("/", headers={"Accept-Language": "zh-CN,zh;q=0.9"})
    assert '<html lang="en">' in page.text
    assert "Recognize, parse, and render chess positions" in page.text


def test_language_cookie_wins_over_accept_language() -> None:
    client = make_client()
    client.get("/lang/de?next=/", follow_redirects=False)

    page = client.get("/", headers={"Accept-Language": "en-US,en;q=0.9"})

    assert '<html lang="de">' in page.text
    assert "Schachstellungen erkennen, parsen und erzeugen" in page.text


def test_language_toggle_rejects_unknown_locale() -> None:
    response = make_client().get("/lang/fr?next=/", follow_redirects=False)

    assert response.status_code == 404


def test_language_toggle_blocks_open_redirects() -> None:
    external = make_client().get(
        "/lang/en?next=https://evil.example", follow_redirects=False
    )
    protocol_relative = make_client().get(
        "/lang/en?next=//evil.example", follow_redirects=False
    )

    assert external.headers["location"] == "/"
    assert protocol_relative.headers["location"] == "/"


def test_language_toggle_preserves_query_string() -> None:
    client = make_client()

    page = client.get("/?game=1")
    assert "next=/%3Fgame%3D1" in page.text

    redirect = client.get("/lang/en?next=/?game=1", follow_redirects=False)
    assert redirect.headers["location"] == "/?game=1"


def test_static_css_is_served() -> None:
    response = make_client().get("/static/css/app.css")

    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
