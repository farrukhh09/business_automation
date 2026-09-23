"""Reply templates and the responder (05-ai.md §6): template-only kinds, LLM wording, guard fallbacks."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.ai import templates
from app.ai.responder import (
    TEMPLATE_KINDS,
    ReplyKind,
    ReplyPlan,
    ReplySource,
    Responder,
    fact_amounts,
    strip_greeting,
    strip_thanks,
)
from tests.bot_fakes import ScriptedLLM

SUMMARY_FACTS = {
    "order_id": 12,
    "items": [
        {"name": "Красный бархат", "quantity": 1, "unit": "шт.", "comment": None},
        {"name": "Медовик", "quantity": 1, "unit": "шт.", "comment": "надпись «С днём рождения»"},
    ],
    "delivery_date": "2026-09-16",
    "delivery_time": "18:00",
    "delivery_type": "DELIVERY",
    "address": "Сино, 82 мкр, дом 5, кв. 10",
    "recipient_name": "Алия",
    "recipient_phone": "+992901234567",
    "payment_method": "CASH",
    "comment": None,
    "total": "1500.00",
}


# --------------------------------------------------------------------------- templates


def test_summary_template_follows_spec_12() -> None:
    assert templates.render(ReplyKind.ORDER_SUMMARY, "ru", SUMMARY_FACTS) == (
        "Проверьте, пожалуйста, заказ:\n\n"
        "Красный бархат — 1 шт.\n"
        "Медовик — 1 шт. (надпись «С днём рождения»)\n\n"
        "Дата: 16.09.2026\n"
        "Время: 18:00\n"
        "Доставка: да\n"
        "Адрес: Сино, 82 мкр, дом 5, кв. 10\n"
        "Получатель: Алия, +992901234567\n"
        "Оплата: наличными\n\n"
        "Итого: 1 500 сомони.\n\n"
        "Всё верно? Напишите «Да», чтобы подтвердить."
    )


def test_summary_template_in_tajik() -> None:
    text = templates.render("ORDER_SUMMARY", "tg", {**SUMMARY_FACTS, "delivery_type": "PICKUP", "address": None})
    assert text.startswith("Фармоишатонро тафтиш кунед:\n\nКрасный бархат — 1 дона\n")
    assert "Худатон мегиред: ҳа" in text
    assert "Ҳамагӣ: 1 500 сомонӣ." in text
    assert text.endswith("Ҳама дуруст? Барои тасдиқ «Ҳа» нависед.")


def test_ask_missing_numbers_two_questions_like_spec_11() -> None:
    text = templates.render("ASK_MISSING", "ru", {}, ["delivery_time", "delivery_type", "phone"])
    assert text == "Уточните, пожалуйста:\n1. К какому времени?\n2. Это будет доставка или самовывоз?"


def test_ask_missing_puts_item_questions_first() -> None:
    facts = {"pending_items": [{"kind": "generic", "product_text": "торт", "quantity": 2, "options": ["А", "Б"]}]}
    assert templates.render("ASK_MISSING", "ru", facts, ["items"]) == (
        "Подскажите, пожалуйста, какие именно? Сейчас есть: А, Б."
    )


def test_money_keeps_kopecks_only_when_present() -> None:
    text = templates.render("ORDER_CONFIRMED", "ru", {"order_id": 3, "total": "12500.50"})
    assert text == "Спасибо! Заказ №3 подтверждён ✅\nИтого: 12 500,50 сомони."


def test_address_clarify_without_candidates_and_link() -> None:
    text = templates.render("ADDRESS_CLARIFY", "ru", {"candidates": [], "link": None})
    assert text == (
        "Адрес записали, но на карте он не нашёлся 🙁 Напишите, пожалуйста, подробнее: микрорайон или улица, дом, "
        "ориентир."
    )


def test_address_clarify_by_geocode_status() -> None:
    link = "http://x/l/t"
    approximate = templates.render("ADDRESS_CLARIFY", "ru", {"approximate": "улица Айни, Душанбе", "link": link})
    assert approximate.startswith("Нашли на карте «улица Айни, Душанбе», но не сам дом.")
    assert link in approximate

    failed = templates.render("ADDRESS_CLARIFY", "ru", {"status": "FAILED", "link": link, "then_summary": True})
    assert failed.startswith("Сейчас не получается проверить адрес на карте — ничего страшного")
    assert "напишите «дальше»" in failed

    with_questions = templates.render("ADDRESS_CLARIFY", "ru", {"link": link}, ["phone"])
    assert with_questions.endswith("Напишите, пожалуйста, номер телефона для связи.")
    assert "«дальше»" not in with_questions  # the order is not complete yet, the questions continue

    tajik = templates.render("ADDRESS_CLARIFY", "tg", {"approximate": "кӯчаи Айнӣ", "link": link})
    assert tajik.startswith("Дар харита «кӯчаи Айнӣ»-ро ёфтем")


def test_small_talk_templates() -> None:
    assert (
        templates.render("SMALL_TALK", "ru", {"small_talk": "thanks"}) == "Пожалуйста! Будем рады видеть вас снова 😊"
    )
    assert templates.render("SMALL_TALK", "tg", {"small_talk": "goodbye"}).startswith("Хайр!")
    assert templates.render("SMALL_TALK", "tg", {"small_talk": "thanks"}) == "Саломат бошед! Боз биёед 😊"
    # "ок" while the draft is being filled → only the pending questions
    assert templates.render("SMALL_TALK", "ru", {"small_talk": "ack"}, ["phone"]) == (
        "Напишите, пожалуйста, номер телефона для связи."
    )
    assert templates.render("SMALL_TALK", "ru", {"small_talk": "thanks", "confirmation_pending_order_id": 5}) == (
        "Пожалуйста! Будем рады видеть вас снова 😊\n\nЧтобы подтвердить заказ №5, напишите «Да»."
    )


def test_greeting_template_answers_in_kind() -> None:
    assert templates.render("GREETING", "ru", {"greeting": "day"}) == "Добрый день! Что желаете заказать? 😊"
    assert templates.render("GREETING", "ru", {"greeting": "evening"}) == "Добрый вечер! Что желаете заказать? 😊"
    assert templates.render("GREETING", "tg", {"greeting": "salam"}) == "Ва алейкум ассалом! Чӣ фармоиш медиҳед? 😊"
    assert templates.render("GREETING", "ru", {}) == "Здравствуйте! Что желаете заказать? 😊"
    assert templates.render("GREETING", "ru", {"greeting": True}) == "Здравствуйте! Что желаете заказать? 😊"
    # an open draft: its questions or the confirmation reminder replace "Что желаете заказать?"
    assert templates.render("GREETING", "ru", {"greeting": "morning"}, ["phone"]) == (
        "Доброе утро! Напишите, пожалуйста, номер телефона для связи."
    )
    assert templates.render("GREETING", "tg", {"greeting": "day", "confirmation_pending_order_id": 5}) == (
        "Рӯз ба хайр! Барои тасдиқи фармоиши №5 «Ҳа» нависед."
    )


def test_product_lines_of_the_boxes() -> None:
    box = {
        "name": "Фисташковые синнамоны",
        "price": "100.00",
        "unit": "кор.",
        "description": "Коробочка из 4 синнамонов.",
    }
    facts = {"products": [box], "asked_specific": True}
    # one period after the unit abbreviation, not "кор.."
    assert templates.render("PRODUCT_INFO", "ru", facts) == (
        "Фисташковые синнамоны — 100 сомони / кор. Коробочка из 4 синнамонов."
    )
    assert templates.render("PRODUCT_INFO", "tg", facts) == (
        "Фисташковые синнамоны — 100 сомонӣ / қуттӣ. Коробочка из 4 синнамонов."
    )
    summary = templates.render("ORDER_SUMMARY", "tg", {"items": [{**box, "quantity": 2}], "total": "200.00"})
    assert "Фисташковые синнамоны — 2 қуттӣ" in summary


def test_timing_notes_name_the_past_date_and_the_earliest_slot() -> None:
    past = templates.render(
        "ASK_MISSING", "ru", {"timing_problem": "delivery_date_past", "problem_date": "2026-08-05"}, ["delivery_date"]
    )
    assert past == "05.08.2026 уже прошло 🙂\nНа какую дату нужен заказ?"
    soon = templates.render(
        "ASK_MISSING",
        "ru",
        {
            "timing_problem": "delivery_too_soon",
            "min_lead_time_hours": 24,
            "earliest_date": "2026-09-17",
            "earliest_time": "14:00",
            "earliest_relative": "tomorrow",
        },
        ["delivery_time"],
    )
    assert soon.startswith("Заказы принимаем не позднее чем за 24 ч. Самое раннее — завтра после 14:00.")
    soon_tg = templates.render(
        "ASK_MISSING",
        "tg",
        {"timing_problem": "delivery_too_soon", "earliest_date": "2026-09-20", "earliest_time": "10:00"},
        [],
    )
    assert "Аз ҳама барвақт — 20.09.2026 баъд аз соати 10:00." in soon_tg


def test_tajik_templates_use_the_khujand_register() -> None:
    """18.09.2026: the customers read northern colloquial Tajik — "раҳмат", "тайёр", "адрес", "доставка",
    "курер", "перевод" — not the literary "ташаккур", "омода", "суроға", "интиқол", "хаткашон"."""
    facts = {**SUMMARY_FACTS, "prepayment": {"wallet": "+992 92 000 00 00", "amount": "750.00", "banks": ""}}
    confirmed = templates.render("ORDER_CONFIRMED", "tg", facts)
    assert confirmed.startswith("Раҳмат! Фармоиши №12 тасдиқ шуд ✅\nДоставка: 16.09.2026, соати 18:00.")
    assert "гузаронед" in confirmed and "скриншоти переводро" in confirmed
    questions = templates.render("ASK_MISSING", "tg", {}, ["delivery_type", "address"])
    assert questions == (
        "Илтимос, аниқ кунед:\n1. Расонем ё худатон мегиред?\n"
        "2. Адреси расонданро нависед: микрорайон ё кӯча, хона, квартира ва ориентир."
    )
    receipt = templates.render(
        "RECEIPT_RESULT", "tg", {"order_id": 12, "amount": "750.00", "wallet": "5058270381156297"}
    )[:60]
    assert receipt.startswith("Чекро гирифтем, раҳмат! Перевод: 750 сомонӣ.")
    literary = ("ташаккур", "омода", "суроға", "интиқол", "хаткашон", "бигӯед", "мутаассифона", "бубахшед", "ҳуҷра")
    every_text = " ".join(templates._TEXTS["tg"].values()).lower() + " ".join(templates.FIELD_QUESTIONS["tg"].values())
    assert not any(word in every_text.lower() for word in literary), [w for w in literary if w in every_text.lower()]


@pytest.mark.parametrize("kind", [kind.value for kind in ReplyKind])
@pytest.mark.parametrize("language", ["ru", "tg"])
def test_every_kind_renders_in_both_languages_with_empty_facts(kind: str, language: str) -> None:
    assert templates.render(kind, language, {}, []).strip()


def test_unknown_kind_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        templates.render("SOMETHING", "ru", {})


def test_voice_not_recognized_text() -> None:
    assert templates.voice_not_recognized("ru") == "Не получилось разобрать голосовое, напишите, пожалуйста, текстом."
    assert templates.voice_not_recognized("tg").startswith("Паёми овозиро")


# --------------------------------------------------------------------------- responder


def test_template_kinds_never_reach_the_llm() -> None:
    llm = ScriptedLLM(reply="Заказ подтверждён, 999 сомони")
    responder = Responder(llm)
    for kind in TEMPLATE_KINDS:
        reply = responder.generate_reply(ReplyPlan(kind, "ru", {"order_id": 1}))
        assert reply.source == ReplySource.TEMPLATE
    assert llm.text_calls == []


def test_llm_wording_is_used_when_the_guard_accepts_it() -> None:
    llm = ScriptedLLM(reply="Здравствуйте! Медовик стоит 750 сомони 😊")
    responder = Responder(llm, catalog_names=["Медовик", "Наполеон"])
    # a question about one named product — the whole price list is always a template (05 §6)
    plan = ReplyPlan(
        ReplyKind.PRODUCT_INFO, "ru", {"products": [{"name": "Медовик", "price": "750.00"}], "asked_specific": True}
    )

    reply = responder.generate_reply(plan)

    # the customer did not greet in this message (no ``greeting`` fact): the model's opener is dropped
    assert reply.source == ReplySource.LLM and reply.text == "Медовик стоит 750 сомони 😊"
    assert reply.fixes == ("greeting_removed",)
    assert "KIND: PRODUCT_INFO" in llm.text_calls[0]["prompt"]


@pytest.mark.parametrize(
    ("llm_text", "violation"),
    [
        ("Медовик стоит 700 сомони", "amount_not_allowed:700.00"),
        ("Ваш заказ подтверждён!", "confirmation_claim"),
        ("Медовик в наличии, скидка 10%", "forbidden_phrase"),
        ("Есть Медовик и Наполеон", "product_not_allowed:Наполеон"),
        ("Салом! Медовик ҳаст, нархаш 750 сомонӣ", "language_mismatch:tg"),
    ],
)
def test_guard_violation_falls_back_to_the_template(llm_text: str, violation: str) -> None:
    responder = Responder(ScriptedLLM(reply=llm_text), catalog_names=["Медовик", "Наполеон"])
    plan = ReplyPlan(
        ReplyKind.PRODUCT_INFO,
        "ru",
        {"products": [{"name": "Медовик", "price": "750.00", "unit": "шт."}], "asked_specific": True},
    )

    reply = responder.generate_reply(plan)

    assert reply.source == ReplySource.FALLBACK
    assert reply.text == "Медовик — 750 сомони / шт."
    assert any(item.startswith(violation) for item in reply.violations), reply.violations


def test_item_choices_and_refusals_are_never_worded_by_the_model() -> None:
    # the model once turned "какие именно?" into "какие ещё три синнамона?" and "всё верно"
    llm = ScriptedLLM(reply="Отлично, всё верно! А какие ещё три синнамона?")
    facts = {
        "pending_items": [{"kind": "generic", "quantity": 2, "options": ["Классические синнамоны"]}],
        "order_so_far": {"items": [{"name": "Ягодные синнамоны", "quantity": 3, "unit": "кор."}]},
    }
    reply = Responder(llm).generate_reply(ReplyPlan(ReplyKind.ASK_MISSING, "ru", facts, ["items"]))
    assert reply.source == ReplySource.TEMPLATE and llm.text_calls == []
    assert reply.text == (
        "Записали: Ягодные синнамоны — 3 кор.\n"
        "Подскажите, пожалуйста, какие ещё 2 выбрать? Сейчас есть: Классические синнамоны."
    )
    timing = ReplyPlan(ReplyKind.ASK_MISSING, "ru", {"timing_problem": "delivery_too_soon"}, ["delivery_time"])
    assert Responder(ScriptedLLM(reply="Выберите другое время")).generate_reply(timing).source == ReplySource.TEMPLATE

    # a plain question about a missing field is still worded by the model
    plain = ReplyPlan(ReplyKind.ASK_MISSING, "ru", {}, ["delivery_date"])
    assert Responder(ScriptedLLM(reply="На какой день вам удобно?")).generate_reply(plain).source == ReplySource.LLM

    # "дальше" at the summary: the plain reminder, not the model's "Всё верно, заказ уже ждёт…"
    go_on = ReplyPlan(ReplyKind.SMALL_TALK, "ru", {"small_talk": "ack", "confirmation_pending_order_id": 7})
    reply = Responder(ScriptedLLM(reply="Всё верно, заказ уже ждёт подтверждения")).generate_reply(go_on)
    assert (reply.source, reply.text) == (ReplySource.TEMPLATE, "Чтобы подтвердить заказ №7, напишите «Да».")
    thanks = ReplyPlan(ReplyKind.SMALL_TALK, "ru", {"small_talk": "thanks"})
    assert Responder(ScriptedLLM(reply="Рады помочь!")).generate_reply(thanks).source == ReplySource.LLM


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ва алейкум ассалом! Навиштем. Фармоиш барои кадом рӯз лозим?", "Навиштем. Фармоиш барои кадом рӯз лозим?"),
        ("Ваалейкум ассалом, навиштем!", "Навиштем!"),
        ("Салом! навиштем", "Навиштем"),
        ("Ассалому алейкум! Медовик ҳаст.", "Медовик ҳаст."),
        ("Здравствуйте! Записали, на какой день?", "Записали, на какой день?"),
        ("Добрый день, записали.", "Записали."),
        ("Привет! Записали.", "Записали."),
        ("Рӯз ба хайр! Навиштем.", "Навиштем."),
    ],
)
def test_a_greeting_opener_is_stripped(text: str, expected: str) -> None:
    assert strip_greeting(text) == (expected, True)


@pytest.mark.parametrize("text", ["Саломат бошед! Боз биёед", "Навиштем, салом ба ҳама", "Записали. Здравствуйте?"])
def test_greeting_words_elsewhere_are_kept(text: str) -> None:
    assert strip_greeting(text) == (text, False)


def test_a_repeated_greeting_is_removed_from_the_model_reply() -> None:
    """Live Tajik dialog of 18.09.2026: the model copied "Ва алейкум ассалом!" from the history into every
    later reply. Without a ``greeting`` fact the opener goes, the rest of the wording stays (``fixes``)."""
    llm = ScriptedLLM(reply="Ва алейкум ассалом! Навиштем. Фармоиш барои кадом рӯз лозим?")
    plan = ReplyPlan(ReplyKind.ASK_MISSING, "tg", {}, ["delivery_date"], question_hint="Фармоиш барои кадом рӯз лозим?")

    reply = Responder(llm).generate_reply(plan)

    assert (reply.source, reply.text) == (ReplySource.LLM, "Навиштем. Фармоиш барои кадом рӯз лозим?")
    assert reply.fixes == ("greeting_removed",) and reply.violations == ()

    # the customer greeted in this very message: the greeting is answered and kept
    greeted = Responder(llm).generate_reply(replace(plan, facts={"greeting": "salam"}))
    assert greeted.text.startswith("Ва алейкум ассалом! ") and greeted.fixes == ()

    # nothing but a greeting → the template of the kind
    bare = Responder(ScriptedLLM(reply="Салом!")).generate_reply(plan)
    assert bare.source == ReplySource.FALLBACK and "empty_reply" in bare.violations
    assert bare.text == "Фармоиш барои кадом рӯз лозим?"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Спасибо! Печём под заказ, к дню выдачи.", "Печём под заказ, к дню выдачи."),
        ("Спасибо, стараемся делать её очень вкусной!", "Стараемся делать её очень вкусной!"),
        ("Спасибо большое за вопрос — доставка 14 сомони.", "Доставка 14 сомони."),
        ("Благодарим за сообщение! Кафе у нас нет.", "Кафе у нас нет."),
        ("Раҳмат, кӯшиш мекунем ки бомазза бошад!", "Кӯшиш мекунем ки бомазза бошад!"),
        ("Ташаккур! Бо фармоиш мепазем.", "Бо фармоиш мепазем."),
    ],
)
def test_a_thanks_opener_is_stripped(text: str, expected: str) -> None:
    assert strip_thanks(text) == (expected, True)


@pytest.mark.parametrize(
    "text",
    [
        "Печём под заказ. Спасибо, что дождались!",
        "Спасибочки — так мы не пишем",
        "Рахматулло привезёт заказ",
        "Записали, спасибо!",
    ],
)
def test_gratitude_elsewhere_is_kept(text: str) -> None:
    assert strip_thanks(text) == (text, False)


def test_an_answer_does_not_thank_the_customer_for_a_question() -> None:
    """Dialog #148, 23.09.2026: "а выпечка у вас вкусная?" — a doubt — came back as "Спасибо,
    стараемся делать её очень вкусной!", as if the customer had paid a compliment."""
    llm = ScriptedLLM(reply="Спасибо, стараемся! Печём под заказ, к дню выдачи.")
    facts = {"faq": [{"question": "А выпечка вкусная?", "answer": "Печём под заказ, к дню выдачи."}]}
    plan = ReplyPlan(ReplyKind.FAQ_ANSWER, "ru", facts, context={"customer_message": "а выпечка у вас вкусная ?"})

    reply = Responder(llm).generate_reply(plan)

    assert reply.text == "Стараемся! Печём под заказ, к дню выдачи."
    assert reply.fixes == ("thanks_removed",)

    # the customer really did thank: the reply may thank back
    thanked = Responder(llm).generate_reply(
        replace(plan, context={"customer_message": "Спасибо! А выпечка вкусная?"})
    )
    assert thanked.text.startswith("Спасибо, стараемся!") and thanked.fixes == ()

    # a reply that acknowledges data the customer gave keeps its "Спасибо"
    asking = ReplyPlan(
        ReplyKind.ASK_MISSING, "ru", {}, ["delivery_date"], context={"customer_message": "90 123 45 67"}
    )
    kept = Responder(ScriptedLLM(reply="Спасибо, записали номер. На какой день?")).generate_reply(asking)
    assert kept.text == "Спасибо, записали номер. На какой день?" and kept.fixes == ()


def test_llm_error_falls_back_to_the_template() -> None:
    reply = Responder(ScriptedLLM()).generate_reply(ReplyPlan(ReplyKind.CLARIFY, "ru"))
    assert reply.source == ReplySource.FALLBACK
    assert reply.text == "Извините, не получилось понять сообщение. Уточните, пожалуйста, что вас интересует?"


@pytest.mark.parametrize(
    ("language", "llm_text", "violation"),
    [
        # Russian sentence with a Tajik currency word is not a Tajik reply
        ("tg", "Торт «Наполеон» (слоёные коржи) стоит 200 сомонӣ за шт.", "language_mismatch:ru"),
        # Persian/Arabic script slipped into a Tajik reply
        ("tg", "Салом! Хуш омадед, мо хурсандем, ки ба شما кумак кунем", "foreign_script"),
    ],
)
def test_mixed_language_and_foreign_script_fall_back(language: str, llm_text: str, violation: str) -> None:
    responder = Responder(ScriptedLLM(reply=llm_text), catalog_names=["Торт «Наполеон»"])
    plan = ReplyPlan(
        ReplyKind.PRODUCT_INFO,
        language,
        {
            "products": [
                {"name": "Торт «Наполеон»", "price": "200.00", "unit": "шт.", "description": "слоёные коржи"}
            ],
            "asked_specific": True,
        },
    )
    reply = responder.generate_reply(plan)
    assert reply.source == ReplySource.FALLBACK
    assert any(item.startswith(violation) for item in reply.violations), reply.violations


def test_proper_tajik_reply_with_russian_product_name_passes() -> None:
    text = "Торти «Наполеон» 200 сомонӣ арзиш дорад, вазнаш 1,5 кг 😊"
    responder = Responder(ScriptedLLM(reply=text), catalog_names=["Торт «Наполеон»"])
    plan = ReplyPlan(
        ReplyKind.PRODUCT_INFO,
        "tg",
        {
            "products": [{"name": "Торт «Наполеон»", "price": "200.00", "unit": "шт.", "description": "1,5 кг"}],
            "asked_specific": True,
        },
    )
    reply = responder.generate_reply(plan)
    assert reply.source == ReplySource.LLM, reply.violations


def test_reply_context_reaches_the_prompt_but_not_the_facts() -> None:
    llm = ScriptedLLM(reply="Отлично, записали! На какой день вам удобно?")
    plan = ReplyPlan(
        ReplyKind.ASK_MISSING,
        "ru",
        {"order_so_far": {"items": [{"name": "Медовик", "quantity": 1}]}},
        ["delivery_date"],
        question_hint="На какую дату нужен заказ?",
        context={
            "customer_message": "хочу медовик, стоил 750 сомони в прошлый раз",
            "history": [{"role": "assistant", "text": "Здравствуйте!"}],
            "customer_name": "Алия",
        },
    )
    reply = Responder(llm, catalog_names=["Медовик"]).generate_reply(plan)
    assert reply.source == ReplySource.LLM
    prompt = llm.text_calls[0]["prompt"]
    assert "CUSTOMER NAME: Алия" in prompt

    # a price quoted by the customer is context, not a fact: the guard still rejects it
    echo = ScriptedLLM(reply="Медовик — 750 сомони, записали!")
    rejected = Responder(echo, catalog_names=["Медовик"]).generate_reply(plan)
    assert rejected.source == ReplySource.FALLBACK and any("amount_not_allowed" in v for v in rejected.violations)


def test_without_llm_everything_is_a_template() -> None:
    reply = Responder(None).generate_reply(ReplyPlan(ReplyKind.CLARIFY, "tg"))
    assert reply.source == ReplySource.TEMPLATE and reply.language == "tg"


def test_amounts_from_admin_texts_are_facts_but_dates_are_not() -> None:
    amounts = fact_amounts(
        {
            "total": "1500.00",
            "delivery_info": "Доставка по городу — 20 сомони, за город 1 000 сомони",
            "delivery_date": "2026-09-16",
            "orders": [{"order_id": 7, "total": 300}],
        }
    )
    assert {Decimal("1500"), Decimal("20"), Decimal("1000"), Decimal("300")} <= amounts
    assert Decimal("2026") not in amounts and Decimal("7") not in amounts
