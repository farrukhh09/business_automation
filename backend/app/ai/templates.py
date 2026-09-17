"""Deterministic RU/TG reply templates (docs/architecture/05-ai.md §6).

Every ``ReplyPlan`` kind has a template here. Some kinds are *always* rendered by a template (the
order summary, the confirmation, cancellation and handoff messages — anything with numbers or with a
legal meaning); the others are worded by the LLM and fall back to these templates when the model is
unavailable or the ``ResponseGuard`` rejects its text.

Facts are plain JSON values built by ``DialogService`` from the database: money is a decimal string
(``"1500.00"``), dates are ISO ``YYYY-MM-DD``, times ``HH:MM``. Nothing in this module queries the
database or invents a value — a missing fact simply leaves its line out.

Tajik texts were written by the developer and still need a native speaker's review
(docs/PROGRESS.md, open question 4); that does not block the MVP.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.formatting import format_amount, format_date_ru
from app.services.phone import PHONE_EXAMPLE_INTERNATIONAL, PHONE_EXAMPLE_NATIONAL

__all__ = [
    "LANGUAGES",
    "MAX_QUESTIONS",
    "PAYMENT_STATUS_LABELS",
    "STATUS_LABELS",
    "field_question",
    "render",
    "voice_not_recognized",
]

RU = "ru"
TG = "tg"
LANGUAGES = (RU, TG)

CURRENCY = {RU: "сомони", TG: "сомонӣ"}
DEFAULT_UNIT_RU = "шт."
UNIT_TG = "дона"

STATUS_LABELS: dict[str, dict[str, str]] = {
    RU: {
        "NEW": "оформляется",
        "WAITING_CONFIRMATION": "ждёт подтверждения",
        "CONFIRMED": "подтверждён",
        "PREPARING": "готовится",
        "READY": "готов",
        "HANDED_TO_COURIER": "передан курьеру",
        "COMPLETED": "выполнен",
        "CANCELLED": "отменён",
    },
    TG: {
        "NEW": "ба расмият дароварда мешавад",
        "WAITING_CONFIRMATION": "интизори тасдиқ",
        "CONFIRMED": "тасдиқ шудааст",
        "PREPARING": "омода мешавад",
        "READY": "тайёр аст",
        "HANDED_TO_COURIER": "ба курер супорида шуд",
        "COMPLETED": "иҷро шуд",
        "CANCELLED": "бекор шуд",
    },
}

PAYMENT_STATUS_LABELS: dict[str, dict[str, str]] = {
    RU: {
        "UNPAID": "не оплачен",
        "PARTIALLY_PAID": "оплачен частично",
        "PAID": "оплачен",
        "REFUNDED": "оформлен возврат",
    },
    TG: {
        "UNPAID": "пардохт нашудааст",
        "PARTIALLY_PAID": "қисман пардохт шудааст",
        "PAID": "пардохт шудааст",
        "REFUNDED": "маблағ баргардонида шуд",
    },
}

PAYMENT_METHOD_LABELS: dict[str, dict[str, str]] = {
    RU: {"CASH": "наличными", "CARD": "картой", "TRANSFER": "переводом", "OTHER": "другим способом"},
    TG: {"CASH": "нақд", "CARD": "бо корт", "TRANSFER": "бо интиқол", "OTHER": "бо роҳи дигар"},
}

# 03 §1.3 field names → the question the bot asks (in asking order).
FIELD_QUESTIONS: dict[str, dict[str, str]] = {
    RU: {
        "items": "Что хотите заказать?",
        "delivery_date": "На какую дату нужен заказ?",
        "delivery_time": "К какому времени?",
        "delivery_type": "Это будет доставка или самовывоз?",
        "customer_name": "Как к вам можно обращаться?",
        "phone": "Напишите, пожалуйста, номер телефона для связи.",
        "address": "Напишите, пожалуйста, адрес доставки: район, улица, дом, квартира и ориентир.",
        "recipient_name": "Кто будет получать заказ? Напишите имя получателя.",
        "location": "Отметьте, пожалуйста, точку доставки на карте.",
    },
    TG: {
        "items": "Чӣ фармоиш додан мехоҳед?",
        "delivery_date": "Фармоиш барои кадом сана лозим аст?",
        "delivery_time": "Барои соати чанд?",
        "delivery_type": "Расонидан лозим аст ё худатон мегиред?",
        "customer_name": "Лутфан, номатонро нависед.",
        "phone": "Лутфан, рақами телефонатонро барои тамос нависед.",
        "address": "Лутфан, суроғаи расониданро нависед: ноҳия, кӯча, хона, ҳуҷра ва нишона.",
        "recipient_name": "Фармоишро кӣ мегирад? Номи гирандаро нависед.",
        "location": "Лутфан, нуқтаи расониданро дар харита қайд кунед.",
    },
}

_TEXTS: dict[str, dict[str, str]] = {
    RU: {
        "summary_title": "Проверьте, пожалуйста, заказ:",
        "date": "Дата",
        "time": "Время",
        "delivery_yes": "Доставка: да",
        "pickup": "Самовывоз",
        "pickup_yes": "Самовывоз: да",
        "address": "Адрес",
        "recipient": "Получатель",
        "payment": "Оплата",
        "comment": "Комментарий",
        "total": "Итого",
        "summary_question": "Всё верно? Напишите «Да», чтобы подтвердить.",
        "confirmed": "Спасибо! Заказ №{order_id} подтверждён ✅",
        "confirmed_delivery": "Доставка: {date} к {time}.",
        "confirmed_pickup": "Заказ можно будет забрать {date} в {time}.",
        "confirmation_repeat": (
            "Чтобы подтвердить заказ, напишите, пожалуйста, «Да». Если нужно что-то изменить — напишите, что именно."
        ),
        "confirm_reminder": "Чтобы подтвердить заказ №{order_id}, напишите «Да».",
        "what_to_change": "Хорошо. Напишите, пожалуйста, что нужно изменить в заказе.",
        "cancel_confirm": "Отменить заказ №{order_id}? Напишите «Да», чтобы отменить.",
        "cancelled": "Заказ №{order_id} отменён.",
        "cancel_kept": "Хорошо, заказ №{order_id} не отменяем.",
        "handoff": "Передаю диалог менеджеру, он скоро ответит.",
        "handoff_complaint": "Извините за неудобства.",
        "handoff_order_locked": "Заказ №{order_id} уже в работе — изменения согласует менеджер.",
        "need_manager": "Мне нужно уточнить эту информацию у менеджера.",
        "address_candidates": "Уточните, пожалуйста, адрес. Возможно, это один из вариантов:",
        "address_choose": "Напишите номер варианта или отметьте точку на карте: {link}",
        "address_choose_no_link": "Напишите номер подходящего варианта или уточните адрес.",
        "address_not_found": (
            "Не нашли этот адрес на карте 🙁 Напишите, пожалуйста, точнее: район или микрорайон, улицу, дом и ориентир"
        ),
        "address_link": " — или отметьте точку на карте: {link}",
        "address_approximate": (
            "Нашли на карте «{place}», но не сам дом. Отметьте, пожалуйста, точку на карте: {link} — "
            "так курьер точно вас найдёт."
        ),
        "address_approximate_no_link": "Нашли на карте «{place}», но не сам дом. Уточните, пожалуйста, дом и ориентир.",
        "address_failed": (
            "Сейчас не получается проверить адрес на карте — ничего страшного, курьер уточнит его по телефону."
        ),
        "address_failed_link": " Если удобно, отметьте точку на карте: {link}",
        "address_continue": (
            "Если неудобно открывать карту, напишите «дальше» — оформим заказ по адресу как есть, "
            "а курьер уточнит по телефону."
        ),
        "clarify_intro": "Уточните, пожалуйста:",
        "pending_generic": "Подскажите, пожалуйста, какие именно? Сейчас есть: {options}.",
        "pending_ambiguous": "Уточните, пожалуйста, какой именно товар вы имели в виду: {options}?",
        "pending_quantity": "Сколько штук нужно: {names}?",
        "unknown_products": "К сожалению, {names} нет в нашем каталоге.",
        "available_products": "Сейчас можно заказать: {names}.",
        "too_soon": (
            "Заказы принимаем не позднее чем за {hours} ч.{earliest} Выберите, пожалуйста, другую дату или время."
        ),
        "earliest": " Самое раннее — {when}.",
        "earliest_today": "сегодня после {time}",
        "earliest_tomorrow": "завтра после {time}",
        "earliest_date": "{date} после {time}",
        "date_past": "{date} уже прошло 🙂",
        "too_far": "Заказы принимаем не больше чем на {days} дн. вперёд. Выберите, пожалуйста, другую дату.",
        "phone_invalid": (
            "Номер телефона не получилось распознать. Напишите, пожалуйста, в формате "
            f"{PHONE_EXAMPLE_NATIONAL} или {PHONE_EXAMPLE_INTERNATIONAL}."
        ),
        "greeting": "Здравствуйте! Чем можем помочь? 😊",
        "small_talk_thanks": "Пожалуйста! Будем рады видеть вас снова 😊",
        "small_talk_goodbye": "Всего доброго! Пишите, если что-то понадобится.",
        "small_talk_ack": "Хорошо 👍 Если что-то понадобится — пишите.",
        "small_talk_chat": (
            "Мы небольшая домашняя пекарня 😊 Подскажите, чем можем помочь: торт, выпечка или доставка?"
        ),
        "products_intro": "Вот что у нас есть:",
        "no_active_orders": "У вас сейчас нет активных заказов.",
        "order_status_line": "Заказ №{order_id}: {status}",
        "payment_line": "оплата: {payment}",
        "working_hours": "Часы работы",
        "clarify": "Извините, не получилось понять сообщение. Уточните, пожалуйста, что вас интересует?",
        "voice_not_recognized": "Не получилось разобрать голосовое, напишите, пожалуйста, текстом.",
        "blocked": "К сожалению, в данное время мы не можем принять Ваш заказ.",
    },
    TG: {
        "summary_title": "Лутфан, фармоишро санҷед:",
        "date": "Сана",
        "time": "Вақт",
        "delivery_yes": "Расонидан: ҳа",
        "pickup": "Худатон мегиред",
        "pickup_yes": "Худатон мегиред: ҳа",
        "address": "Суроға",
        "recipient": "Гиранда",
        "payment": "Пардохт",
        "comment": "Шарҳ",
        "total": "Ҳамагӣ",
        "summary_question": "Ҳама дуруст? Барои тасдиқ «Ҳа» нависед.",
        "confirmed": "Ташаккур! Фармоиши №{order_id} тасдиқ шуд ✅",
        "confirmed_delivery": "Расонидан: {date}, соати {time}.",
        "confirmed_pickup": "Фармоишро {date}, соати {time} гирифтан мумкин аст.",
        "confirmation_repeat": (
            "Барои тасдиқи фармоиш, лутфан, «Ҳа» нависед. Агар чизеро иваз кардан лозим бошад — нависед, ки чиро."
        ),
        "confirm_reminder": "Барои тасдиқи фармоиши №{order_id} «Ҳа» нависед.",
        "what_to_change": "Хуб. Лутфан, нависед, ки дар фармоиш чиро иваз кардан лозим аст.",
        "cancel_confirm": "Фармоиши №{order_id}-ро бекор кунем? Барои бекор кардан «Ҳа» нависед.",
        "cancelled": "Фармоиши №{order_id} бекор карда шуд.",
        "cancel_kept": "Хуб, фармоиши №{order_id} бекор карда намешавад.",
        "handoff": "Суҳбатро ба менеҷер мегузаронам, ӯ ба зудӣ ҷавоб медиҳад.",
        "handoff_complaint": "Барои нороҳатӣ бубахшед.",
        "handoff_order_locked": "Фармоиши №{order_id} аллакай дар кор аст — тағйиротро менеҷер ҳамоҳанг мекунад.",
        "need_manager": "Ман бояд ин маълумотро аз менеҷер аниқ кунам.",
        "address_candidates": "Лутфан, суроғаро аниқ кунед. Шояд яке аз инҳо бошад:",
        "address_choose": "Рақами вариантро нависед ё нуқтаро дар харита қайд кунед: {link}",
        "address_choose_no_link": "Рақами варианти мувофиқро нависед ё суроғаро аниқ кунед.",
        "address_not_found": (
            "Ин суроғаро дар харита наёфтем 🙁 Лутфан, аниқтар нависед: ноҳия ё микрорайон, кӯча, хона ва нишона"
        ),
        "address_link": " — ё нуқтаро дар харита қайд кунед: {link}",
        "address_approximate": (
            "Дар харита «{place}»-ро ёфтем, аммо худи хонаро не. Лутфан, нуқтаро дар харита қайд кунед: {link} — "
            "то хаткашон шуморо дақиқ ёбад."
        ),
        "address_approximate_no_link": (
            "Дар харита «{place}»-ро ёфтем, аммо худи хонаро не. Лутфан, хона ва нишонаро аниқ кунед."
        ),
        "address_failed": (
            "Ҳоло суроғаро дар харита санҷида наметавонем — ҳеҷ гап не, хаткашон онро бо телефон аниқ мекунад."
        ),
        "address_failed_link": " Агар қулай бошад, нуқтаро дар харита қайд кунед: {link}",
        "address_continue": (
            "Агар кушодани харита нокулай бошад, «давом» нависед — фармоишро бо ҳамин суроға қабул мекунем, "
            "хаткашон бо телефон аниқ мекунад."
        ),
        "clarify_intro": "Лутфан, аниқ кунед:",
        "pending_generic": "Лутфан, бигӯед, кадомашро мехоҳед? Ҳоло дорем: {options}.",
        "pending_ambiguous": "Лутфан, аниқ кунед, кадом маҳсулотро дар назар доред: {options}?",
        "pending_quantity": "Чанд дона лозим аст: {names}?",
        "unknown_products": "Мутаассифона, {names} дар каталоги мо нест.",
        "available_products": "Ҳоло инҳоро фармоиш додан мумкин аст: {names}.",
        "too_soon": (
            "Фармоишҳоро на дертар аз {hours} соат пеш қабул мекунем.{earliest} "
            "Лутфан, сана ё вақти дигарро интихоб кунед."
        ),
        "earliest": " Барвақттарин — {when}.",
        "earliest_today": "имрӯз баъд аз соати {time}",
        "earliest_tomorrow": "пагоҳ баъд аз соати {time}",
        "earliest_date": "{date} баъд аз соати {time}",
        "date_past": "{date} аллакай гузашт 🙂",
        "too_far": "Фармоишҳоро на бештар аз {days} рӯз пеш қабул мекунем. Лутфан, санаи дигарро интихоб кунед.",
        "phone_invalid": (
            "Рақами телефонро муайян карда натавонистем. Лутфан, дар шакли "
            f"{PHONE_EXAMPLE_NATIONAL} ё {PHONE_EXAMPLE_INTERNATIONAL} нависед."
        ),
        "greeting": "Салом! Чӣ хизмат карда метавонем? 😊",
        "small_talk_thanks": "Марҳамат! Боз интизори шумо ҳастем 😊",
        "small_talk_goodbye": "Хайр, рӯзи хуш! Агар чизе лозим шавад, нависед.",
        "small_talk_ack": "Хуб 👍 Агар чизе лозим шавад, нависед.",
        "small_talk_chat": (
            "Мо нонвойхонаи хурди хонагӣ ҳастем 😊 Бигӯед, чӣ лозим аст: торт, ширинӣ ё расонидан?"
        ),
        "products_intro": "Ана чизҳое, ки дорем:",
        "no_active_orders": "Ҳоло шумо фармоиши фаъол надоред.",
        "order_status_line": "Фармоиши №{order_id}: {status}",
        "payment_line": "пардохт: {payment}",
        "working_hours": "Вақти корӣ",
        "clarify": "Бубахшед, паёмро нафаҳмидем. Лутфан, аниқ нависед, ки чӣ лозим аст?",
        "voice_not_recognized": "Паёми овозиро фаҳмида натавонистем, лутфан, бо матн нависед.",
        "blocked": "Мутаассифона, дар айни замон мо наметавонем фармоиши шуморо қабул кунем.",
    },
}

MAX_QUESTIONS = 2  # 05 §5.7.4: at most two fields per question


# --------------------------------------------------------------------------- helpers


def _lang(language: object) -> str:
    return TG if str(language or "").strip().lower() == TG else RU


def _t(language: str, key: str, **values: Any) -> str:
    text = _TEXTS[language][key]
    return text.format(**values) if values else text


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _money(value: Any, language: str) -> str | None:
    number = _decimal(value)
    if number is None:
        return None
    return f"{format_amount(number)} {CURRENCY[language]}"


def _date(value: Any) -> str | None:
    if isinstance(value, date):
        return format_date_ru(value)
    if isinstance(value, str) and value.strip():
        try:
            return format_date_ru(date.fromisoformat(value.strip()))
        except ValueError:
            return None
    return None


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _unit(unit: Any, language: str) -> str:
    text = _text(unit) or DEFAULT_UNIT_RU
    if language == TG and text in ("шт", "шт."):
        return UNIT_TG
    return text


def _quoted(names: Sequence[Any]) -> str:
    return ", ".join(f"«{_text(name)}»" for name in names if _text(name))


def _names(names: Sequence[Any]) -> str:
    return ", ".join(_text(name) for name in names if _text(name))


def _order_id(facts: Mapping[str, Any]) -> str:
    return _text(facts.get("order_id")) or "—"


def field_question(field: str, language: object) -> str | None:
    """The question for one missing field (03 §1.3), or ``None`` for an unknown name."""
    return FIELD_QUESTIONS[_lang(language)].get(field)


def _questions(missing_fields: Sequence[str], language: str) -> list[str]:
    questions = [q for q in (field_question(field, language) for field in missing_fields) if q]
    return questions[:MAX_QUESTIONS]


def _join_questions(questions: Sequence[str], language: str) -> str:
    """SPEC §11: one question as is, several as a numbered list under "Уточните, пожалуйста:"."""
    if not questions:
        return ""
    if len(questions) == 1:
        return questions[0]
    numbered = "\n".join(f"{index}. {question}" for index, question in enumerate(questions, start=1))
    return f"{_t(language, 'clarify_intro')}\n{numbered}"


def _paragraphs(*parts: str) -> str:
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


# --------------------------------------------------------------------------- notes shared by kinds


def _item_notes(facts: Mapping[str, Any], language: str) -> list[str]:
    """Pending item questions, unknown products, timing and phone problems (asked before fields)."""
    notes: list[str] = []
    unknown = facts.get("unknown_products") or []
    if unknown:
        notes.append(_t(language, "unknown_products", names=_quoted(unknown)))
        available = facts.get("available_products") or []
        if available:
            notes.append(_t(language, "available_products", names=_names([_product_name(p) for p in available])))
    for pending in facts.get("pending_items") or []:
        kind = pending.get("kind")
        options = _names(pending.get("options") or [])
        if kind == "generic" and options:
            notes.append(_t(language, "pending_generic", options=options))
        elif kind == "ambiguous" and options:
            notes.append(_t(language, "pending_ambiguous", options=options))
    quantity_names = [
        pending.get("name") for pending in facts.get("pending_items") or [] if pending.get("kind") == "quantity"
    ]
    if quantity_names:
        notes.append(_t(language, "pending_quantity", names=_names(quantity_names)))
    timing = facts.get("timing_problem")
    if timing == "delivery_date_past":
        notes.append(_t(language, "date_past", date=_date(facts.get("problem_date")) or "…"))
    elif timing == "delivery_too_soon":
        earliest = _earliest(facts, language)
        notes.append(
            _t(
                language,
                "too_soon",
                hours=_text(facts.get("min_lead_time_hours")) or "24",
                earliest=_t(language, "earliest", when=earliest) if earliest else "",
            )
        )
    elif timing == "delivery_too_far":
        notes.append(_t(language, "too_far", days=_text(facts.get("max_days_ahead")) or "60"))
    if facts.get("phone_invalid"):
        notes.append(_t(language, "phone_invalid"))
    return notes


def _earliest(facts: Mapping[str, Any], language: str) -> str:
    """"сегодня после 14:00" / "завтра после 14:00" / "18.09.2026 после 14:00" from the ``earliest_*`` facts."""
    time_text = _text(facts.get("earliest_time"))
    if not time_text:
        return ""
    relative = _text(facts.get("earliest_relative"))
    if relative == "today":
        return _t(language, "earliest_today", time=time_text)
    if relative == "tomorrow":
        return _t(language, "earliest_tomorrow", time=time_text)
    date_text = _date(facts.get("earliest_date"))
    return _t(language, "earliest_date", date=date_text, time=time_text) if date_text else ""


def _reminder(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    """Open draft: the next questions, or the confirmation reminder when the summary was shown."""
    if facts.get("confirmation_pending_order_id"):
        return _t(language, "confirm_reminder", order_id=_text(facts["confirmation_pending_order_id"]))
    return _join_questions(_questions(missing_fields, language), language)


def _product_name(product: Any) -> str:
    return _text(product.get("name")) if isinstance(product, Mapping) else _text(product)


# --------------------------------------------------------------------------- order summary (SPEC §12)


def _summary_lines(facts: Mapping[str, Any], language: str) -> list[str]:
    lines: list[str] = []
    for item in facts.get("items") or []:
        line = f"{_text(item.get('name'))} — {_text(item.get('quantity'))} {_unit(item.get('unit'), language)}"
        comment = _text(item.get("comment"))
        if comment:
            line += f" ({comment})"
        lines.append(line)
    return lines


def _summary(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    details: list[str] = []
    formatted_date = _date(facts.get("delivery_date"))
    if formatted_date:
        details.append(f"{_t(language, 'date')}: {formatted_date}")
    if _text(facts.get("delivery_time")):
        details.append(f"{_t(language, 'time')}: {_text(facts.get('delivery_time'))}")
    if facts.get("delivery_type") == "DELIVERY":
        details.append(_t(language, "delivery_yes"))
        if _text(facts.get("address")):
            details.append(f"{_t(language, 'address')}: {_text(facts.get('address'))}")
    elif facts.get("delivery_type") == "PICKUP":
        pickup = _text(facts.get("pickup_address"))
        details.append(f"{_t(language, 'pickup')}: {pickup}" if pickup else _t(language, "pickup_yes"))
    recipient_parts = (_text(facts.get("recipient_name")), _text(facts.get("recipient_phone")))
    recipient = ", ".join(part for part in recipient_parts if part)
    if recipient:
        details.append(f"{_t(language, 'recipient')}: {recipient}")
    method = PAYMENT_METHOD_LABELS[language].get(_text(facts.get("payment_method")))
    if method:
        details.append(f"{_t(language, 'payment')}: {method}")
    if _text(facts.get("comment")):
        details.append(f"{_t(language, 'comment')}: {_text(facts.get('comment'))}")

    total = _money(facts.get("total"), language)
    return _paragraphs(
        _t(language, "summary_title"),
        "\n".join(_summary_lines(facts, language)),
        "\n".join(details),
        f"{_t(language, 'total')}: {total}." if total else "",
        _t(language, "summary_question"),
    )


def _confirmed(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    lines = [_t(language, "confirmed", order_id=_order_id(facts))]
    formatted_date = _date(facts.get("delivery_date"))
    time_text = _text(facts.get("delivery_time"))
    if formatted_date and time_text:
        key = "confirmed_delivery" if facts.get("delivery_type") == "DELIVERY" else "confirmed_pickup"
        lines.append(_t(language, key, date=formatted_date, time=time_text))
    total = _money(facts.get("total"), language)
    if total:
        lines.append(f"{_t(language, 'total')}: {total}.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- kinds


def _price_lines(facts: Mapping[str, Any], language: str) -> list[str]:
    """Prices asked for while ordering: "Торт «Медовик» — 220 сомони / шт." + the draft total when known."""
    lines = []
    for product in facts.get("prices") or []:
        if not isinstance(product, Mapping):
            continue
        price = _money(product.get("price"), language)
        if price:
            lines.append(f"{_text(product.get('name'))} — {price} / {_unit(product.get('unit'), language)}")
    total = _money(facts.get("prices_total"), language)
    if lines and total:
        lines.append(f"{_t(language, 'total')}: {total}.")
    return lines


def _ask_missing(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    notes = _item_notes(facts, language)
    # Item problems are asked first (they precede every other field, 03 §1.3).
    fields = [field for field in missing_fields if not (notes and field == "items")]
    questions = _join_questions(_questions(fields, language), language)
    prices = "\n".join(_price_lines(facts, language))
    body = "\n".join(part for part in (*notes, questions) if part)
    return _paragraphs(prices, body) or _t(language, "clarify")


def _address_clarify(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    """03 §7 by geocode status: house variants to pick from, a street/microdistrict found but not the
    house, a provider failure, or nothing found — always with the map link when there is one.
    ``then_summary`` (nothing else is missing) tells the customer they may just go on."""
    link = _text(facts.get("link"))
    status = _text(facts.get("status")).upper()
    candidates = [candidate for candidate in facts.get("candidates") or [] if _text(candidate)]
    approximate = _text(facts.get("approximate"))
    if candidates:
        numbered = "\n".join(f"{index}. {_text(candidate)}" for index, candidate in enumerate(candidates, start=1))
        choose = _t(language, "address_choose", link=link) if link else _t(language, "address_choose_no_link")
        body = f"{_t(language, 'address_candidates')}\n{numbered}\n{choose}"
    elif status == "FAILED":
        body = _t(language, "address_failed") + (_t(language, "address_failed_link", link=link) if link else "")
    elif approximate:
        key = "address_approximate" if link else "address_approximate_no_link"
        body = _t(language, key, place=approximate, link=link)
    else:
        text = _t(language, "address_not_found")
        body = f"{text}{_t(language, 'address_link', link=link)}" if link else f"{text}."
    if facts.get("then_summary"):
        body = _paragraphs(body, _t(language, "address_continue"))
    return _paragraphs(body, _join_questions(_questions(missing_fields, language), language))


def _small_talk(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    """Thanks / goodbye / "ок" / chat — plus the reminder of an open order, when there is one."""
    kind = _text(facts.get("small_talk")) or "chat"
    key = {"thanks": "small_talk_thanks", "goodbye": "small_talk_goodbye", "ack": "small_talk_ack"}.get(
        kind, "small_talk_chat"
    )
    reminder = _reminder(facts, missing_fields, language)
    if kind == "ack" and reminder:
        return reminder
    return _paragraphs(_t(language, key), reminder)


def _handoff(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    code = facts.get("reason_code")
    lead = ""
    if code == "complaint":
        lead = _t(language, "handoff_complaint")
    elif code == "not_understood":
        # ai_unavailable has no lead: SPEC §40's "уточнить у менеджера" is about missing information,
        # while an AI outage says nothing about the question — the customer only hears the handoff.
        lead = _t(language, "need_manager")
    elif code == "order_locked":
        lead = _t(language, "handoff_order_locked", order_id=_order_id(facts))
    return " ".join(part for part in (lead, _t(language, "handoff")) if part)


def _with_reminder(body: str, facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    return _paragraphs(body, _reminder(facts, missing_fields, language))


def _greeting(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    return _with_reminder(_t(language, "greeting"), facts, missing_fields, language)


def _faq_answer(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    answers = [_text_block(entry.get("answer")) for entry in facts.get("faq") or [] if isinstance(entry, Mapping)]
    body = _paragraphs(*answers) or _t(language, "need_manager")
    return _with_reminder(body, facts, missing_fields, language)


def _text_block(value: Any) -> str:
    """Keeps the admin's line breaks (FAQ/settings texts) but trims every line."""
    if value is None:
        return ""
    return "\n".join(line.strip() for line in str(value).strip().splitlines())


def _product_line(product: Mapping[str, Any], language: str) -> str:
    name = _text(product.get("name"))
    price = _money(product.get("price"), language)
    unit = _unit(product.get("unit"), language)
    line = f"{name} — {price} / {unit}" if price else name
    description = _text(product.get("description"))
    return f"{line}. {description}" if description else line


def _product_info(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    products = [product for product in facts.get("products") or [] if isinstance(product, Mapping)]
    if not products:
        return _with_reminder(_t(language, "need_manager"), facts, missing_fields, language)
    lines = "\n".join(_product_line(product, language) for product in products)
    body = lines if facts.get("asked_specific") else f"{_t(language, 'products_intro')}\n{lines}"
    return _with_reminder(body, facts, missing_fields, language)


def _unknown_product(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    notes = _item_notes({key: facts.get(key) for key in ("unknown_products", "available_products")}, language)
    return _with_reminder("\n".join(notes) or _t(language, "need_manager"), facts, missing_fields, language)


def _order_status(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    orders = [order for order in facts.get("orders") or [] if isinstance(order, Mapping)]
    if not orders:
        return _with_reminder(_t(language, "no_active_orders"), facts, missing_fields, language)
    lines: list[str] = []
    for order in orders:
        status = STATUS_LABELS[language].get(_text(order.get("status")), _text(order.get("status")))
        parts = [_t(language, "order_status_line", order_id=_text(order.get("order_id")), status=status)]
        when_parts = (_date(order.get("delivery_date")), _text(order.get("delivery_time")))
        when = " ".join(part for part in when_parts if part)
        if when:
            parts.append(when)
        payment = PAYMENT_STATUS_LABELS[language].get(_text(order.get("payment_status")))
        if payment:
            parts.append(_t(language, "payment_line", payment=payment))
        total = _money(order.get("total"), language)
        if total:
            parts.append(total)
        lines.append(", ".join(parts))
    return _with_reminder("\n".join(lines), facts, missing_fields, language)


def _faq_block(facts: Mapping[str, Any]) -> list[str]:
    return [_text_block(entry.get("answer")) for entry in facts.get("faq") or [] if isinstance(entry, Mapping)]


def _delivery_info(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    lines = [_text_block(facts.get("delivery_info"))]
    if _text(facts.get("pickup_address")):
        lines.append(f"{_t(language, 'pickup')}: {_text(facts.get('pickup_address'))}")
    if _text(facts.get("working_hours")):
        lines.append(f"{_t(language, 'working_hours')}: {_text(facts.get('working_hours'))}")
    body = _paragraphs(*lines, *_faq_block(facts)) or _t(language, "need_manager")
    return _with_reminder(body, facts, missing_fields, language)


def _payment_info(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    body = _paragraphs(_text_block(facts.get("payment_methods")), *_faq_block(facts)) or _t(language, "need_manager")
    return _with_reminder(body, facts, missing_fields, language)


def _simple(key: str) -> Callable[[Mapping[str, Any], Sequence[str], str], str]:
    def render_simple(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
        return _t(language, key, order_id=_order_id(facts))

    return render_simple


_RENDERERS: dict[str, Callable[[Mapping[str, Any], Sequence[str], str], str]] = {
    "GREETING": _greeting,
    "ASK_MISSING": _ask_missing,
    "ORDER_SUMMARY": _summary,
    "ORDER_CONFIRMED": _confirmed,
    "CONFIRMATION_REPEAT": _simple("confirmation_repeat"),
    "ASK_WHAT_TO_CHANGE": _simple("what_to_change"),
    "ORDER_CANCELLED": _simple("cancelled"),
    "CANCEL_CONFIRM": _simple("cancel_confirm"),
    "CANCEL_KEPT": _simple("cancel_kept"),
    "SMALL_TALK": _small_talk,
    "FAQ_ANSWER": _faq_answer,
    "PRODUCT_INFO": _product_info,
    "ORDER_STATUS_INFO": _order_status,
    "DELIVERY_INFO": _delivery_info,
    "PAYMENT_INFO": _payment_info,
    "ADDRESS_CLARIFY": _address_clarify,
    "HANDOFF": _handoff,
    "NEED_MANAGER": _simple("need_manager"),
    "UNKNOWN_PRODUCT": _unknown_product,
    "CLARIFY": _simple("clarify"),
    "BLOCKED": _simple("blocked"),
}


def render(
    kind: object,
    language: object,
    facts: Mapping[str, Any] | None = None,
    missing_fields: Sequence[str] = (),
) -> str:
    """Deterministic text of a ``ReplyPlan`` (05 §6). Unknown kind → ``ValueError`` (a programming error)."""
    key = str(getattr(kind, "value", kind))
    renderer = _RENDERERS.get(key)
    if renderer is None:
        raise ValueError(f"Unknown reply kind: {key!r}")
    return renderer(facts or {}, list(missing_fields), _lang(language)).strip()


def voice_not_recognized(language: object) -> str:
    """06 §3: STT failed or is not configured."""
    return _t(_lang(language), "voice_not_recognized")
