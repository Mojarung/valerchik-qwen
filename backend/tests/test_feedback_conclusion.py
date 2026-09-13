"""Заключение отчёта по отзывам: у каждого совета есть адресат — специалист, тема или статья.

Раньше список дел выходил безымянным: «разобрать нерешённые обращения», «разобрать
эти обращения» — без того, кому и что. Здесь держим, что каждая строка «Что
сделать» говорит, кого она касается, а результат работы и понятность названы
отдельно.
"""

from app.services.feedback import Findings, _compose_conclusion, _recommendations, system_problems

SUMMARY = {"count": 20, "avg_rating": 3.9, "positive_share": 0.7, "solved_rate": 0.75, "clear_rate": 0.6}
WEAK = {
    "name": "Сергей Ефимов",
    "kind": "operator",
    "index": 38,
    "low_data": False,
    "weak_spots": [
        {
            "dimension": "clear",
            "text": "объясняет непонятно: «непонятно» в 5 из 6 отзывов",
            "advice": "объяснять по шагам",
        }
    ],
}
AI = {
    "name": "ИИ-ассистент",
    "kind": "ai",
    "index": 80,
    "low_data": False,
    "weak_spots": [],
    "thumbs_up": 11,
    "thumbs_down": 5,
    "thumbs_unclear": 3,
}
TOPIC = {
    "topic": "Электронная подпись",
    "flagged": True,
    "unsolved": 3,
    "solved_total": 5,
    "unclear": 2,
    "clear_total": 5,
}
ARTICLE = {
    "article": "Как подписать контракт?",
    "topic": "Электронная подпись",
    "cases": 4,
    "bad": 3,
    "unsolved": 1,
    "unclear": 3,
    "bad_share": 0.75,
    "flagged": True,
}
ABOUT_WEAK = {
    "size": 4,
    "representative": "Объяснили непонятно, ничего не понял.",
    "keywords": [],
    "examples": [],
    "focus": "Сергей Ефимов",
    "focus_count": 3,
}
NOBODYS = {"size": 2, "representative": "Долго ждал.", "keywords": [], "examples": []}


def _problems():
    return system_problems([WEAK, AI], [TOPIC], None, [ABOUT_WEAK, NOBODYS], articles=[ARTICLE])


def _first(kind):
    return next(p for p in _problems() if p["kind"] == kind)


def test_problems_name_specialist_topic_and_article():
    assert _first("specialist")["target"] == "Сергей Ефимов"
    topic = _first("topic")
    assert topic["target"] == "тема «Электронная подпись»"
    # правку темы начинают с её худшей статьи
    assert "начать со статьи «Как подписать контракт?»" in topic["advice"]
    article = _first("article")
    assert article["target"] == "статья «Как подписать контракт?»"
    assert article["detail"] == (
        "после ответа по статье недовольны в 3 из 4 диалогов: не помогло — 1, непонятно — 3"
    )
    # непонятную статью переписывают, а не ищут в ней ошибку
    assert article["advice"].startswith("переписать проще")


def test_article_that_did_not_help_is_checked_not_rewritten():
    wrong = dict(ARTICLE, unsolved=3, unclear=1)
    (problem,) = [p for p in system_problems([], [], None, [], articles=[wrong]) if p["kind"] == "article"]
    assert problem["advice"] == "проверить, верны ли шаги и не устарела ли статья"


def test_complaint_gets_an_addressee():
    complaints = [p for p in _problems() if p["kind"] == "complaint"]
    assert complaints[0]["detail"].endswith("; 3 из 4 — Сергей Ефимов")
    assert complaints[0]["target"] == "Сергей Ефимов"
    # без общего адресата советом правит сама жалоба
    assert complaints[1]["target"] == "жалоба «Долго ждал.»"


def test_one_line_per_addressee():
    lines = _recommendations(_problems())
    assert lines[0] == "Сергей Ефимов: объяснять по шагам"
    # жалоба на того же оператора не повторяет его строку
    assert sum(line.startswith("Сергей Ефимов") for line in lines) == 1
    assert lines[1].startswith("тема «Электронная подпись»: проверить статьи этой темы")
    assert lines[2] == "статья «Как подписать контракт?»: переписать проще: по шагам, без служебных слов"
    assert lines[3] == "жалоба «Долго ждал.»: разобрать эти обращения и устранить общую причину"


def test_conclusion_separates_result_and_clarity_and_names_articles():
    text = _compose_conclusion(SUMMARY, [WEAK, AI], [], Findings(topics=[TOPIC], articles=[ARTICLE]))
    assert "Результат работы: проблему решили в 75% случаев." in text
    assert (
        "Качество донесения: понятно объяснили в 60% случаев; под ответами ИИ-ассистента "
        "«Непонятно» отметили 3 раза из 16 оценок." in text
    )
    assert "Статьи, после которых чаще недовольны: «Как подписать контракт?» (недовольны в 3 из 4)." in text


def test_long_article_title_is_shortened():
    long = dict(ARTICLE, article="Как " + "очень " * 30 + "длинно?")
    (problem,) = [p for p in system_problems([], [], None, [], articles=[long]) if p["kind"] == "article"]
    assert len(problem["title"]) < 100
    assert problem["title"].endswith("…»")
