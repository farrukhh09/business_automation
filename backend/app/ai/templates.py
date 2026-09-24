"""Deterministic RU/TG reply templates (docs/architecture/05-ai.md §6).

Every ``ReplyPlan`` kind has a template here. Some kinds are *always* rendered by a template (the
order summary, the confirmation, cancellation and handoff messages — anything with numbers or with a
legal meaning); the others are worded by the LLM and fall back to these templates when the model is
unavailable or the ``ResponseGuard`` rejects its text.

Facts are plain JSON values built by ``DialogService`` from the database: money is a decimal string
(``"1500.00"``), dates are ISO ``YYYY-MM-DD``, times ``HH:MM``. Nothing in this module queries the
database or invents a value — a missing fact simply leaves its line out.

Tajik texts are written in the northern (Khujand) colloquial register the customers actually use —
"раҳмат", "тайёр", "пагоҳ", "ҳозир", "адрес", "доставка", "курер", "чек", "перевод" — not in the
literary language ("ташаккур", "омода", "фардо", "суроға", "интиқол", "хаткашон"): the bakery is in
Khujand and a bookish reply reads as foreign there (customer's request, 18.09.2026). They were written
by the developer and still need a native speaker's review (docs/PROGRESS.md, open question 4).
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ai.catalog import flavour_groups, flavour_names
from app.ai.receipt import is_card_number
from app.ai.small_talk import Greeting
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

#: Weekday names by ``date.weekday()`` (0 = Monday). ``WEEKDAYS_ON`` is the form used after the
#: preposition ("в субботу", "рӯзи шанбе"), ``WEEKDAYS_NAME`` the plain one ("понедельник, 22.09.2026").
WEEKDAYS_ON: dict[str, tuple[str, ...]] = {
    RU: ("понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"),
    TG: ("душанбе", "сешанбе", "чоршанбе", "панҷшанбе", "ҷумъа", "шанбе", "якшанбе"),
}
WEEKDAYS_NAME: dict[str, tuple[str, ...]] = {
    RU: ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"),
    TG: WEEKDAYS_ON[TG],
}
#: Russian unit abbreviations of the catalog as said in a Tajik reply ("2 кор." → "2 қуттӣ").
UNITS_TG = {"шт": UNIT_TG, "шт.": UNIT_TG, "кор": "қуттӣ", "кор.": "қуттӣ"}

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
        "NEW": "қайд мешавад",
        "WAITING_CONFIRMATION": "интизори тасдиқ",
        "CONFIRMED": "тасдиқ шуд",
        "PREPARING": "тайёр мешавад",
        "READY": "тайёр",
        "HANDED_TO_COURIER": "ба курер дода шуд",
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
        "UNPAID": "пардохт нашуд",
        "PARTIALLY_PAID": "қисман пардохт шуд",
        "PAID": "пардохт шуд",
        "REFUNDED": "пул баргардонда шуд",
    },
}

PAYMENT_METHOD_LABELS: dict[str, dict[str, str]] = {
    RU: {"CASH": "наличными", "CARD": "картой", "TRANSFER": "переводом", "OTHER": "другим способом"},
    TG: {"CASH": "нақд", "CARD": "бо корт", "TRANSFER": "бо перевод", "OTHER": "бо роҳи дигар"},
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
        "items": "Чӣ фармоиш медиҳед?",
        "delivery_date": "Фармоиш барои кадом рӯз лозим?",
        "delivery_time": "Барои соати чанд?",
        "delivery_type": "Расонем ё худатон мегиред?",
        "customer_name": "Номатонро нависед.",
        "phone": "Рақами телефонатонро нависед.",
        "address": "Адреси расонданро нависед: микрорайон ё кӯча, хона, квартира ва ориентир.",
        "recipient_name": "Фармоишро кӣ мегирад? Номи гирандаро нависед.",
        "location": "Ҷои расонданро дар харита нишон диҳед.",
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
        # Nothing was placed yet, so there is no number to name and nothing to confirm.
        "draft_discarded": "Хорошо, ничего не записываем. Будут нужны синнамоны — напишите, соберём 🙂",
        # Follow-ups (03 §6a): the bakery's own wording from the Instagram archive — «Вам коробку
        # оставить или нет», «Вы заказываете или нет», «Мне для оформления заказа чек нужен или вы
        # передумали?». A number is never named: the customer has no order yet, only a conversation.
        "follow_up_draft": "Вам коробочку оставить? Напишите, пожалуйста, и мы всё оформим 🙂",
        "follow_up_confirmation": (
            "Подскажите, заказ оформляем? Напишите «Да» — и всё готово. Если передумали, просто скажите."
        ),
        "follow_up_receipt": "Мне для оформления заказа чек нужен, или вы передумали?",
        "handoff": "Передаю диалог менеджеру, он скоро ответит.",
        "handoff_complaint": "Извините за неудобства.",
        "handoff_order_locked": "Заказ №{order_id} уже в работе — изменения согласует менеджер.",
        "need_manager": "Мне нужно уточнить эту информацию у менеджера.",
        "need_manager_payment": "Про оплату уточню у менеджера — он напишет здесь.",
        "need_manager_delivery": "Про доставку уточню у менеджера — он напишет здесь.",
        # The second "уточню у менеджера" in a row: said in other words instead of handing the whole
        # dialog over — the customer may still ask everything else (dialog #3, 23.09.2026).
        "need_manager_again": (
            "Этот вопрос тоже передали менеджеру — он ответит здесь. А пока могу подсказать по вкусам, ценам "
            "и доставке или оформить заказ 🙂"
        ),
        "address_candidates": "Уточните, пожалуйста, адрес. Возможно, это один из вариантов:",
        "address_choose": "Напишите номер варианта или отметьте точку на карте: {link}",
        "address_choose_no_link": "Напишите номер подходящего варианта или уточните адрес.",
        "address_not_found": "Адрес записали, но на карте он не нашёлся 🙁",
        "address_not_found_link": (
            "Отметьте, пожалуйста, точку на карте: {link} — так курьер точно вас найдёт. "
            "Или напишите адрес подробнее: микрорайон или улица, дом, ориентир."
        ),
        "address_not_found_no_link": "Напишите, пожалуйста, подробнее: микрорайон или улица, дом, ориентир.",
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
        "address_other_town": (
            "Доставка в {town} — по договорённости: возможность и стоимость подтвердит менеджер."
        ),
        "address_town_link": " Отметьте, пожалуйста, точку на карте: {link}",
        "clarify_intro": "Уточните, пожалуйста:",
        "recorded": "Записали: {items}.",
        "pending_generic": "Подскажите, пожалуйста, какие именно? Сейчас есть: {options}.",
        "pending_mix": "Микс соберём из любых вкусов: {options}. Сколько штук нужно?",
        "pending_mix_uneven": (
            "{quantity} шт. на {count} вкусов поровну не делятся — напишите, какие выбрать: {options}."
        ),
        "pending_mix_full": " Или сделаем {count} шт. — по одному каждого?",
        "pending_generic_more": "Подскажите, пожалуйста, какие ещё {quantity} выбрать? Сейчас есть: {options}.",
        "pending_ambiguous": "Уточните, пожалуйста, какой именно товар вы имели в виду: {options}?",
        "pending_quantity": "Сколько штук нужно: {names}?",
        "pending_quantity_boxes": "Сколько коробочек нужно: {names}?",
        "unknown_products": "К сожалению, {names} у нас нет.",
        "available_products": "Сейчас можно заказать: {names}.",
        # The catalog by flavour: both prices on one line, the sizes explained once underneath
        # (the owner, 23.09.2026 — twelve rows with a description each are a wall of text).
        "size_price": ", {size} — {price}",
        "sizes_note": "Первая цена — стандартный размер, вторая — {sizes}.",
        "too_soon": (
            "Заказы принимаем не позднее чем за {hours} ч.{earliest} Выберите, пожалуйста, другую дату или время."
        ),
        "too_soon_same_day": "На {day} можем не раньше {time}.",
        "earliest": " Самое раннее — {when}.",
        "earliest_today": "сегодня после {time}",
        "earliest_tomorrow": "завтра после {time}",
        "earliest_date": "{date} после {time}",
        "day_today": "сегодня",
        "day_tomorrow": "завтра",
        "date_past": "{date} уже прошло 🙂",
        "too_far": "Заказы принимаем не больше чем на {days} дн. вперёд. Выберите, пожалуйста, другую дату.",
        "out_of_hours": "Заказы выдаём с {start} до {end}. Выберите, пожалуйста, другое время.",
        "out_of_hours_from": "Заказы выдаём не раньше {start}. Выберите, пожалуйста, другое время.",
        "out_of_hours_until": "Заказы выдаём не позже {end}. Выберите, пожалуйста, другое время.",
        "closed_day": "В {weekday} мы не работаем.",
        "closed_day_next": " Ближайший рабочий день — {weekday}, {date}.",
        "quantity_min": "Минимальный заказ — {min} шт.",
        # The step is not a box size once the bakery packs boxes of 4 *and* 6 (03 §1.3), so the rule
        # is stated by the totals that fit it. What a box is, the FAQ says in the owner's own words.
        "quantity_step": "Собираем заказ коробочками, поэтому по количеству подходят {examples} и так далее.",
        "quantity_choice": " Сейчас {total} — сделаем {lower} или {upper}?",
        "quantity_choice_up": " Сделаем {upper}?",
        "packing_note": (
            "Заказ собираем коробочками от {min} шт. — подходят {examples} и так далее; "
            "вкусы любые, цена складывается из выбранных."
        ),
        "packing_example": " Например, {quantity} шт. «{name}» — {total}.",
        "phone_invalid": (
            "Номер телефона не получилось распознать. Напишите, пожалуйста, в формате "
            f"{PHONE_EXAMPLE_NATIONAL} или {PHONE_EXAMPLE_INTERNATIONAL}."
        ),
        "greeting_salam": "Ва алейкум ассалом!",
        "greeting_morning": "Доброе утро!",
        "greeting_day": "Добрый день!",
        "greeting_evening": "Добрый вечер!",
        "greeting_hello": "Здравствуйте!",
        "greeting_question": "Что желаете заказать? 😊",
        "small_talk_thanks": "Пожалуйста! Будем рады видеть вас снова 😊",
        "small_talk_goodbye": "Всего доброго! Пишите, если что-то понадобится.",
        "small_talk_ack": "Хорошо 👍 Если что-то понадобится — пишите.",
        "small_talk_ping": "Да, мы здесь 😊",
        "small_talk_ping_question": "Подскажите, что вас интересует?",
        "small_talk_confused": "Извините, если написали непонятно 🙂",
        "small_talk_how": "Спасибо, всё хорошо 🙂",
        "small_talk_who": (
            "Я помощник пекарни «Синнамоны» 🙂 Подскажу по вкусам, ценам и доставке и оформлю заказ, "
            "а если понадобится — подключится менеджер."
        ),
        "small_talk_chat": (
            "Мы «Синнамоны» — премиальные синнамон-роллы в Худжанде 😊 Подскажите, чем можем помочь: "
            "заказ, доставка или самовывоз?"
        ),
        # The same small talk twice in a row: said in other words, never handed to the manager.
        "small_talk_again": "Мы на связи 🙂 Напишите, чем помочь: подскажем по вкусам и ценам или оформим заказ.",
        "small_talk_thanks_again": "Всегда рады 😊",
        "greeting_question_again": "Слушаем вас 😊 Подскажите, что вас интересует?",
        "unknown_products_again": "Да, к сожалению, {names} мы не делаем. Могу подсказать цены на то, что есть 🙂",
        "products_intro": "Вот что у нас есть:",
        # The caption under the price list photo (03 §1.4): the picture is the list, so the text
        # says only what a picture cannot — that the prices are per roll.
        "price_list_photo": "Вот наш прайс-лист 📋 Цены за штуку.",
        "no_active_orders": "У вас сейчас нет активных заказов.",
        "order_status_line": "Заказ №{order_id}: {status}",
        "payment_line": "оплата: {payment}",
        "working_hours": "Часы работы",
        "clarify": "Извините, не получилось понять сообщение. Уточните, пожалуйста, что вас интересует?",
        "voice_not_recognized": "Не получилось разобрать голосовое, напишите, пожалуйста, текстом.",
        "blocked": "К сожалению, в данное время мы не можем принять Ваш заказ.",
        # prepayment and receipts (03 §3)
        "prepayment_share": " {percent}%",
        "prepayment_info": (
            "Предоплата{share}: {account} {wallet}{banks}. После перевода пришлите, пожалуйста, чек — "
            "скриншот перевода."
        ),
        "confirmed_prepayment": (
            "Предоплата{share} — {amount}: переведите, пожалуйста, на {account_to} {wallet}{banks} и пришлите сюда "
            "чек — скриншот перевода. Как только менеджер увидит оплату, заказ пойдёт в работу."
        ),
        "ask_receipt": (
            "Спасибо! Пришлите, пожалуйста, чек — скриншот перевода на {account_to} {wallet}, — и менеджер "
            "подтвердит оплату по заказу №{order_id}."
        ),
        "receipt_ok": (
            "Чек получили, спасибо! Перевод {amount}. Менеджер сверит поступление и подтвердит оплату — "
            "заказ №{order_id} пойдёт в работу."
        ),
        "receipt_auto_paid": "Спасибо, оплату {amount} получили ✅ Заказ №{order_id} в работе.",
        "receipt_resent": (
            "Этот чек мы уже получили, спасибо! Менеджер сверит поступление и подтвердит оплату по заказу №{order_id}."
        ),
        "receipt_duplicate": (
            "Этот чек уже присылали к другому заказу. Если это новый перевод, пришлите, пожалуйста, чек именно "
            "по нему — менеджер проверит."
        ),
        "receipt_short": (
            "Чек получили: {amount}, а предоплата по заказу №{order_id} — {expected}. Переведите, пожалуйста, "
            "ещё {shortfall} на {account_to} {wallet} и пришлите чек."
        ),
        "receipt_wallet_mismatch": (
            "На чеке получатель {recipient}, а перевод нужен на {account_to} {wallet}. Проверьте, пожалуйста, "
            "перевод; если деньги ушли не туда, напишите нам — менеджер поможет."
        ),
        "receipt_failed": (
            "Похоже, перевод не прошёл — на чеке нет отметки об успешной оплате. Попробуйте, пожалуйста, "
            "ещё раз и пришлите новый чек."
        ),
        "receipt_amount_unknown": (
            "Чек получили, но сумму на нём разобрать не удалось — менеджер проверит вручную и напишет вам."
        ),
        "receipt_currency": (
            "На чеке сумма не в сомони ({currency}) — проверьте, пожалуйста, перевод; менеджер уточнит."
        ),
    },
    # Northern (Khujand) colloquial register — see the module docstring.
    TG: {
        "summary_title": "Фармоишатонро тафтиш кунед:",
        "date": "Сана",
        "time": "Соат",
        "delivery_yes": "Доставка: ҳа",
        "pickup": "Худатон мегиред",
        "pickup_yes": "Худатон мегиред: ҳа",
        "address": "Адрес",
        "recipient": "Гиранда",
        "payment": "Пардохт",
        "comment": "Изоҳ",
        "total": "Ҳамагӣ",
        "summary_question": "Ҳама дуруст? Барои тасдиқ «Ҳа» нависед.",
        "confirmed": "Раҳмат! Фармоиши №{order_id} тасдиқ шуд ✅",
        "confirmed_delivery": "Доставка: {date}, соати {time}.",
        "confirmed_pickup": "Фармоишро {date}, соати {time} гирифта метавонед.",
        "confirmation_repeat": (
            "Барои тасдиқи фармоиш «Ҳа» нависед. Агар чизе иваз кардан лозим бошад, нависед, чиро."
        ),
        "confirm_reminder": "Барои тасдиқи фармоиши №{order_id} «Ҳа» нависед.",
        "what_to_change": "Хуб. Нависед, дар фармоиш чиро иваз кунем.",
        "cancel_confirm": "Фармоиши №{order_id}-ро бекор кунем? Барои бекор кардан «Ҳа» нависед.",
        "cancelled": "Фармоиши №{order_id} бекор шуд.",
        "cancel_kept": "Хуб, фармоиши №{order_id}-ро бекор намекунем.",
        "draft_discarded": "Хуб, чизе сабт накардем. Синнамон даркор шавад — нависед, тайёр мекунем 🙂",
        "follow_up_draft": "Қуттиро барои шумо монем? Нависед — фармоишро ба расмият медарорем 🙂",
        "follow_up_confirmation": (
            "Фармоишро қабул кунам ё не? «Ҳа» нависед — тайёр мекунем. Агар фикратон дигар шуда бошад, гӯед."
        ),
        "follow_up_receipt": "Барои ба расмият даровардани фармоиш чек лозим — ё фикратон дигар шуд?",
        "handoff": "Паёматонро ба менеҷер медиҳам, ӯ зуд ҷавоб медиҳад.",
        "handoff_complaint": "Барои нороҳатӣ мебахшед.",
        "handoff_order_locked": "Фармоиши №{order_id} аллакай дар кор аст — тағйиротро менеҷер ҳал мекунад.",
        "need_manager": "Инро аз менеҷер мепурсам ва ба шумо менависам.",
        "need_manager_payment": "Дар бораи пардохт аз менеҷер мепурсам — ҳамин ҷо менависад.",
        "need_manager_delivery": "Дар бораи доставка аз менеҷер мепурсам — ҳамин ҷо менависад.",
        "need_manager_again": (
            "Инро ҳам ба менеҷер додем — ҳамин ҷо ҷавоб медиҳад. То он вақт дар бораи таъмҳо, нарх ва доставка "
            "гуфта метавонам ё фармоишро қабул мекунам 🙂"
        ),
        "address_candidates": "Адресро аниқ кунед. Шояд яке аз инҳо бошад:",
        "address_choose": "Рақами вариантро нависед ё ҷои худро дар харита нишон диҳед: {link}",
        "address_choose_no_link": "Рақами варианти мувофиқро нависед ё адресро аниқтар нависед.",
        "address_not_found": "Адресро навиштем, аммо дар харита наёфтем 🙁",
        "address_not_found_link": (
            "Ҷои худро дар харита нишон диҳед: {link} — курер шуморо зуд меёбад. "
            "Ё адресро муфассалтар нависед: микрорайон ё кӯча, хона, ориентир."
        ),
        "address_not_found_no_link": "Муфассалтар нависед: микрорайон ё кӯча, хона, ориентир.",
        "address_approximate": (
            "Дар харита «{place}»-ро ёфтем, аммо худи хонаро не. Ҷои худро дар харита нишон диҳед: {link} — "
            "курер шуморо зуд меёбад."
        ),
        "address_approximate_no_link": (
            "Дар харита «{place}»-ро ёфтем, аммо худи хонаро не. Хона ва ориентирро аниқ кунед."
        ),
        "address_failed": (
            "Ҳозир адресро дар харита тафтиш карда наметавонем — ҳеҷ гап не, курер бо телефон аниқ мекунад."
        ),
        "address_failed_link": " Агар қулай бошад, ҷои худро дар харита нишон диҳед: {link}",
        "address_continue": (
            "Агар харита кушодан нокулай бошад, «давом» нависед — фармоишро бо ҳамин адрес қабул мекунем, "
            "курер бо телефон аниқ мекунад."
        ),
        "address_other_town": "Доставка ба {town} — бо гуфтугӯ: мешавад ё не ва чанд пул, менеҷер мегӯяд.",
        "address_town_link": " Ҷои худро дар харита нишон диҳед: {link}",
        "clarify_intro": "Илтимос, аниқ кунед:",
        "recorded": "Навиштем: {items}.",
        "pending_generic": "Кадомашро мегиред? Ҳозир дорем: {options}.",
        "pending_mix": "Миксро аз ҳар мазза ҷамъ мекунем: {options}. Чандто лозим?",
        "pending_mix_uneven": (
            "{quantity} дона ба {count} таъм баробар тақсим намешавад — нависед, кадомашро гирем: {options}."
        ),
        "pending_mix_full": " Ё {count} дона кунем — аз ҳар таъм якто?",
        "pending_generic_more": "Боз кадомашро мегиред ({quantity})? Ҳозир дорем: {options}.",
        "pending_ambiguous": "Кадомашро дар назар доред: {options}?",
        "pending_quantity": "Чандто лозим: {names}?",
        "pending_quantity_boxes": "Чанд қуттӣ лозим: {names}?",
        "unknown_products": "Мебахшед, {names} дар мо нест.",
        "available_products": "Ҳозир инҳо ҳастанд: {names}.",
        "size_price": ", {size} — {price}",
        "sizes_note": "Нархи якум — андозаи стандартӣ, дуюмаш — {sizes}.",
        "too_soon": ("Фармоишро камаш {hours} соат пештар қабул мекунем.{earliest} Рӯз ё соати дигарро интихоб кунед."),
        "too_soon_same_day": "Барои {day} — на барвақттар аз соати {time}.",
        "earliest": " Аз ҳама барвақт — {when}.",
        "earliest_today": "имрӯз баъд аз соати {time}",
        "earliest_tomorrow": "пагоҳ баъд аз соати {time}",
        "day_today": "имрӯз",
        "day_tomorrow": "пагоҳ",
        "earliest_date": "{date} баъд аз соати {time}",
        "date_past": "{date} аллакай гузашт 🙂",
        "too_far": "Фармоишро то {days} рӯз пештар қабул мекунем. Рӯзи дигарро интихоб кунед.",
        "out_of_hours": "Фармоишро аз соати {start} то {end} медиҳем. Соати дигарро интихоб кунед.",
        "out_of_hours_from": "Фармоишро на барвақттар аз соати {start} медиҳем. Соати дигарро интихоб кунед.",
        "out_of_hours_until": "Фармоишро на дертар аз соати {end} медиҳем. Соати дигарро интихоб кунед.",
        "closed_day": "Рӯзи {weekday} кор намекунем.",
        "closed_day_next": " Рӯзи кории наздиктарин — {weekday}, {date}.",
        "quantity_min": "Фармоиши камтарин — {min} дона.",
        "quantity_step": "Фармоишро қуттигӣ ҷамъ мекунем, барои ҳамин {examples} ва ҳамин тавр мешавад.",
        "quantity_choice": " Ҳозир {total} шуд — {lower} ё {upper} кунем?",
        "quantity_choice_up": " {upper} кунем?",
        "packing_note": (
            "Фармоишро қуттигӣ, аз {min} дона ҷамъ мекунем — {examples} ва ҳамин тавр мешавад; "
            "таъмҳо ҳар хел, нарх аз ҳамонҳо ҷамъ мешавад."
        ),
        "packing_example": " Масалан, {quantity} дона «{name}» — {total}.",
        "phone_invalid": (
            f"Рақами телефонро нафаҳмидем. Дар шакли {PHONE_EXAMPLE_NATIONAL} ё {PHONE_EXAMPLE_INTERNATIONAL} нависед."
        ),
        "greeting_salam": "Ва алейкум ассалом!",
        "greeting_morning": "Субҳ ба хайр!",
        "greeting_day": "Рӯз ба хайр!",
        "greeting_evening": "Шом ба хайр!",
        "greeting_hello": "Салом!",
        "greeting_question": "Чӣ фармоиш медиҳед? 😊",
        "small_talk_thanks": "Саломат бошед! Боз биёед 😊",
        "small_talk_goodbye": "Хайр! Агар чизе лозим шавад, нависед.",
        "small_talk_ack": "Хуб 👍 Агар чизе лозим шавад, нависед.",
        "small_talk_ping": "Ҳа, мо ҳастем 😊",
        "small_talk_ping_question": "Чӣ лозим, нависед?",
        "small_talk_confused": "Мебахшед, агар нофаҳмо навишта бошем 🙂",
        "small_talk_how": "Раҳмат, нағз 🙂",
        "small_talk_who": (
            "Ман ёрдамчии «Синнамоны» ҳастам 🙂 Дар бораи таъмҳо, нарх ва доставка мегӯям ва фармоиш қабул "
            "мекунам, лозим шавад — менеҷер ҳам пайваст мешавад."
        ),
        "small_talk_again": (
            "Мо дар алоқаем 🙂 Нависед, чӣ лозим: дар бораи таъмҳо ва нарх мегӯем ё фармоиш қабул мекунем."
        ),
        "small_talk_thanks_again": "Ҳамеша хурсандем 😊",
        "greeting_question_again": "Гӯш мекунем 😊 Чӣ лозим, нависед?",
        "unknown_products_again": "Ҳа, мебахшед, {names} тайёр намекунем. Нархи чизҳои доштаамонро гуфта метавонам 🙂",
        "small_talk_chat": (
            "Мо «Синнамоны» ҳастем — синнамон-роллҳои премиум дар Хуҷанд 😊 Чӣ лозим: фармоиш, доставка ё "
            "худатон мегиред?"
        ),
        "products_intro": "Ана чӣ дорем:",
        "price_list_photo": "Ана прайс-листи мо 📋 Нархҳо барои як дона.",
        "no_active_orders": "Ҳозир шумо фармоиши фаъол надоред.",
        "order_status_line": "Фармоиши №{order_id}: {status}",
        "payment_line": "пардохт: {payment}",
        "working_hours": "Вақти корӣ",
        "clarify": "Мебахшед, нафаҳмидем. Аниқтар нависед, чӣ лозим?",
        "voice_not_recognized": "Паёми овозиро фаҳмида натавонистем, бо матн нависед.",
        "blocked": "Мебахшед, ҳозир фармоиши шуморо қабул карда наметавонем.",
        # prepayment and receipts (03 §3)
        "prepayment_share": " {percent}%",
        "prepayment_info": (
            "Пешпардохт{share}: {account} {wallet}{banks}. Пас аз перевод чекро — скриншоти переводро — фиристед."
        ),
        "confirmed_prepayment": (
            "Пешпардохт{share} — {amount}: ба {account_to} {wallet}{banks} гузаронед ва чекро — скриншоти "
            "переводро — ҳамин ҷо фиристед. Ҳамин ки менеҷер пардохтро бинад, фармоишатонро тайёр кардан сар мекунем."
        ),
        "ask_receipt": (
            "Раҳмат! Чекро — скриншоти перевод ба {account_to} {wallet} — фиристед, менеҷер пардохти фармоиши "
            "№{order_id}-ро тасдиқ мекунад."
        ),
        "receipt_ok": (
            "Чекро гирифтем, раҳмат! Перевод: {amount}. Менеҷер омадани пулро тафтиш мекунад ва пардохтро тасдиқ "
            "мекунад — баъд фармоиши №{order_id}-ро тайёр мекунем."
        ),
        "receipt_auto_paid": "Раҳмат, пардохти {amount} расид ✅ Фармоиши №{order_id} дар кор аст.",
        "receipt_resent": (
            "Ин чекро аллакай гирифтем, раҳмат! Менеҷер омадани пулро тафтиш мекунад ва пардохти фармоиши "
            "№{order_id}-ро тасдиқ мекунад."
        ),
        "receipt_duplicate": (
            "Ин чек аллакай барои фармоиши дигар фиристода шуда буд. Агар ин переводи нав бошад, чеки худи ҳаминро "
            "фиристед — менеҷер тафтиш мекунад."
        ),
        "receipt_short": (
            "Чекро гирифтем: {amount}, аммо пешпардохти фармоиши №{order_id} — {expected}. Боз {shortfall} ба "
            "{account_to} {wallet} гузаронед ва чекро фиристед."
        ),
        "receipt_wallet_mismatch": (
            "Дар чек гиранда {recipient} аст, аммо перевод бояд ба {account_to} {wallet} равад. Переводро тафтиш "
            "кунед; агар пул ба ҷои дигар рафта бошад, ба мо нависед — менеҷер ёрӣ медиҳад."
        ),
        "receipt_failed": (
            "Гӯё перевод нагузаштааст — дар чек аломати пардохти муваффақ нест. Боз як бор кӯшиш кунед ва чеки "
            "навро фиристед."
        ),
        "receipt_amount_unknown": (
            "Чекро гирифтем, аммо суммаро хонда натавонистем — менеҷер худаш тафтиш мекунад ва ба шумо менависад."
        ),
        "receipt_currency": (
            "Дар чек сумма бо сомонӣ нест ({currency}) — переводро тафтиш кунед; менеҷер аниқ мекунад."
        ),
    },
}

#: The bakery's own account in the prepayment texts: (plain form, form after "на" / "ба") — 03 §3.
_ACCOUNT_WORDS: dict[str, dict[str, tuple[str, str]]] = {
    RU: {"wallet": ("кошелёк", "кошелёк"), "card": ("карта", "карту")},
    TG: {"wallet": ("ҳамён", "ҳамёни"), "card": ("корт", "корти")},
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
    return UNITS_TG.get(text, text) if language == TG else text


def _is_box_unit(unit: Any) -> bool:
    """The catalog's "кор." / "коробка" (or its Tajik "қуттӣ")."""
    text = _text(unit).lower().rstrip(".")
    return text.startswith("кор") or text in ("қуттӣ", "куттӣ", "кутти")


def _quoted(names: Sequence[Any]) -> str:
    return ", ".join(f"«{_text(name)}»" for name in names if _text(name))


def _names(names: Sequence[Any]) -> str:
    return ", ".join(_text(name) for name in names if _text(name))


def _number_series(values: Any) -> str:
    """ "4, 6, 8, 10" — the allowed order totals, as the packing texts list them (03 §1.3)."""
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ""
    return ", ".join(str(int(value)) for value in values if isinstance(value, int) and not isinstance(value, bool))


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
    asks_which = any(
        pending.get("kind") == "generic" and pending.get("options") for pending in facts.get("pending_items") or []
    )
    if unknown:
        notes.append(_t(language, "unknown_products", names=_quoted(unknown)))
        available = facts.get("available_products") or []
        # "какие именно?" below lists the very same catalog — once per message is enough
        # (dialog #3, 23.09.2026: the customer got the twelve names twice in one reply).
        if available and not asks_which:
            names = flavour_names([_product_name(product) for product in available])
            notes.append(_t(language, "available_products", names=_names(names)))
    recorded = _recorded_items(facts)
    for pending in facts.get("pending_items") or []:
        kind = pending.get("kind")
        names = [_text(option) for option in pending.get("options") or []]
        # "какие именно?" is a question about the flavour, so the sizes of one flavour are one
        # option; "какой именно товар?" is exactly the question of which of the two it was.
        options = _names(flavour_names(names) if kind == "generic" else names)
        quantity = _text(pending.get("quantity"))
        if kind == "generic" and options and pending.get("mix") and not quantity:
            # A mix with no count: the flavours are settled ("все"), the number is what is missing.
            # Asking "какие именно?" here sends the customer round the same circle (dialog #3).
            notes.append(_t(language, "pending_mix", options=options))
        elif kind == "generic" and options and pending.get("mix"):
            # "4 синнамона" + "все разные": 4 on 6 flavours — which 4, or 6, one of each?
            count = len(flavour_names(names))
            note = _t(language, "pending_mix_uneven", quantity=quantity, count=count, options=options)
            if _text(facts.get("mix_full")):
                note += _t(language, "pending_mix_full", count=_text(facts.get("mix_full")))
            notes.append(note)
        elif kind == "generic" and options and recorded and quantity:
            # "5 синнамонов, 3 ягодных": the 3 are written down — the question is about the 2 more.
            notes.append(_t(language, "pending_generic_more", quantity=quantity, options=options))
        elif kind == "generic" and options:
            notes.append(_t(language, "pending_generic", options=options))
        elif kind == "ambiguous" and options:
            notes.append(_t(language, "pending_ambiguous", options=options))
    quantity_pending = [pending for pending in facts.get("pending_items") or [] if pending.get("kind") == "quantity"]
    if quantity_pending:
        # Products sold by the box are asked "сколько коробочек?", not "сколько штук?".
        boxes = all(_is_box_unit(pending.get("unit")) for pending in quantity_pending)
        key = "pending_quantity_boxes" if boxes else "pending_quantity"
        notes.append(_t(language, key, names=_names([pending.get("name") for pending in quantity_pending])))
    timing = facts.get("timing_problem")
    if timing == "delivery_date_past":
        notes.append(_t(language, "date_past", date=_date(facts.get("problem_date")) or "…"))
    elif timing == "delivery_too_soon" and facts.get("timing_keeps_date") and _text(facts.get("earliest_time")):
        # The day was accepted, only the hour was too early: "На завтра можем не раньше 17:00."
        notes.append(_t(language, "too_soon_same_day", day=_earliest_day(facts, language), time=facts["earliest_time"]))
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
    elif timing == "delivery_out_of_hours":
        start, end = _text(facts.get("order_hours_start")), _text(facts.get("order_hours_end"))
        if start and end:
            notes.append(_t(language, "out_of_hours", start=start, end=end))
        elif start:
            notes.append(_t(language, "out_of_hours_from", start=start))
        elif end:
            notes.append(_t(language, "out_of_hours_until", end=end))
    elif timing == "delivery_closed_day":
        notes.append(_closed_day(facts, language))
    if facts.get("quantity_problem"):
        notes.append(_quantity_note(facts, language))
    if facts.get("phone_invalid"):
        notes.append(_t(language, "phone_invalid"))
    return notes


def _weekday(index: Any, language: str, names: dict[str, tuple[str, ...]]) -> str:
    """Weekday name for a ``date.weekday()`` value coming from the facts; "" when it is not one."""
    try:
        position = int(index)
    except (TypeError, ValueError):
        return ""
    row = names.get(language) or names[RU]
    return row[position] if 0 <= position < len(row) else ""


def _closed_day(facts: Mapping[str, Any], language: str) -> str:
    """ "В субботу мы не работаем. Ближайший рабочий день — понедельник, 22.09.2026." """
    day = _weekday(facts.get("closed_weekday"), language, WEEKDAYS_ON)
    note = _t(language, "closed_day", weekday=day) if day else ""
    following = _weekday(facts.get("next_open_weekday"), language, WEEKDAYS_NAME)
    date_text = _date(facts.get("next_open_date"))
    if following and date_text:
        note += _t(language, "closed_day_next", weekday=following, date=date_text)
    return note.strip()


def _quantity_note(facts: Mapping[str, Any], language: str) -> str:
    """The packing rule: the minimum or the allowed totals, plus the two the customer can pick."""
    if facts.get("quantity_problem") == "quantity_below_min":
        note = _t(language, "quantity_min", min=_text(facts.get("quantity_min")) or "1")
    else:
        note = _t(language, "quantity_step", examples=_number_series(facts.get("quantity_examples")))
    lower, upper = _text(facts.get("quantity_lower")), _text(facts.get("quantity_upper"))
    total = _text(facts.get("quantity_total"))
    if lower and upper and total:
        note += _t(language, "quantity_choice", total=total, lower=lower, upper=upper)
    elif upper:
        note += _t(language, "quantity_choice_up", upper=upper)
    return note


def _earliest(facts: Mapping[str, Any], language: str) -> str:
    """ "сегодня после 14:00" / "завтра после 14:00" / "18.09.2026 после 14:00" from the ``earliest_*`` facts."""
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


def _earliest_day(facts: Mapping[str, Any], language: str) -> str:
    """ "сегодня" / "завтра" / "18.09.2026" — the day of the earliest slot."""
    relative = _text(facts.get("earliest_relative"))
    if relative in ("today", "tomorrow"):
        return _t(language, f"day_{relative}")
    return _date(facts.get("earliest_date")) or "…"


def _recorded_items(facts: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Items already in the draft (``order_so_far``), for "Записали: …" next to an item question."""
    order = facts.get("order_so_far")
    items = order.get("items") if isinstance(order, Mapping) else None
    return [item for item in items or [] if isinstance(item, Mapping) and _text(item.get("name"))]


def _reminder(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    """Open draft: the next questions, or the confirmation reminder when the summary was shown."""
    if facts.get("confirmation_pending_order_id"):
        return _t(language, "confirm_reminder", order_id=_text(facts["confirmation_pending_order_id"]))
    return _join_questions(_questions(missing_fields, language), language)


def _product_name(product: Any) -> str:
    return _text(product.get("name")) if isinstance(product, Mapping) else _text(product)


def _answers(facts: Mapping[str, Any], language: str) -> str:
    """What the customer asked while giving order data ("а доставка платная?"), answered before the
    order's own text (fact ``answers``): FAQ entries, delivery / pickup / hours, payment methods —
    or "уточню у менеджера" when the settings hold nothing about it."""
    answers = facts.get("answers")
    if not isinstance(answers, Mapping):
        return ""
    parts: list[str] = []
    # "Трайфл и круассаны есть? И можно у вас посидеть?" — a thing we do not make is said plainly
    # next to the other answer, never left out (audit 23.09.2026).
    missing = {key: answers.get(key) for key in ("unknown_products", "available_products")}
    parts.append("\n".join(_item_notes(missing, language)))
    topic = answers.get("need_manager")
    if topic:
        # "Про оплату уточню у менеджера" — which of the questions waits for the manager.
        key = f"need_manager_{topic}" if isinstance(topic, str) and f"need_manager_{topic}" in _TEXTS[language] else ""
        parts.append(_t(language, key or "need_manager"))
    parts.extend(_faq_block(answers))
    if _text(answers.get("delivery_info")):
        parts.append(_text_block(answers.get("delivery_info")))
    if _text(answers.get("pickup_address")):
        parts.append(f"{_t(language, 'pickup')}: {_text(answers.get('pickup_address'))}")
    if _text(answers.get("working_hours")):
        parts.append(f"{_t(language, 'working_hours')}: {_text(answers.get('working_hours'))}")
    if _text(answers.get("payment_methods")):
        parts.append(_text_block(answers.get("payment_methods")))
    parts.append(_prepayment_info(answers.get("prepayment"), language))
    return _paragraphs(*parts)


def _account_words(wallet: Any, language: str) -> dict[str, str]:
    """The word for the bakery's own account: a phone wallet or a card number (03 §3).

    ``account`` is the plain form ("кошелёк" / "карта", "ҳамён" / "корт"), ``account_to`` the one
    used after "на" / "ба" ("кошелёк" / "карту", "ҳамёни" / "корти").
    """
    plain, after_to = _ACCOUNT_WORDS[language]["card" if is_card_number(_text(wallet)) else "wallet"]
    return {"account": plain, "account_to": after_to}


def _prepayment_values(prepayment: Mapping[str, Any], language: str) -> dict[str, str]:
    """``{share, wallet, banks, amount, account…}``: " 50%" / "", " (Алиф, Эсхата)" / "", "кошелёк" / "карту"."""
    percent = _text(prepayment.get("percent"))
    banks = _text(prepayment.get("banks"))
    wallet = _text(prepayment.get("wallet"))
    return {
        "share": _t(language, "prepayment_share", percent=percent) if percent and percent != "100" else "",
        "wallet": wallet,
        "banks": f" ({banks})" if banks else "",
        "amount": _money(prepayment.get("amount"), language) or "",
        **_account_words(wallet, language),
    }


def _prepayment_info(prepayment: Any, language: str) -> str:
    """ "Предоплата: кошелёк +992 … (Душанбе Сити, Алиф, Эсхата). После перевода пришлите чек." (``prepayment``)."""
    if not isinstance(prepayment, Mapping) or not _text(prepayment.get("wallet")):
        return ""
    return _t(language, "prepayment_info", **_prepayment_values(prepayment, language))


def _greeting_line(facts: Mapping[str, Any], language: str) -> str:
    """ "Добрый день!" / "Ва алейкум ассалом!" — the customer's own greeting returned (fact ``greeting``)."""
    try:
        greeting = Greeting(_text(facts.get("greeting")))
    except ValueError:
        greeting = Greeting.HELLO
    return _t(language, f"greeting_{greeting.value}")


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
        _answers(facts, language),
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
    prepayment = facts.get("prepayment")
    request = ""
    if isinstance(prepayment, Mapping) and _text(prepayment.get("wallet")) and _text(prepayment.get("amount")):
        # 03 §3: the wallet and the sum right after "Да"; the receipt comes back into the same dialog.
        request = _t(language, "confirmed_prepayment", **_prepayment_values(prepayment, language))
    return _paragraphs("\n".join(lines), request)


# --------------------------------------------------------------------------- kinds


def _price_lines(facts: Mapping[str, Any], language: str) -> list[str]:
    """Prices asked for while ordering: "Фисташковые синнамоны — 100 сомони / кор." + the draft total when known."""
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
    recorded = ""
    if facts.get("pending_items") and _recorded_items(facts):
        # While items are still being picked, the customer sees what is already written down.
        lines = [
            f"{_text(item.get('name'))} — {_text(item.get('quantity'))} {_unit(item.get('unit'), language)}"
            for item in _recorded_items(facts)
        ]
        # "— 3 кор." already ends the sentence: "Записали: … — 3 кор.", not "кор.."
        recorded = _t(language, "recorded", items=", ".join(lines).removesuffix("."))
    body = "\n".join(part for part in (recorded, *notes, questions) if part)
    return _paragraphs(_answers(facts, language), prices, body) or _t(language, "clarify")


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
        # The address is kept as written (03 §1.3): only the map did not know it — never "no such address".
        hint = _t(language, "address_not_found_link", link=link) if link else _t(language, "address_not_found_no_link")
        body = f"{_t(language, 'address_not_found')} {hint}"
    if facts.get("then_summary"):
        body = _paragraphs(body, _t(language, "address_continue"))
    town = _text(facts.get("out_of_town"))
    if town:
        # Not looked up in our city (03 §7): the arrangement first, then the map pin.
        link_text = _t(language, "address_town_link", link=link) if link else ""
        body = _t(language, "address_other_town", town=town) + link_text
    return _paragraphs(_answers(facts, language), body, _join_questions(_questions(missing_fields, language), language))


def _small_talk(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    """Thanks / goodbye / "ок" / "это всё" / chat — plus the reminder of an open order, when there is one."""
    kind = _text(facts.get("small_talk")) or "chat"
    key = {
        "thanks": "small_talk_thanks",
        "goodbye": "small_talk_goodbye",
        "ack": "small_talk_ack",
        "done": "small_talk_ack",
        "ping": "small_talk_ping",
        "confused": "small_talk_confused",
        "how": "small_talk_how",
        "who": "small_talk_who",
    }.get(kind, "small_talk_chat")
    reminder = _reminder(facts, missing_fields, language)
    if facts.get("again"):
        again = "small_talk_thanks_again" if kind in ("thanks", "goodbye") else "small_talk_again"
        return _paragraphs(_t(language, again), reminder)
    if kind in ("ack", "done") and reminder:
        return reminder
    if kind in ("ping", "confused", "how"):
        # "Алло?" / "вы тут?": we are here; "не понял" / "что?": sorry — and the open question
        # again, or "what can we do?".
        return f"{_t(language, key)} {reminder or _t(language, 'small_talk_ping_question')}"
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


def _info(body: str, facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    """An answer, then the other questions of the same message (``answers``), then the open draft's
    question: "Сколько стоит коробка и есть ли доставка?" gets both answers (audit 23.09.2026)."""
    return _paragraphs(body, _answers(facts, language), _reminder(facts, missing_fields, language))


def _need_manager(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    """SPEC §40, plus whatever else the message asked that the data does answer. Asked again, it is
    said in other words — "already with the manager" — instead of repeating the same sentence."""
    key = "need_manager_again" if facts.get("again") else "need_manager"
    return _paragraphs(_answers(facts, language), _t(language, key))


def _greeting(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    """ "Добрый день! Что желаете заказать? 😊" — the customer's own greeting back (fact ``greeting``);
    with an open draft its questions or the confirmation reminder take the place of the question."""
    reminder = _reminder(facts, missing_fields, language)
    question = _t(language, "greeting_question_again" if facts.get("again") else "greeting_question")
    return f"{_greeting_line(facts, language)} {reminder or question}"


def _faq_answer(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    answers = [_text_block(entry.get("answer")) for entry in facts.get("faq") or [] if isinstance(entry, Mapping)]
    body = _paragraphs(*answers) or _t(language, "need_manager")
    return _info(body, facts, missing_fields, language)


def _text_block(value: Any) -> str:
    """Keeps the admin's line breaks (FAQ/settings texts) but trims every line."""
    if value is None:
        return ""
    return "\n".join(line.strip() for line in str(value).strip().splitlines())


def _product_line(product: Mapping[str, Any], language: str, *, description: bool = True) -> str:
    name = _text(product.get("name"))
    price = _money(product.get("price"), language)
    unit = _unit(product.get("unit"), language)
    line = f"{name} — {price} / {unit}" if price else name
    text = _text(product.get("description")) if description else ""
    # "/ кор." already ends with a period: "— 100 сомони / кор. Коробочка из 4…", not "кор.. Коробочка"
    return f"{line.removesuffix('.')}. {text}" if text else line


#: The size words of the catalog as a Tajik text says them («большой размер» → «калон»).
_SIZE_TG: dict[str, str] = {"большой": "калон", "большая": "калон", "большие": "калон"}


def _size_word(size: str, language: str) -> str:
    word = size.strip().lower()
    return _SIZE_TG.get(word, word) if language == TG else word


def _catalog_lines(products: Sequence[Mapping[str, Any]], language: str) -> tuple[list[str], list[str]]:
    """The whole catalog by flavour: "Классический синнамон — 10 сомони / шт, большой — 15 сомони".

    Descriptions are left out here — the list is read to pick a flavour and a size, and a dozen
    rows with a sentence each is the wall of text the owner asked us to stop sending (03 §1.4).
    """
    lines: list[str] = []
    sizes: list[str] = []
    for group in flavour_groups(products):
        line = _product_line(group.base, language, description=False)
        for size, variant in group.variants:
            price = _money(variant.get("price"), language)
            if price:
                label = _size_word(size, language)
                # "10 сомони / шт." already ends the line: "/ шт, большой — 15", not "шт., большой"
                line = line.removesuffix(".") + _t(language, "size_price", size=label, price=price)
                sizes.append(label)
        lines.append(line)
    return lines, list(dict.fromkeys(sizes))


def _product_info(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    products = [product for product in facts.get("products") or [] if isinstance(product, Mapping)]
    if not products:
        return _with_reminder(_t(language, "need_manager"), facts, missing_fields, language)
    # "Круассаны есть? А фисташковый сколько?": what we do not make is said first, in one line —
    # the list below is already the answer to "what do you have".
    missing = _item_notes({"unknown_products": facts.get("unknown_products")}, language)
    if facts.get("price_list_photo"):
        # The picture of the price list is sent just before this text (03 §1.4): the list itself is
        # on it, so repeating twelve prices underneath would be the wall of text all over again.
        body = _t(language, "price_list_photo")
    elif facts.get("asked_specific"):
        # A question about named products ("что такое фисташковый?") — with the description; about a
        # group of them ("а большие есть?") — one short line each, the list is what was asked for.
        described = len(products) <= 2
        body = "\n".join(_product_line(product, language, description=described) for product in products)
    else:
        lines, sizes = _catalog_lines(products, language)
        body = f"{_t(language, 'products_intro')}\n" + "\n".join(lines)
        if sizes:
            body += "\n" + _t(language, "sizes_note", sizes=_names(sizes))
    packing_min = _text(facts.get("packing_min"))
    if packing_min:
        # "Сколько стоит коробка?" is the commonest question of all: prices are per piece, so the
        # answer has to say how the order is put together and that its price adds up (03 §1.3).
        body += "\n" + _t(
            language, "packing_note", min=packing_min, examples=_number_series(facts.get("packing_examples"))
        )
        example = facts.get("packing_example")
        total = _money(example.get("total"), language) if isinstance(example, Mapping) else None
        if total:
            body += _t(
                language, "packing_example", quantity=_text(example.get("quantity")), name=_text(example.get("name")),
                total=total,
            )
    return _info("\n".join([*missing, body]), facts, missing_fields, language)


def _unknown_product(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    if facts.get("again") and facts.get("unknown_products"):
        # "я спрашиваю про пирожки" right after "пирожков нет": the same list again helps nobody.
        body = _t(language, "unknown_products_again", names=_quoted(facts["unknown_products"]))
        return _info(body, facts, missing_fields, language)
    notes = _item_notes({key: facts.get(key) for key in ("unknown_products", "available_products")}, language)
    return _info("\n".join(notes) or _t(language, "need_manager"), facts, missing_fields, language)


def _order_status(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    orders = [order for order in facts.get("orders") or [] if isinstance(order, Mapping)]
    if not orders:
        return _info(_t(language, "no_active_orders"), facts, missing_fields, language)
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
    return _info("\n".join(lines), facts, missing_fields, language)


def _faq_block(facts: Mapping[str, Any]) -> list[str]:
    return [_text_block(entry.get("answer")) for entry in facts.get("faq") or [] if isinstance(entry, Mapping)]


def _delivery_info(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    faq = _faq_block(facts)
    if any(faq):
        # The owner's answer to exactly this question. The settings texts say the same things again
        # (price, pickup, hours): printed together they were the wall of text (audit 23.09.2026).
        return _info(_paragraphs(*faq), facts, missing_fields, language)
    lines = [_text_block(facts.get("delivery_info"))]
    if _text(facts.get("pickup_address")):
        lines.append(f"{_t(language, 'pickup')}: {_text(facts.get('pickup_address'))}")
    if _text(facts.get("working_hours")):
        lines.append(f"{_t(language, 'working_hours')}: {_text(facts.get('working_hours'))}")
    body = _paragraphs(*lines) or _t(language, "need_manager")
    return _info(body, facts, missing_fields, language)


def _payment_info(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    body = _paragraphs(
        _text_block(facts.get("payment_methods")),
        _prepayment_info(facts.get("prepayment"), language),
        *_faq_block(facts),
    ) or _t(language, "need_manager")
    return _info(body, facts, missing_fields, language)


def _ask_receipt(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    wallet = _text(facts.get("wallet"))
    return _t(language, "ask_receipt", order_id=_order_id(facts), wallet=wallet, **_account_words(wallet, language))


def _receipt_result(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    """What the bot read on the receipt (03 §3): one message per outcome, the worst problem first.

    Only ``problems`` reach the customer; operator-only alerts (a doubtful date, traces of editing)
    leave the neutral "менеджер сверит" text — the facts never carry them.
    """
    problems = [str(problem) for problem in facts.get("problems") or []]
    if "status" in problems:
        key = "receipt_failed"
    elif "currency" in problems:
        key = "receipt_currency"
    elif "duplicate" in problems:
        key = "receipt_duplicate"
    elif "resent" in problems:
        key = "receipt_resent"
    elif "wallet_mismatch" in problems:
        key = "receipt_wallet_mismatch"
    elif "amount_unknown" in problems:
        key = "receipt_amount_unknown"
    elif "amount_short" in problems:
        key = "receipt_short"
    elif facts.get("auto_paid"):
        key = "receipt_auto_paid"
    else:
        key = "receipt_ok"
    wallet = _text(facts.get("wallet"))
    return _t(
        language,
        key,
        order_id=_order_id(facts),
        amount=_money(facts.get("amount"), language) or "",
        expected=_money(facts.get("expected"), language) or "",
        shortfall=_money(facts.get("shortfall"), language) or "",
        wallet=wallet,
        recipient=_text(facts.get("recipient")) or "—",
        currency=_text(facts.get("currency")) or "?",
        **_account_words(wallet, language),
    )


#: Follow-up stages (03 §6a) → the text that asks about them.
FOLLOW_UP_STAGE_KEYS: dict[str, str] = {
    "draft": "follow_up_draft",
    "confirmation": "follow_up_confirmation",
    "receipt": "follow_up_receipt",
}


def _follow_up(facts: Mapping[str, Any], missing_fields: Sequence[str], language: str) -> str:
    """ "Вам коробочку оставить?" plus the one question the answer still waits for (03 §6a)."""
    key = FOLLOW_UP_STAGE_KEYS.get(_text(facts.get("stage")), "follow_up_draft")
    question = _questions(missing_fields, language)[:1]
    return _paragraphs(_t(language, key), _join_questions(question, language))


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
    "DRAFT_DISCARDED": _simple("draft_discarded"),
    "SMALL_TALK": _small_talk,
    "FAQ_ANSWER": _faq_answer,
    "PRODUCT_INFO": _product_info,
    "ORDER_STATUS_INFO": _order_status,
    "DELIVERY_INFO": _delivery_info,
    "PAYMENT_INFO": _payment_info,
    "ADDRESS_CLARIFY": _address_clarify,
    "HANDOFF": _handoff,
    "NEED_MANAGER": _need_manager,
    "UNKNOWN_PRODUCT": _unknown_product,
    "CLARIFY": _simple("clarify"),
    "BLOCKED": _simple("blocked"),
    "ASK_RECEIPT": _ask_receipt,
    "RECEIPT_RESULT": _receipt_result,
    "FOLLOW_UP": _follow_up,
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
    values = facts or {}
    text = renderer(values, list(missing_fields), _lang(language)).strip()
    if key != "GREETING" and values.get("greeting"):
        # "Здравствуйте, хочу 2 коробки": the customer's greeting is answered before the reply itself.
        text = f"{_greeting_line(values, _lang(language))} {text}".strip()
    return text


def voice_not_recognized(language: object) -> str:
    """06 §3: STT failed or is not configured."""
    return _t(_lang(language), "voice_not_recognized")
