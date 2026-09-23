"""Instagram Graph API client: Send API, User Profile, media download, token refresh (06 §1).

Endpoints, bodies and limits follow docs/research/instagram.md §5, §7, §9. No network is used:
httpx is mocked with respx and every response body is a recorded-style example from the research doc.
The 24-hour messaging window rule (06 §1, research §6) is checked at the end.
"""

import itertools
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import IntegrationError, IntegrationNotConfiguredError
from app.core.logging import MASK
from app.integrations.instagram import (
    MAX_TEXT_BYTES,
    MESSAGING_WINDOW,
    InstagramAPIError,
    InstagramClient,
    InstagramMediaTooLargeError,
    InstagramMessenger,
    InstagramNotConfiguredError,
    get_instagram_client,
    messaging_window_expires_at,
    messaging_window_open,
    split_text,
)
from app.integrations.instagram.client import API_TIMEOUT, DOWNLOAD_TIMEOUT, MAX_MEDIA_BYTES, USER_AGENT

TOKEN = "IGAAtest-long-lived-access-token-0123456789abcdef"
NEW_TOKEN = "IGAAtest-refreshed-access-token-fedcba9876543210"
ACCOUNT_ID = "17841400000000000"
CUSTOMER_ID = "1234567890123456"

GRAPH_URL = "https://graph.instagram.com"
API_BASE = f"{GRAPH_URL}/v25.0"
MESSAGES_URL = f"{API_BASE}/{ACCOUNT_ID}/messages"
PROFILE_URL = f"{API_BASE}/{CUSTOMER_ID}"
REFRESH_URL = f"{GRAPH_URL}/refresh_access_token"
CDN_URL = "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=1&signature=abc"
AUDIO_URL = "https://files.example-bakery.tj/voice/reply-8812.m4a"

VOICE_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"voice-payload" * 8
LOGGER_NAME = "app.integrations.instagram.client"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"INSTAGRAM_ACCESS_TOKEN": TOKEN, "INSTAGRAM_ACCOUNT_ID": ACCOUNT_ID}
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture(name="ig")
def ig_fixture() -> Iterator[InstagramClient]:
    with InstagramClient(make_settings()) as client:
        yield client


def sent(message_id: str = "aWdfZAG1faXRlbToxOklHTWVzc2FnZAUlEOjE3ODQz...") -> httpx.Response:
    """Send API success body (research §5)."""
    return httpx.Response(200, json={"recipient_id": CUSTOMER_ID, "message_id": message_id})


def graph_error(status: int, **error: Any) -> httpx.Response:
    return httpx.Response(status, json={"error": error})


def body_of(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content.decode("utf-8"))


# --------------------------------------------------------------------------- send_text


def test_send_text_request_shape(respx_mock: Any) -> None:
    text = "Здравствуйте! Торт «Наполеон» 1 кг на субботу можно. Подтвердите, пожалуйста, время доставки."
    route = respx_mock.post(MESSAGES_URL).mock(return_value=sent("mid.1"))

    with InstagramClient(make_settings()) as client:
        assert client.send_text(CUSTOMER_ID, text) == ["mid.1"]

    request = route.calls.last.request
    assert request.method == "POST"
    assert str(request.url) == MESSAGES_URL
    assert request.url.query == b""  # the token never travels in the URL
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["user-agent"] == USER_AGENT
    assert body_of(request) == {"recipient": {"id": CUSTOMER_ID}, "message": {"text": text}}
    assert request.extensions["timeout"] == {"connect": 5.0, "read": 20.0, "write": 20.0, "pool": 20.0}


def test_api_version_and_host_come_from_settings(respx_mock: Any) -> None:
    route = respx_mock.post("https://graph.instagram.com/v26.0/999/messages").mock(return_value=sent("mid.v26"))
    settings = make_settings(
        INSTAGRAM_API_VERSION="v26.0", INSTAGRAM_ACCOUNT_ID="999", INSTAGRAM_GRAPH_URL=GRAPH_URL + "/"
    )

    with InstagramClient(settings) as client:
        assert client.send_text(CUSTOMER_ID, "Готово") == ["mid.v26"]
    assert route.call_count == 1


LONG_RU = " ".join(
    f"Заказ номер {i}: торт «Наполеон» весом один килограмм, доставка по Душанбе в субботу утром."
    for i in range(1, 26)
)
LONG_TG = " ".join(
    f"Фармоиши рақами {i}: торти «Наполеон» бо вазни як килограмм, расонидан дар Душанбе рӯзи шанбе."
    for i in range(1, 26)
)


def numbered_ids(respx_mock: Any) -> Any:
    """Send API route answering with mid.1, mid.2, ... so ordering can be checked."""
    counter = itertools.count(1)
    return respx_mock.post(MESSAGES_URL).mock(side_effect=lambda request: sent(f"mid.{next(counter)}"))


@pytest.mark.parametrize("text", [LONG_RU, LONG_TG])
def test_send_text_splits_long_cyrillic_text_at_sentence_boundaries(respx_mock: Any, text: str) -> None:
    assert len(text.encode("utf-8")) > 3 * MAX_TEXT_BYTES
    route = numbered_ids(respx_mock)

    with InstagramClient(make_settings()) as client:
        message_ids = client.send_text(CUSTOMER_ID, text)

    chunks = [body_of(call.request)["message"]["text"] for call in route.calls]
    assert len(chunks) == len(message_ids) > 3
    assert message_ids == [f"mid.{index}" for index in range(1, len(chunks) + 1)]
    assert all(len(chunk.encode("utf-8")) <= MAX_TEXT_BYTES for chunk in chunks)
    assert " ".join(chunks) == text  # order preserved, nothing lost or duplicated
    assert all(chunk.endswith(".") for chunk in chunks)  # cut at sentence ends, not mid-word


COMBINING_ACUTE = "e" + chr(0x0301)  # a base letter plus its combining mark: one user-perceived character


@pytest.mark.parametrize("piece", ["🎂", "👨‍👩‍👧‍👦", "🇹🇯", "ҳ", "ӯ", COMBINING_ACUTE])
def test_send_text_never_breaks_a_character_or_an_emoji_sequence(respx_mock: Any, piece: str) -> None:
    """Cyrillic and Tajik letters are 2 bytes, emoji 4, ZWJ sequences and combining marks more."""
    text = piece * 600
    assert len(text.encode("utf-8")) > MAX_TEXT_BYTES
    route = numbered_ids(respx_mock)

    with InstagramClient(make_settings()) as client:
        client.send_text(CUSTOMER_ID, text)

    chunks = [body_of(call.request)["message"]["text"] for call in route.calls]
    assert len(chunks) > 1
    assert b"".join(chunk.encode("utf-8") for chunk in chunks) == text.encode("utf-8")
    assert all(len(chunk.encode("utf-8")) <= MAX_TEXT_BYTES for chunk in chunks)
    assert all(chunk.replace(piece, "") == "" for chunk in chunks)  # only whole sequences in every part


def test_send_text_keeps_short_text_in_one_message(respx_mock: Any, ig: InstagramClient) -> None:
    route = respx_mock.post(MESSAGES_URL).mock(return_value=sent("mid.1"))

    assert ig.send_text(CUSTOMER_ID, "  Спасибо за заказ!  ") == ["mid.1"]
    assert route.call_count == 1
    assert body_of(route.calls.last.request)["message"]["text"] == "Спасибо за заказ!"


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_send_text_rejects_empty_text_without_calling_the_api(respx_mock: Any, ig: InstagramClient, text: str) -> None:
    route = respx_mock.post(MESSAGES_URL).mock(return_value=sent())

    with pytest.raises(ValueError):
        ig.send_text(CUSTOMER_ID, text)
    assert route.call_count == 0


@pytest.mark.parametrize("recipient", ["", "   ", None])
def test_send_text_rejects_an_empty_recipient(respx_mock: Any, ig: InstagramClient, recipient: Any) -> None:
    route = respx_mock.post(MESSAGES_URL).mock(return_value=sent())

    with pytest.raises(ValueError):
        ig.send_text(recipient, "Привет")
    assert route.call_count == 0


def test_send_text_reports_parts_already_delivered(respx_mock: Any, ig: InstagramClient) -> None:
    respx_mock.post(MESSAGES_URL).mock(
        side_effect=[sent("mid.1"), graph_error(500, message="Internal error", code=2, fbtrace_id="A1")]
    )

    with pytest.raises(InstagramAPIError) as caught:
        ig.send_text(CUSTOMER_ID, LONG_RU)

    assert caught.value.partial_message_ids == ["mid.1"]
    assert caught.value.retryable is True


def test_send_text_without_message_id_raises(respx_mock: Any, ig: InstagramClient) -> None:
    respx_mock.post(MESSAGES_URL).mock(return_value=httpx.Response(200, json={"recipient_id": CUSTOMER_ID}))

    with pytest.raises(InstagramAPIError) as caught:
        ig.send_text(CUSTOMER_ID, "Привет")

    assert caught.value.retryable is False  # the message was probably delivered — never retry


# --------------------------------------------------------------------------- send_audio


def test_send_audio_request_shape(respx_mock: Any, ig: InstagramClient) -> None:
    route = respx_mock.post(MESSAGES_URL).mock(return_value=sent("mid.audio"))

    assert ig.send_audio(CUSTOMER_ID, AUDIO_URL) == "mid.audio"

    request = route.calls.last.request
    assert body_of(request) == {
        "recipient": {"id": CUSTOMER_ID},
        "message": {"attachment": {"type": "audio", "payload": {"url": AUDIO_URL}}},
    }
    assert request.headers["authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize("url", ["", "   ", "ftp://files.example-bakery.tj/a.m4a", "/media/a.m4a", None])
def test_send_audio_requires_a_public_http_url(respx_mock: Any, ig: InstagramClient, url: Any) -> None:
    route = respx_mock.post(MESSAGES_URL).mock(return_value=sent())

    with pytest.raises(ValueError):
        ig.send_audio(CUSTOMER_ID, url)
    assert route.call_count == 0


# --------------------------------------------------------------------------- user profile


def test_get_user_profile(respx_mock: Any, ig: InstagramClient) -> None:
    route = respx_mock.get(PROFILE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "Peter Chang",
                "username": "peter_chang_live",
                "profile_pic": "https://fbcdn-profile-...",
                "follower_count": 1234,
            },
        )
    )

    assert ig.get_user_profile(CUSTOMER_ID) == {"name": "Peter Chang", "username": "peter_chang_live"}

    request = route.calls.last.request
    assert request.method == "GET"
    assert dict(request.url.params) == {"fields": "name,username"}  # no access_token in the URL
    assert request.headers["authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"name": None, "username": "bakery_client"}, {"name": None, "username": "bakery_client"}),
        ({"username": "bakery_client"}, {"name": None, "username": "bakery_client"}),
        ({"name": "  Мадина  ", "username": "  "}, {"name": "Мадина", "username": None}),
        ({}, {"name": None, "username": None}),
    ],
)
def test_get_user_profile_optional_fields(
    respx_mock: Any, ig: InstagramClient, body: dict[str, Any], expected: dict[str, str | None]
) -> None:
    respx_mock.get(PROFILE_URL).mock(return_value=httpx.Response(200, json=body))

    assert ig.get_user_profile(CUSTOMER_ID) == expected


@pytest.mark.parametrize("status", [400, 403, 404, 429])
def test_get_user_profile_returns_none_on_4xx(
    respx_mock: Any, ig: InstagramClient, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    respx_mock.get(PROFILE_URL).mock(
        return_value=graph_error(status, message="Unsupported get request", code=100, error_subcode=33, fbtrace_id="Zz")
    )

    assert ig.get_user_profile(CUSTOMER_ID) is None

    events = [getattr(record, "event", None) for record in caplog.records]
    assert "instagram.profile_unavailable" in events


def test_get_user_profile_raises_on_5xx(respx_mock: Any, ig: InstagramClient) -> None:
    respx_mock.get(PROFILE_URL).mock(return_value=httpx.Response(502, text="Bad Gateway"))

    with pytest.raises(InstagramAPIError) as caught:
        ig.get_user_profile(CUSTOMER_ID)

    assert caught.value.retryable is True


def test_get_user_profile_needs_only_the_token(respx_mock: Any) -> None:
    route = respx_mock.get(PROFILE_URL).mock(return_value=httpx.Response(200, json={"username": "client"}))

    with InstagramClient(make_settings(INSTAGRAM_ACCOUNT_ID="")) as client:
        assert client.get_user_profile(CUSTOMER_ID) == {"name": None, "username": "client"}
    assert route.call_count == 1


# --------------------------------------------------------------------------- errors


@pytest.mark.parametrize(
    ("status", "error", "retryable"),
    [
        (500, {"message": "Internal", "code": 1}, True),
        (503, {"message": "Service unavailable", "code": 2}, True),
        (429, {"message": "Too many calls", "code": 4, "error_subcode": 2207051}, True),
        (400, {"message": "Application request limit reached", "code": 4}, True),
        (400, {"message": "User request limit reached", "code": 17}, True),
        (400, {"message": "Page request limit reached", "code": 32}, True),
        (400, {"message": "Custom rate limit", "code": 613}, True),
        (400, {"message": "Instagram messaging throttled", "code": 80002}, True),  # research §9
        (400, {"message": "Temporary issue", "code": 2, "is_transient": True}, True),
        (400, {"message": "Session expired", "code": 190, "error_subcode": 463, "type": "OAuthException"}, False),
        (400, {"message": "Unsupported request", "code": 100}, False),
        (403, {"message": "Permission denied", "code": 10}, False),
        (404, {"message": "Unknown path", "code": 803}, False),
    ],
)
def test_error_mapping(
    respx_mock: Any, ig: InstagramClient, status: int, error: dict[str, Any], retryable: bool
) -> None:
    respx_mock.post(MESSAGES_URL).mock(return_value=graph_error(status, fbtrace_id="AbCd1234", **error))

    with pytest.raises(InstagramAPIError) as caught:
        ig.send_text(CUSTOMER_ID, "Привет")

    exc = caught.value
    assert isinstance(exc, IntegrationError)
    assert (exc.status, exc.error_code, exc.error_subcode) == (status, error["code"], error.get("error_subcode"))
    assert exc.fbtrace_id == "AbCd1234"
    assert exc.error_type == error.get("type")
    assert exc.retryable is retryable
    assert exc.message == error["message"]
    assert exc.code == "instagram_api_error"  # machine code of the HTTP error body
    assert exc.status_code == 502
    assert exc.to_dict()["retryable"] is retryable
    assert exc.partial_message_ids == []
    assert str(status) in str(exc)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="<html>502 Bad Gateway</html>"),
        httpx.Response(500, json={"error": "строка вместо объекта"}),
        httpx.Response(400, json={"error": {"code": "80002"}}),  # numeric code as a string
        httpx.Response(400, content=b"\xff\xfe not json"),
    ],
)
def test_error_bodies_that_are_not_graph_errors(respx_mock: Any, ig: InstagramClient, response: httpx.Response) -> None:
    respx_mock.post(MESSAGES_URL).mock(return_value=response)

    with pytest.raises(InstagramAPIError) as caught:
        ig.send_text(CUSTOMER_ID, "Привет")

    assert caught.value.status == response.status_code
    assert caught.value.retryable is (response.status_code >= 500 or caught.value.error_code == 80002)


@pytest.mark.parametrize("side_effect", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_network_errors_are_retryable(respx_mock: Any, ig: InstagramClient, side_effect: type[Exception]) -> None:
    respx_mock.post(MESSAGES_URL).mock(side_effect=side_effect)

    with pytest.raises(InstagramAPIError) as caught:
        ig.send_text(CUSTOMER_ID, "Привет")

    assert caught.value.status is None
    assert caught.value.retryable is True
    assert side_effect.__name__ in caught.value.message


def test_access_token_is_never_exposed_by_an_error(respx_mock: Any, ig: InstagramClient) -> None:
    respx_mock.post(MESSAGES_URL).mock(
        return_value=graph_error(400, message=f"Invalid OAuth access token: {TOKEN}", code=190)
    )

    with pytest.raises(InstagramAPIError) as caught:
        ig.send_text(CUSTOMER_ID, "Привет")

    dumped = f"{caught.value!s} {caught.value!r} {caught.value.to_dict()} {caught.value.detail}"
    assert TOKEN not in dumped
    assert MASK in caught.value.message


def test_long_error_messages_are_truncated(respx_mock: Any, ig: InstagramClient) -> None:
    respx_mock.post(MESSAGES_URL).mock(return_value=graph_error(400, message="э" * 5000, code=100))

    with pytest.raises(InstagramAPIError) as caught:
        ig.send_text(CUSTOMER_ID, "Привет")

    assert len(caught.value.message) <= 300


# --------------------------------------------------------------------------- not configured


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        (
            {"INSTAGRAM_ACCESS_TOKEN": "", "INSTAGRAM_ACCOUNT_ID": ""},
            ["INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_ACCOUNT_ID"],
        ),
        ({"INSTAGRAM_ACCOUNT_ID": "  "}, ["INSTAGRAM_ACCOUNT_ID"]),
        ({"INSTAGRAM_ACCESS_TOKEN": "   "}, ["INSTAGRAM_ACCESS_TOKEN"]),
    ],
)
def test_send_without_configuration(respx_mock: Any, overrides: dict[str, Any], missing: list[str]) -> None:
    route = respx_mock.post(MESSAGES_URL).mock(return_value=sent())

    with InstagramClient(make_settings(**overrides)) as client:
        assert client.is_configured is False
        with pytest.raises(InstagramNotConfiguredError) as caught:
            client.send_text(CUSTOMER_ID, "Привет")
        with pytest.raises(InstagramNotConfiguredError):
            client.send_audio(CUSTOMER_ID, AUDIO_URL)

    assert isinstance(caught.value, IntegrationNotConfiguredError)
    assert caught.value.status_code == 503
    assert caught.value.missing == missing
    assert TOKEN not in f"{caught.value!r} {caught.value.to_dict()} {caught.value.detail}"
    assert route.call_count == 0


def test_profile_and_refresh_without_a_token(respx_mock: Any) -> None:
    route = respx_mock.get(PROFILE_URL).mock(return_value=httpx.Response(200, json={}))
    client = InstagramClient(make_settings(INSTAGRAM_ACCESS_TOKEN=""))

    with pytest.raises(InstagramNotConfiguredError) as caught:
        client.get_user_profile(CUSTOMER_ID)
    with pytest.raises(InstagramNotConfiguredError):
        client.refresh_long_lived_token()
    client.close()

    assert caught.value.missing == ["INSTAGRAM_ACCESS_TOKEN"]  # the account id is not needed here
    assert route.call_count == 0


# --------------------------------------------------------------------------- media download


def test_download_attachment(respx_mock: Any, ig: InstagramClient) -> None:
    route = respx_mock.get(CDN_URL).mock(
        return_value=httpx.Response(200, content=VOICE_BYTES, headers={"Content-Type": "audio/mp4; codecs=mp4a.40.2"})
    )

    content, content_type = ig.download_attachment(CDN_URL)

    assert content == VOICE_BYTES
    assert content_type == "audio/mp4"  # parameters stripped, lowercased
    request = route.calls.last.request
    assert "authorization" not in request.headers  # no credentials are sent to the CDN
    assert request.extensions["timeout"] == {"connect": 10.0, "read": 60.0, "write": 60.0, "pool": 60.0}


def test_download_attachment_without_content_type(respx_mock: Any, ig: InstagramClient) -> None:
    respx_mock.get(CDN_URL).mock(return_value=httpx.Response(200, content=VOICE_BYTES, headers={"Content-Type": ""}))

    assert ig.download_attachment(CDN_URL)[1] == "application/octet-stream"


def test_download_attachment_follows_redirects(respx_mock: Any, ig: InstagramClient) -> None:
    respx_mock.get(CDN_URL).mock(return_value=httpx.Response(302, headers={"Location": "https://cdn.example.tj/a.mp4"}))
    respx_mock.get("https://cdn.example.tj/a.mp4").mock(
        return_value=httpx.Response(200, content=VOICE_BYTES, headers={"Content-Type": "audio/mp4"})
    )

    assert ig.download_attachment(CDN_URL) == (VOICE_BYTES, "audio/mp4")


def test_download_attachment_rejects_a_declared_size_over_the_limit(respx_mock: Any, ig: InstagramClient) -> None:
    route = respx_mock.get(CDN_URL).mock(
        return_value=httpx.Response(
            200, content=b"x" * 100, headers={"Content-Length": str(MAX_MEDIA_BYTES + 1), "Content-Type": "audio/mp4"}
        )
    )

    with pytest.raises(InstagramMediaTooLargeError) as caught:
        ig.download_attachment(CDN_URL)

    assert caught.value.size == MAX_MEDIA_BYTES + 1
    assert caught.value.max_bytes == MAX_MEDIA_BYTES
    assert caught.value.retryable is False
    assert route.call_count == 1


def test_download_attachment_stops_a_stream_over_the_limit(respx_mock: Any, ig: InstagramClient) -> None:
    """A chunked response (no Content-Length) is abandoned as soon as the guard trips."""
    pulled: list[int] = []

    def chunks() -> Iterator[bytes]:
        for index in range(20):
            pulled.append(index)
            yield b"x" * (100 * 1024)

    respx_mock.get(CDN_URL).mock(
        return_value=httpx.Response(200, content=chunks(), headers={"Content-Type": "audio/mp4"})
    )

    with pytest.raises(InstagramMediaTooLargeError) as caught:
        ig.download_attachment(CDN_URL, max_bytes=150 * 1024)

    assert caught.value.size is None  # the size was not declared
    assert caught.value.max_bytes == 150 * 1024
    assert len(pulled) < 20  # the download is abandoned, not read to the end


def test_download_attachment_size_limit_is_25mb() -> None:
    assert MAX_MEDIA_BYTES == 25 * 1024 * 1024


@pytest.mark.parametrize("url", ["http://lookaside.fbsbx.com/media", "ftp://host/a.mp4", "/media/a.mp4", "", None, 5])
def test_download_attachment_requires_https(respx_mock: Any, ig: InstagramClient, url: Any) -> None:
    route = respx_mock.get(CDN_URL).mock(return_value=httpx.Response(200, content=VOICE_BYTES))

    with pytest.raises(InstagramAPIError) as caught:
        ig.download_attachment(url)

    assert caught.value.retryable is False
    assert route.call_count == 0


@pytest.mark.parametrize(("status", "retryable"), [(404, False), (403, False), (410, False), (429, True), (500, True)])
def test_download_attachment_http_errors(
    respx_mock: Any, ig: InstagramClient, status: int, retryable: bool
) -> None:
    respx_mock.get(CDN_URL).mock(return_value=httpx.Response(status, text="no"))

    with pytest.raises(InstagramAPIError) as caught:
        ig.download_attachment(CDN_URL)

    assert caught.value.status == status
    assert caught.value.retryable is retryable


def test_download_attachment_empty_body(respx_mock: Any, ig: InstagramClient) -> None:
    respx_mock.get(CDN_URL).mock(return_value=httpx.Response(200, content=b""))

    with pytest.raises(InstagramAPIError) as caught:
        ig.download_attachment(CDN_URL)

    assert "empty" in caught.value.message


def test_download_attachment_network_error(respx_mock: Any, ig: InstagramClient) -> None:
    respx_mock.get(CDN_URL).mock(side_effect=httpx.ReadTimeout)

    with pytest.raises(InstagramAPIError) as caught:
        ig.download_attachment(CDN_URL)

    assert caught.value.retryable is True


@pytest.mark.parametrize("max_bytes", [0, -1])
def test_download_attachment_invalid_limit(respx_mock: Any, ig: InstagramClient, max_bytes: int) -> None:
    route = respx_mock.get(CDN_URL).mock(return_value=httpx.Response(200, content=VOICE_BYTES))

    with pytest.raises(ValueError):
        ig.download_attachment(CDN_URL, max_bytes=max_bytes)
    assert route.call_count == 0


def test_media_url_and_size_are_logged_without_the_query_string(
    respx_mock: Any, ig: InstagramClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    respx_mock.get(CDN_URL).mock(
        return_value=httpx.Response(200, content=VOICE_BYTES, headers={"Content-Type": "audio/mp4"})
    )

    ig.download_attachment(CDN_URL)

    record = next(r for r in caplog.records if getattr(r, "event", None) == "instagram.media_downloaded")
    assert record.host == "lookaside.fbsbx.com"
    assert record.size_bytes == len(VOICE_BYTES)
    assert "signature=abc" not in str(record.__dict__)


# --------------------------------------------------------------------------- token refresh


def test_refresh_long_lived_token(respx_mock: Any, ig: InstagramClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    refreshed = {"access_token": NEW_TOKEN, "token_type": "bearer", "expires_in": 5183944}  # research §3.4
    route = respx_mock.get(REFRESH_URL).mock(return_value=httpx.Response(200, json=refreshed))

    assert ig.refresh_long_lived_token() == (NEW_TOKEN, 5183944)

    request = route.calls.last.request
    assert request.url.path == "/refresh_access_token"  # no API version on this endpoint (research §3.4)
    assert dict(request.url.params) == {"grant_type": "ig_refresh_token", "access_token": TOKEN}
    assert "authorization" not in request.headers  # documented as a query parameter here
    record = next(r for r in caplog.records if getattr(r, "event", None) == "instagram.token_refreshed")
    assert record.expires_in == 5183944
    dumped = " ".join(str(r.__dict__) for r in caplog.records)
    assert NEW_TOKEN not in dumped and TOKEN not in dumped


@pytest.mark.parametrize(
    "body",
    [
        {"token_type": "bearer", "expires_in": 5183944},
        {"access_token": "   ", "expires_in": 5183944},
        {"access_token": NEW_TOKEN},
        {"access_token": NEW_TOKEN, "expires_in": 0},
        {"access_token": NEW_TOKEN, "expires_in": "скоро"},
        [],
    ],
)
def test_refresh_long_lived_token_invalid_response(respx_mock: Any, ig: InstagramClient, body: Any) -> None:
    respx_mock.get(REFRESH_URL).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(InstagramAPIError):
        ig.refresh_long_lived_token()


def test_refresh_long_lived_token_expired(respx_mock: Any, ig: InstagramClient) -> None:
    respx_mock.get(REFRESH_URL).mock(
        return_value=graph_error(
            400, message="Error validating access token: Session has expired", code=190, error_subcode=463
        )
    )

    with pytest.raises(InstagramAPIError) as caught:
        ig.refresh_long_lived_token()

    assert caught.value.retryable is False  # the owner has to log in again


def test_a_fresher_token_can_be_passed_explicitly(respx_mock: Any) -> None:
    route = respx_mock.post(MESSAGES_URL).mock(return_value=sent("mid.1"))

    with InstagramClient(make_settings(), access_token=NEW_TOKEN) as client:
        client.send_text(CUSTOMER_ID, "Привет")

    assert route.calls.last.request.headers["authorization"] == f"Bearer {NEW_TOKEN}"


# --------------------------------------------------------------------------- logging & lifecycle


def test_logs_never_contain_the_token_or_the_message_text(
    respx_mock: Any, ig: InstagramClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    text = "Здравствуйте, Мадина! Ваш заказ на медовик готов, курьер будет в 18:00."
    respx_mock.post(MESSAGES_URL).mock(side_effect=[sent("mid.1"), graph_error(500, message="Internal", code=2)])

    ig.send_text(CUSTOMER_ID, text)
    with pytest.raises(InstagramAPIError):
        ig.send_text(CUSTOMER_ID, text)

    events = [getattr(record, "event", None) for record in caplog.records]
    assert {"instagram.request", "instagram.response", "instagram.message_sent", "instagram.error"} <= set(events)
    dumped = " ".join(str(record.__dict__) for record in caplog.records)
    assert TOKEN not in dumped
    assert text not in dumped
    assert "Мадина" not in dumped
    assert "/v25.0/" in dumped  # the request path is logged, the query string is not


def test_injected_http_client_is_used_and_not_closed(respx_mock: Any) -> None:
    route = respx_mock.post(MESSAGES_URL).mock(return_value=sent("mid.1"))
    http = httpx.Client()

    try:
        with InstagramClient(make_settings(), http=http) as client:
            client.send_text(CUSTOMER_ID, "Привет")
        assert not http.is_closed  # a shared pooled client is never closed by the adapter
        assert route.call_count == 1
    finally:
        http.close()


def test_get_instagram_client_factory(respx_mock: Any) -> None:
    route = respx_mock.post(MESSAGES_URL).mock(return_value=sent("mid.1"))

    with get_instagram_client(make_settings()) as client:
        assert client.is_configured is True
        client.send_text(CUSTOMER_ID, "Привет")

    assert route.call_count == 1


class FakeMessenger:
    """A service-level fake: what other tracks may inject instead of the real client."""

    def send_text(self, recipient_id: str, text: str) -> list[str]:
        return ["mid.fake"]

    def send_audio(self, recipient_id: str, url: str) -> str:
        return "mid.fake"

    def send_image(self, recipient_id: str, url: str) -> str:
        return "mid.fake"

    def get_user_profile(self, igsid: str) -> dict[str, str | None] | None:
        return None

    def download_attachment(self, url: str, max_bytes: int = MAX_MEDIA_BYTES) -> tuple[bytes, str]:
        return b"", "application/octet-stream"

    def refresh_long_lived_token(self) -> tuple[str, int]:
        return "token", 1


def test_client_and_fakes_satisfy_the_messenger_protocol() -> None:
    with InstagramClient(make_settings()) as client:
        assert isinstance(client, InstagramMessenger)
    assert isinstance(FakeMessenger(), InstagramMessenger)
    assert not isinstance(object(), InstagramMessenger)


def test_timeout_constants() -> None:
    assert (API_TIMEOUT.connect, API_TIMEOUT.read) == (5.0, 20.0)
    assert (DOWNLOAD_TIMEOUT.connect, DOWNLOAD_TIMEOUT.read) == (10.0, 60.0)
    assert MAX_TEXT_BYTES == 1000


# --------------------------------------------------------------------------- text splitting


def test_split_text_prefers_paragraphs() -> None:
    first = "Здравствуйте! " + "Это первый абзац про торты. " * 9
    second = "А это второй абзац про доставку. " * 9
    assert max(len(first.encode()), len(second.encode())) < MAX_TEXT_BYTES < len(f"{first}{second}".encode())
    chunks = split_text(f"{first}\n\n{second}")

    assert len(chunks) == 2
    assert chunks[0] == first.strip()
    assert chunks[1] == second.strip()


def test_split_text_falls_back_to_words_without_punctuation() -> None:
    text = " ".join(["слово"] * 300)  # 11 bytes per word, no sentence boundaries
    chunks = split_text(text)

    assert len(chunks) > 3
    assert " ".join(chunks) == text
    assert all(len(chunk.encode("utf-8")) <= MAX_TEXT_BYTES for chunk in chunks)
    assert all(chunk.startswith("слово") and chunk.endswith("слово") for chunk in chunks)


def test_split_text_handles_one_very_long_word() -> None:
    text = "ҳ" * 900  # 1800 bytes, no separators at all
    chunks = split_text(text)

    assert "".join(chunks) == text
    assert all(len(chunk.encode("utf-8")) <= MAX_TEXT_BYTES for chunk in chunks)


@pytest.mark.parametrize("limit", [4, 7, 33, 100, 999, 1000])
def test_split_text_respects_any_limit(limit: int) -> None:
    text = "Салом! Ман як торти «Наполеон» мехоҳам. " * 30
    chunks = split_text(text, limit)

    assert all(len(chunk.encode("utf-8")) <= limit for chunk in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_split_text_rejects_a_tiny_limit() -> None:
    with pytest.raises(ValueError):
        split_text("Привет", 3)


# --------------------------------------------------------------------------- 24-hour messaging window


NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def test_messaging_window_is_24_hours() -> None:
    assert MESSAGING_WINDOW == timedelta(hours=24)


@pytest.mark.parametrize(
    ("last_message", "expected"),
    [
        (None, False),
        (NOW, True),
        (NOW - timedelta(minutes=1), True),
        (NOW - timedelta(hours=23, minutes=59, seconds=59), True),
        (NOW - timedelta(hours=24) + timedelta(milliseconds=1), True),
        (NOW - timedelta(hours=24), False),  # exactly 24 hours: closed
        (NOW - timedelta(hours=24, seconds=1), False),
        (NOW - timedelta(days=7), False),
        (NOW + timedelta(minutes=5), True),  # clock skew: treated as open
    ],
)
def test_messaging_window_open(last_message: datetime | None, expected: bool) -> None:
    assert messaging_window_open(last_message, NOW) is expected


def test_messaging_window_treats_naive_datetimes_as_utc() -> None:
    naive = (NOW - timedelta(hours=1)).replace(tzinfo=None)

    assert messaging_window_open(naive, NOW) is True
    assert messaging_window_open(naive, NOW + timedelta(hours=23)) is False
    assert messaging_window_expires_at(naive) == NOW + timedelta(hours=23)


def test_messaging_window_accepts_other_timezones() -> None:
    dushanbe = NOW.astimezone(ZoneInfo("Asia/Dushanbe"))

    assert messaging_window_open(dushanbe, NOW + timedelta(hours=23)) is True
    assert messaging_window_open(dushanbe, NOW + timedelta(hours=25)) is False


def test_messaging_window_defaults_to_now(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import time as app_time

    monkeypatch.setattr(app_time, "now_utc", lambda: NOW)

    assert messaging_window_open(NOW - timedelta(hours=23)) is True
    assert messaging_window_open(NOW - timedelta(hours=25)) is False
    assert messaging_window_open(None) is False


def test_messaging_window_expires_at() -> None:
    assert messaging_window_expires_at(None) is None
    assert messaging_window_expires_at(NOW) == NOW + timedelta(hours=24)
