"""FAQ API and FaqService (04-api.md §10, SPEC §14)."""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models.faq import FaqItem
from app.services.faq_service import FaqService

URL = "/api/faq"


@pytest.fixture
def make_faq(db: Session) -> Callable[..., FaqItem]:
    def _make(question: str = "Есть ли доставка?", answer: str = "Да, по Душанбе.", **fields: Any) -> FaqItem:
        item = FaqItem(question=question, answer=answer, **fields)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    return _make


def create_faq(client: TestClient, headers: dict[str, str], **payload: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"question": "Есть ли доставка?", "answer": "Да, по Душанбе.", **payload}
    response = client.post(URL, json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def questions(items: list[dict[str, Any]]) -> list[str]:
    return [item["question"] for item in items]


# --------------------------------------------------------------------------- auth & roles


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", URL, None),
        ("post", URL, {"question": "Вопрос", "answer": "Ответ"}),
        ("patch", f"{URL}/1", {"answer": "Ответ"}),
        ("delete", f"{URL}/1", None),
    ],
)
def test_requires_authentication(client: TestClient, method: str, path: str, body: Any) -> None:
    response = client.request(method, path, json=body)

    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_operator_can_read_faq(
    client: TestClient, operator_headers: dict[str, str], make_faq: Callable[..., FaqItem]
) -> None:
    make_faq()

    response = client.get(URL, headers=operator_headers)

    assert response.status_code == 200
    assert questions(response.json()) == ["Есть ли доставка?"]


@pytest.mark.parametrize(
    ("method", "body"),
    [("post", {"question": "Вопрос", "answer": "Ответ"}), ("patch", {"answer": "Ответ"}), ("delete", None)],
)
def test_operator_cannot_write(
    client: TestClient,
    operator_headers: dict[str, str],
    make_faq: Callable[..., FaqItem],
    method: str,
    body: Any,
) -> None:
    item = make_faq()
    path = URL if method == "post" else f"{URL}/{item.id}"

    response = client.request(method, path, json=body, headers=operator_headers)

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


# --------------------------------------------------------------------------- create


def test_create_applies_defaults(client: TestClient, admin_headers: dict[str, str]) -> None:
    data = create_faq(client, admin_headers)

    assert data["id"] > 0
    assert data["question"] == "Есть ли доставка?"
    assert data["answer"] == "Да, по Душанбе."
    assert data["question_tg"] is None
    assert data["answer_tg"] is None
    assert data["keywords"] == []
    assert data["is_active"] is True
    assert data["sort_order"] == 0
    assert data["created_at"] and data["updated_at"]


def test_create_trims_texts_and_normalizes_keywords(client: TestClient, admin_headers: dict[str, str]) -> None:
    data = create_faq(
        client,
        admin_headers,
        question="  Где самовывоз?  ",
        answer="  Улица Рудаки, 1  ",
        question_tg="   ",
        answer_tg="Кӯчаи Рӯдакӣ, 1",
        keywords=[" Самовывоз ", "САМОВЫВОЗ", "", "адрес"],
        sort_order=2,
    )

    assert data["question"] == "Где самовывоз?"
    assert data["answer"] == "Улица Рудаки, 1"
    assert data["question_tg"] is None
    assert data["answer_tg"] == "Кӯчаи Рӯдакӣ, 1"
    assert data["keywords"] == ["самовывоз", "адрес"]
    assert data["sort_order"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "Вопрос"},
        {"answer": "Ответ"},
        {"question": "   ", "answer": "Ответ"},
        {"question": "Вопрос", "answer": "  "},
        {"question": "Вопрос", "answer": "Ответ", "keywords": ["к" * 129]},
    ],
)
def test_create_rejects_invalid_payload(
    client: TestClient, admin_headers: dict[str, str], payload: dict[str, Any]
) -> None:
    response = client.post(URL, json=payload, headers=admin_headers)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# --------------------------------------------------------------------------- list


def test_list_is_ordered_by_sort_order_then_id(
    client: TestClient, admin_headers: dict[str, str], make_faq: Callable[..., FaqItem]
) -> None:
    make_faq("Третий", "Ответ", sort_order=5)
    first = make_faq("Первый", "Ответ", sort_order=0)
    second = make_faq("Второй", "Ответ", sort_order=0)

    response = client.get(URL, headers=admin_headers)

    assert questions(response.json()) == ["Первый", "Второй", "Третий"]
    assert [item["id"] for item in response.json()][:2] == [first.id, second.id]


def test_list_hides_inactive_unless_requested(
    client: TestClient, admin_headers: dict[str, str], make_faq: Callable[..., FaqItem]
) -> None:
    make_faq("Активный", "Ответ")
    make_faq("Выключенный", "Ответ", is_active=False)

    default = client.get(URL, headers=admin_headers)
    with_inactive = client.get(URL, params={"include_inactive": True}, headers=admin_headers)

    assert questions(default.json()) == ["Активный"]
    assert sorted(questions(with_inactive.json())) == ["Активный", "Выключенный"]


# --------------------------------------------------------------------------- update & delete


def test_update_changes_only_given_fields(
    client: TestClient, admin_headers: dict[str, str], make_faq: Callable[..., FaqItem]
) -> None:
    item = make_faq("Есть ли доставка?", "Да", question_tg="Расониш ҳаст?", keywords=["старое"])

    response = client.patch(
        f"{URL}/{item.id}",
        json={"answer": "  Да, по всему городу  ", "keywords": ["Доставка", "ДОСТАВКА", "расониш"]},
        headers=admin_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Да, по всему городу"
    assert data["question"] == "Есть ли доставка?"
    assert data["question_tg"] == "Расониш ҳаст?"
    assert data["keywords"] == ["доставка", "расониш"]


def test_update_can_clear_tajik_text(
    client: TestClient, admin_headers: dict[str, str], make_faq: Callable[..., FaqItem]
) -> None:
    item = make_faq(question_tg="Расониш ҳаст?", answer_tg="Ҳа")

    response = client.patch(f"{URL}/{item.id}", json={"question_tg": None, "answer_tg": ""}, headers=admin_headers)

    data = response.json()
    assert data["question_tg"] is None
    assert data["answer_tg"] is None


def test_update_unknown_item_returns_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.patch(f"{URL}/9999", json={"answer": "Ответ"}, headers=admin_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Вопрос не найден", "code": "not_found"}


def test_delete_removes_the_row(
    client: TestClient, admin_headers: dict[str, str], db: Session, make_faq: Callable[..., FaqItem]
) -> None:
    item = make_faq()
    faq_id = item.id

    response = client.delete(f"{URL}/{faq_id}", headers=admin_headers)

    assert response.status_code == 204
    assert response.content == b""
    db.expire_all()
    assert db.get(FaqItem, faq_id) is None
    assert client.get(URL, headers=admin_headers).json() == []


def test_delete_unknown_item_returns_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.delete(f"{URL}/9999", headers=admin_headers)

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


# --------------------------------------------------------------------------- service


def test_service_accepts_plain_mapping_and_reports_invalid_fields(db: Session) -> None:
    service = FaqService(db)

    item = service.create({"question": " Сколько стоит? ", "answer": "От 150 сомони", "keywords": ["ЦЕНА", "цена"]})

    assert item.question == "Сколько стоит?"
    assert item.keywords == ["цена"]

    with pytest.raises(ValidationError) as excinfo:
        service.create({"question": "", "answer": ""})
    assert excinfo.value.status_code == 422
    assert set(excinfo.value.details["fields"]) == {"question", "answer"}
