"""Выгрузка службы поддержки: одна статья на ответ, который оператор дал не раз.

Строка такой выгрузки — обращение, а не статья. Один и тот же ответ («Если вы
обнаружили ошибку в СТЕ…») оператор вставляет сотням заявителей, а «Описание»
начинается с «Подтема запроса: …» — статья на строку даёт десятки тысяч дублей с
мусорным вопросом. Поэтому строки складываются по ответу, а статьёй становится
только ответ, данный хотя бы дважды: второе употребление — единственная доступная
нам улика, что текст писался не под одного заявителя, а как переиспользуемый ответ.

Повтора мало. «На момент проверки УПД в статусе „Подписано заказчиком“» и
«Заказчику направлено приглашение на установку связи» операторы пишут сотнями, но
это не инструкции, а отчёт о состоянии одной заявки или о действии, сделанном за
заявителя. Следующему человеку никто ничего не направлял — выдать ему такой ответ
значит соврать. Такие группы становятся случаями для оператора: отвечать ими
нельзя, а понять по ним, что обращение решает человек и на какой линии, — можно.
"""

from __future__ import annotations

import html
import logging
import re
from collections import Counter
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from urllib.parse import unquote

from app.ingest.blob import SHEET_SUFFIXES, FileBlob
from app.ingest.chunking import _normalized_text
from app.ingest.records import ArticleRecord, Provenance
from app.ingest.registry import IngestContext, register
from app.services import kbkind
from app.services.wordforms import STOPWORDS, plural, stem

log = logging.getLogger(__name__)

# Колонки, по которым книга опознаётся как выгрузка поддержки, — все три сразу:
# «Тема» и «Описание» есть и у справочников, «Решение» отличает тикет-систему.
REQUIRED_COLUMNS = ("тема", "описание", "решение")
IMPACT_COLUMN = "влияние"
# Выше spreadsheet (0.90): тот тоже берётся за книгу и, узнав «Описание» и
# «Решение», честно сделает по статье на строку — ровно то, чего избегаем.
CONFIDENCE = 0.97

# Короче этого ответ — отметка о закрытии («Исправлено», «Предоставлены
# полномочия.»), а не ответ: медиана содержательного ответа в выгрузке около
# 180 символов, а всё короче 40 — сплошь такие отметки. Порог взят с запасом.
MIN_ANSWER_CHARS = 60
# Текст обращения короче — «ошибка», «см. скрин»: по нему не выбрать ни вопрос,
# ни ключевые слова, а ответ у такой строки обычно есть и в других.
MIN_TEXT_CHARS = 10
# Ответ, данный однажды, мог быть написан под ситуацию одного заявителя (номер
# контракта, дата, имя). Второе употребление — единственная улика, что текст
# годится как статья: оператор сам счёл его переиспользуемым.
MIN_REPEATS = 2
# Вопрос статьи — реальное обращение: короче 20 символов это «не работает»,
# длиннее 160 — рассказ с предысторией, из которого вопроса не собрать.
QUESTION_MIN_CHARS = 20
QUESTION_MAX_CHARS = 160
# Вопросу нужны хотя бы два предметных слова: «ЧТО МНЕ С ЭТИМ ДЕЛАТЬ???» и «Прошу
# разъяснить, где проблема» — вопросы по форме, но ни слова о том, что случилось, а
# бот показывает вопросы статей человеку, когда переспрашивает, какой ответ нужен.
QUESTION_MIN_STEMS = 2
# Один ответ оператор вставляет на разные обращения («нет кнопки», «не могу
# создать КС», «как изменить адрес»), и самое типичное из них не всегда то, на
# которое ответ отвечает. Совпадение слов с ответом весит половину типичности.
ANSWER_RELEVANCE_WEIGHT = 0.5
# Форма вопроса — довесок, а не решающий довод: пока знак вопроса весил больше
# всего остального, у группы про тип грузополучателя побеждало «ЧТО МНЕ С ЭТИМ
# ДЕЛАТЬ???», а у группы про заявку на изменение данных — «…в корзину????».
QUESTION_MARK_BONUS = 0.2
QUESTION_OPENER_BONUS = 0.1
# Другие формулировки того же вопроса — то, по чему статью найдёт поиск, когда
# следующий заявитель напишет иначе. Длинные тексты в ключевых словах только
# размывают индекс, и шести формулировок на статью хватает.
KEYWORD_MIN_CHARS = 15
KEYWORD_MAX_CHARS = 120
MAX_KEYWORD_TEXTS = 6
# Формулировка, у которой с уже взятой общих слов больше половины, — перефраз,
# а не новая: она добавит индексу вес, но не охват.
KEYWORD_SIMILARITY = 0.5
# Формулировки для поиска (pick_phrasings): каждая — своя точка векторного
# индекса. В группе ответа десятки обращений, но уже десяток самых типичных и
# непохожих покрывает, как о той же беде пишут по-разному; остальное — повторы.
MAX_PHRASINGS = 10
# Перефраз узнаётся по основам значимых слов: «Не могу подписать УПД» и «Как
# подписать УПД?» — одна формулировка, «УПД не уходит заказчику» — другая.
PHRASING_SIMILARITY = 0.5
# Два ответа с таким совпадением слов — один ответ в редакции разных операторов
# («направлено приглашение» / «отправлено приглашение»). Порознь они делили бы
# между собой и вопросы, и место в выдаче, и уточняющий вопрос предлагал бы
# выбрать между одним и тем же.
NEAR_DUPLICATE = 0.7
# Инцидент «услуга недоступна» ведёт техническая линия; вопросы — первая.
# Группа считается инцидентной, когда инцидентов в ней больше половины: один
# заявитель с «Наивысшее» на сотню «RFI» не делает вопрос инцидентом.
TECHNICAL_LINE = 2
INCIDENT_SHARE = 0.5
# «RFI - запрос информации» и «RFC - запрос на изменение» — это вопросы,
# всё остальное в колонке «Влияние» — степень недоступности услуги.
_REQUEST_PREFIXES = ("rfi", "rfc")
# Инструкция, оставшаяся после вычета отписки, должна быть инструкцией, а не
# хвостом фразы: «Ожидайте.» после «Заявка отправлена» статьёй не станет.
MIN_INSTRUCTION_CHARS = 80
# Ответ, в котором кроме просьб к заявителю («в случае если токен указан верно,
# предоставьте скриншот») почти ничего нет, — встречный вопрос, а не ответ.
MIN_SUBSTANCE_CHARS = 40

_SUBTOPIC = re.compile(r"^\s*Подтема запроса:\s*(.*?)\s*/\s*(.*)$", re.DOTALL)
# Экранирование markdown, которое тикет-система оставила в тексте: «1\\.», «\\-»,
# «\\<». Пользователю оно уходит как есть, поэтому снимаем весь набор, а не
# только точку и дефис.
_ESCAPED = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>])")
# Разметка тикет-системы: цвет текста <c #1a1a1a>…</c>, подсветка <m #00004004>…</m>,
# абзацы <p> и таблицы <td rowspan="6">. Пользователю она уходила как есть — в
# ленте чата буквально «<m #ffff00><c #1a1a1a>По вопросам блокировки…». Снимаем
# только латинские теги: кириллический <НомерСЧФ> — кусок XML, о котором и спрашивают.
_MARKUP = re.compile(
    r"</?(?:c|m)(?:\s+#[0-9a-f]{3,8})?\s*>"
    r"|</?(?:td|tr|th|table|tbody|thead|br|p|li|ul|ol|h[1-6]|div|span|center|b|i|u|strong|em)(?:\s+[^<>]*)?/?>",
    re.IGNORECASE,
)
# Подчёркивание тикет-системы: «**+Важно+**». Плюсы — разметка, а не текст.
_UNDERLINE = re.compile(r"(?<![\w+])\+(?=\S)([^+\n]{1,60}?)(?<=\S)\+(?![\w+])")
# Ссылка через переадресацию ВКонтакте: оператор копировал её из мессенджера, и
# человек, нажав «сайт налоговой», попадал сначала на vk.com.
_VK_AWAY = re.compile(r"https?://(?:m\.)?vk\.com/away\.php\?to=([^&\s)\]]+)[^\s)\]]*")
# Отчёт об ошибке, который портал сам кладёт в обращение: «ID запроса поддержки:
# 2887…,Текст ошибки: РДИК_ИК_0003 с Строка160Тип,ID шаблона: 31f8…,Решение: <p>…».
# Номер запроса и шаблон — шум, а «Текст ошибки» — ровно то, что человек потом
# наберёт в чате: код с экрана. Без разбора восьмая часть выгрузки выпадала из
# вопросов и ключевых слов целиком — по номеру запроса.
_ERROR_REPORT = re.compile(
    r"(?:I?D\s+запроса\s+поддержки:\s*\w*\s*,?\s*)?Текст\s+ошибки:\s*(?P<error>.+?)\s*,\s*ID\s+шаблона:.*$",
    re.IGNORECASE | re.DOTALL,
)
_ERROR_CODE = re.compile(r"^[A-ZА-ЯЁ]{2,}_\w+")
_URL = re.compile(r"https?://\S+")
_SPACES = re.compile(r"\s+")
_SENTENCES = re.compile(r"(?<=[.!?])\s+")
# Точка сокращения («ст. 15», «т.е. не…», «г. Москвы») фразу не заканчивает: иначе
# «Контракт заключен с основанием «ст.» считался отдельной фразой-отчётом, и от
# ответа оставалось «15 46-ФЗ Дополнительные случаи…».
_ABBREVIATION = re.compile(
    r"(?:^|[\s(«\"])(?:ст|п|пп|ч|г|гг|т\.е|т\.к|т\.п|т\.д|руб|ред|стр|им|др|ул|д|см|утв|напр|тел|рис|разд|прил)\.$",
    re.IGNORECASE,
)
_GREETING = (
    r"(?:добр\w*\s+(?:день|дня|утро|утра|вечер|вечера|время\s+суток)|здравствуйте|приветствую"
    r"|доброго\s+времени\s+суток|дд(?=[\s!.,]))"
)
# Приветствие в начале ответа или обращения: в статье базы знаний оно лишнее, а в
# ответе посреди диалога «Добрый день!» звучит так, будто бот забыл, что уже
# здоровался. Их бывает и два подряд: «Добрый день, уважаемые коллеги!».
_LEADING_GREETING = re.compile(
    rf"^\s*(?:(?:{_GREETING}|уважаем\w*\s+\w+|коллеги)[\s!.,:;-]*)+", re.IGNORECASE
)
_TRAILING_THANKS = re.compile(
    r"[\s,.;!-]*(?:заранее\s+)?спасибо[\s!.]*$|[\s,.;!-]*с уважением.*$", re.IGNORECASE
)
_REPEATED_MARKS = re.compile(r"[?!]{2,}")

# Отписка про одну заявку узнаётся по началу фразы. Условие («если пользователь
# уже зарегистрирован…», «после того как заказчик примет…») — не отчёт, а
# инструкция для всех, поэтому такие фразы под правила не попадают вовсе.
_NOT_CONDITION = r"^(?!(?:если|в случае|когда|после|пока|чтобы|для|как только)\b)"
# До четырёх слов перед кратким причастием («Приглашение на установку связи
# переотправлено»). Модальное слово среди них делает фразу правилом, а не отчётом:
# «УПД должен быть подписан…».
_WORDS_BEFORE = (
    r"(?:(?!(?:должн|мож|могут|буд|быть|нужн|необходим|следует|нельз|требуе)\w*\s)[\w«»\"-]+\s+){0,4}?"
)
# Краткие причастия действий, которые оператор делает за заявителя. Краткое
# причастие заканчивает слово, поэтому «изменение», «удаление» и полные причастия
# («заключенные контракты») под правило не попадают.
_SENT_PARTICIPLES = r"(?:направлен|отправлен|переотправлен|перенаправлен)"
_PARTICIPLES = (
    r"(?:" + _SENT_PARTICIPLES + r"|внесен|внесён|заведен|актуализирован|удален|изменен|подписан|заблокирован"
    r"|разблокирован|предоставлен|восстановлен|обновлен|перенесен|добавлен|снят|отклонен|одобрен|согласован"
    r"|переведен|передан|создан|сформирован|проверен|выгружен|получен|зафиксирован|аннулирован|расторгнут"
    r"|заключен|принят|возвращен|продлен|активирован|закрыт|завершен|интегрирован|допущен|приложен)[аоы]?\b"
)
# Состояние на момент проверки и отметка «исправлено». Что после них — обычно
# годится всем («…Необходимо подписывать исполнение строго по ссылке с портала»),
# и такой остаток спасается как ответ. «Приглашение не принято», «дата не
# выгружена» — тоже состояние, а не действие: отрицание ничего не сделало.
_STATE_OPENER = re.compile(
    _NOT_CONDITION + r"(?:на момент проверки|при проверке|по результатам проверки|проверено"
    r"|в настоящ(?:ий|ее) (?:момент|время)|на текущий момент|по информации,? полученной"
    r"|в рамках (?:вашего |данного )?(?:обращения|запроса)|дубль (?:запроса|обращения)|скриншот приложен"
    r"|статус электронного исполнения|ожидайте|выполнено|готово|сделано"
    r"|" + _WORDS_BEFORE + r"(?:исправлен|устранен|решен|урегулирован|неактуальн)[аоы]?\b"
    r"|" + _WORDS_BEFORE + r"(?:не )?(?:воспроизводится|подтверждается)\b"
    r"|" + _WORDS_BEFORE + r"не (?:был[аои]? )?" + _PARTICIPLES + r")",
    re.IGNORECASE,
)
# Действие, сделанное за заявителя: «победа возвращена», «исполнение создано без
# ошибок», «пробел в токене удалён». Что идёт после такого начала, от него и
# зависит («Переподпишите оферту» — только тому, кому вернули победу), поэтому
# остаток не спасается.
_ACTION_OPENER = re.compile(_NOT_CONDITION + _WORDS_BEFORE + _PARTICIPLES, re.IGNORECASE)
# Исключение — отправка: «Заказчику направлено приглашение. Заказчику требуется
# принять приглашение, далее повторно подписать УПД». Приглашение и заявку
# заявитель обычно отправляет сам кнопкой на портале, и то, что после, — общий
# порядок действий, а не последствия чужого вмешательства.
_SENT_OPENER = re.compile(_NOT_CONDITION + _WORDS_BEFORE + _SENT_PARTICIPLES + r"[аоы]?\b", re.IGNORECASE)
# «Контракт в системе заказчика в статусе „Согласование“» — тоже состояние одной
# заявки, но только без инструкции в той же фразе: «УПД в статусе „Черновик“
# можно удалить» — правило для всех.
_IN_STATUS = re.compile(_NOT_CONDITION + r"(?:[\w«»\"-]+\s+){0,5}?в статусе\b", re.IGNORECASE)
# Сбой, о котором сообщают всем пострадавшим: «заведена массовая задача», «ведутся
# работы». Он временный: любой совет в таком ответе — про этот сбой, а повторять
# его после починки значит сообщать о поломке, которой нет. Такой ответ — случай.
_INCIDENT = re.compile(
    r"массов\w+\s+(?:задач|проблем|ошибк|сбо)|ведутся работы|ведут работы|сложности в работе"
    r"|технически\w+ работ|восстановительн\w+ работ|стадии устранения",
    re.IGNORECASE,
)
# Остаток, который начинается со ссылки назад («Его необходимо удалить…» после
# «На ГИС ЕИС создан черновик») или с середины списка («2. Сумма…»), без
# отрезанной фразы не читается.
_ANAPHORA = re.compile(
    r"^(?:(?:его|её|ее|их|он|она|оно|они|так\s*же|тогда|поэтому|в связи с этим)\b|(?:[2-9]|\d{2,})[.)])",
    re.IGNORECASE,
)
# Встречный вопрос вместо ответа: без данных заявителя оператор не мог ответить.
# «Направить» и «предоставить» — просьба о данных, только если дальше сами данные
# (скриншот, ссылка, файл): «просьба повторно направить новую заявку модераторам» —
# инструкция всем, у кого заявку отклонили.
_ASK_DATA = r"(?:скриншот|ссылк|номер|файл|архив|xml|информаци|сведени|пример|фото|видео)"
_ASKS_BACK = re.compile(
    r"(?:просьба|просим)\s+(?:[\w,]+\s+){0,4}?(?:прислать|уточнить|сообщить|указать|переоткрыть"
    r"|(?:предоставить|направить)\s+(?:[\w,]+\s+){0,3}?" + _ASK_DATA + r")"
    r"|предоставьте|пришлите|уточните,? пожалуйста|(?:направьте|отправьте) (?:нам )?" + _ASK_DATA,
    re.IGNORECASE,
)
# Просьба на случай («в случае возобновления ошибки просьба прислать скриншот») —
# совет всем, а не вопрос одному заявителю: отпиской ответ она не делает.
_CONDITIONAL = re.compile(
    r"(?:в случае|если|при (?:повтор|возобнов|сохранен|возникновен))[^.]{0,80}?"
    r"(?:просьба|просим|предоставьте|пришлите|направьте|отправьте)",
    re.IGNORECASE,
)
# Частности одной заявки: длинные номера (реестровый номер, КПП, ИНН), «№ 60»,
# GUID исполнения или пакета ЭДО (бывает и обрезан), время с секундами, период
# контроля «с … по …», ссылка на конкретную закупку или исполнение. Просто дата —
# не частность: «С 01.04.2025 соглашение подписывается в ЕИС» — правило для всех.
_CONCRETE = re.compile(
    r"\b\d{7,}\b|№\s?\S*\d|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{8,12}|\b\d{1,2}:\d{2}:\d{2}\b"
    r"|\d{1,2}\.\d{1,2}\.\d{4}\s+\d{1,2}:\d{2}|\bс \d{1,2}\.\d{1,2}\.\d{4} по \d{1,2}\.\d{1,2}\.\d{4}",
    re.IGNORECASE,
)
# Ссылка — частность, только если ведёт на конкретную закупку или исполнение.
# Цифры в ссылке на раздел портала или инструкцию («…/im/home/-2156133059644390»)
# ничего не говорят о заявке, и такие ссылки при проверке вырезаются целиком.
_CASE_LINK = re.compile(
    r"zakupki\.mos\.ru/\S*?(?:auction|contract|sku|offer|order|need|execution)\S*?\d{4,}", re.IGNORECASE
)
# Реквизиты нормативного акта («приказом … от 08.06.2023 № ДПР-ПР-81/23», «№ 44-ФЗ»)
# — не частность: их цитируют всем, и без вычета ответ со ссылкой на закон уходил в
# отписки по «№», хотя это самые общие ответы выгрузки. Номеров бывает и два
# подряд: «Приказ № 08.06.2023 №ДПР-ПР-81/23».
_LEGAL_ACT = re.compile(
    r"(?:закон|кодекс|постановлени|приказ|письм|распоряжени|регламент|положени|приложени|правительств|управлени"
    r"|политик|министерств|минфин|комитет|федераци|департамент)\w*[^;\n]{0,80}?"
    r"(?:(?:№|(?-i:N)\b)\s?[\w./-]*\d[\w./-]*\s*)+"
    r"|\b\d+-(?:ФЗ|ПП)\b|\(ред\.? от [^)]*\)"
    r"|\bот \d{1,2}\.\d{1,2}\.\d{4}(?:\s*г\.?)?\s*(?:№|(?-i:N)\b)\s?[\w./-]+",
    re.IGNORECASE,
)
# Признаки инструкции: что сделать самому или как устроено, а не что сделали за тебя.
_INSTRUCTS = re.compile(
    r"необходимо|нужно|требуется|следует|рекоменд|выполните|перейдите|нажмите|откройте|выберите"
    r"|сформируйте|создайте|проверьте|укажите|подайте|повторите|заполните|прикрепите|измените|удалите"
    r"|используйте|воспользуйтесь|отправьте|подпишите|зарегистрируйтесь|войдите|обновите|исправьте|добавьте"
    r"|обратит|должн|обязан|для того|чтобы|если|в случае|можно|означает|предусмотрен|допуска",
    re.IGNORECASE,
)
_ASKED = re.compile(
    r"^(?:как|где|почему|что|зачем|когда|куда|откуда|можно ли|нужно ли|каким|какой|какая|какие|каков"
    r"|сколько|не могу|не можем|не получается|не удается|не удаётся|не приходит|не отображается"
    r"|не работает|нет\b|ошибка|подскажите)",
    re.IGNORECASE,
)
# Приметы одной заявки в тексте обращения: номер запроса или контракта («1232А/25»,
# «№ 3-3/1087ТС»), дата, ИНН, инициалы, ссылка. Такой вопрос — про одну заявку, а
# не про тему, а в уточняющем вопросе бота чужой договор или чужая фамилия — утечка.
_CASE_DETAILS = re.compile(
    r"\d{5,}|№\s?\S*\d|\d{1,2}\.\d{1,2}\.\d{2,4}|\b\d+[а-яёa-z]*/\d+|\bинн\b|id запроса|https?://"
    r"|(?-i:\b[А-ЯЁ]\.\s?[А-ЯЁ]\.)",
    re.IGNORECASE,
)
_WORD = re.compile(r"[а-яёa-z0-9]+")
# Слова, которые есть в любом обращении и не говорят, о чём оно: в счёт двух
# предметных слов вопроса они не идут.
_VAGUE = frozenset(
    stem(word)
    for word in (
        "проблема ошибка помочь помогите разъяснить устранить устранении вопрос ситуация срочно решить "
        "делать сделать работает скрин скриншот прилагаю вложение повторно повторяется обращение почему"
    ).split()
)
# «Организация не заблокирована» и «организация зарегистрирована» делят семь слов
# из десяти, но это разные ответы: частица переворачивает смысл.
_NEGATIONS = frozenset({"не", "нет"})

# Чем фраза относится к одной заявке (см. _case_part)
_STATE = "state"
_SENT = "sent"
_ACTION = "action"
_ASK = "ask"
_DETAIL = "detail"


def _squash(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def _sentences(text: str) -> list[str]:
    """Фразы текста; точка сокращения и продолжение со строчной буквы фразу не рвут."""
    merged: list[str] = []
    for piece in _SENTENCES.split(text.strip()):
        if not piece:
            continue
        if merged and (_ABBREVIATION.search(merged[-1]) or piece[:1].islower()):
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return merged


def _error_question(error: str) -> str:
    """«РДИК_0161 - ошибка с контрактом» → «Ошибка РДИК_0161: ошибка с контрактом»."""
    error = _squash(error)
    code, dash, rest = error.partition(" - ")
    if dash and _ERROR_CODE.match(code):
        return f"Ошибка {code}: {rest}"
    return f"Ошибка {error}" if _ERROR_CODE.match(error) else error


def clean_appeal(text: str) -> str:
    """Текст обращения без разметки, HTML-сущностей и служебного отчёта портала.

    От отчёта об ошибке остаётся её текст: «…помогите разобраться. Ошибка
    РДИК_0161: ошибка с контрактом».
    """
    text = _squash(html.unescape(_MARKUP.sub(" ", text)))
    report = _ERROR_REPORT.search(text)
    if report is None:
        return text
    error = _error_question(report.group("error"))
    before = text[: report.start()].strip(" ,.;")
    return f"{before}. {error}" if len(before) >= MIN_TEXT_CHARS else error


def split_description(description: str) -> tuple[str, str]:
    """«Подтема запроса: X/текст» → (подтема, текст). Без префикса подтемы нет."""
    matched = _SUBTOPIC.match(description)
    if matched is None:
        return "", clean_appeal(description)
    return _squash(matched.group(1)), clean_appeal(matched.group(2))


def clean_answer(answer: str) -> str:
    """Ответ оператора как текст: без разметки тикет-системы, экранирования,
    переадресации ссылок, приветствия в начале и лишних пробелов."""
    text = _VK_AWAY.sub(lambda link: unquote(link.group(1)), _MARKUP.sub(" ", answer))
    text = _squash(html.unescape(_UNDERLINE.sub(r"\1", _ESCAPED.sub(r"\1", text))))
    stripped = _squash(_LEADING_GREETING.sub("", text, count=1))
    # «Добрый день, необходимо…» без приветствия начиналось бы со строчной буквы
    return stripped[:1].upper() + stripped[1:] if stripped else text


def clean_topic(topic: str) -> str:
    """«Работа с СТЕ.» и «Работа с СТЕ» — одна тема: хвостовая точка — не рубрика."""
    return _squash(topic).strip(" .;,")


def answer_key(answer: str) -> str:
    """Ключ группировки: регистр, завершающие точки и пробелы ответ не меняют."""
    return _squash(answer).rstrip(". ").casefold()


def _asks_back(sentence: str) -> bool:
    return bool(_ASKS_BACK.search(sentence)) and not _CONDITIONAL.search(sentence)


def _concrete(text: str) -> bool:
    if _CASE_LINK.search(text):
        return True
    return bool(_CONCRETE.search(_URL.sub(" ", _LEGAL_ACT.sub(" ", text))))


def _case_part(sentence: str) -> str:
    """Чем фраза относится к одной заявке: состояние, отправка, иное действие за
    заявителя, встречная просьба или частность; пусто — фраза годится всем."""
    text = _LEADING_GREETING.sub("", sentence, count=1).strip()
    if _STATE_OPENER.match(text) or (_IN_STATUS.match(text) and not _INSTRUCTS.search(text)):
        return _STATE
    if _SENT_OPENER.match(text):
        return _SENT
    if _ACTION_OPENER.match(text):
        return _ACTION
    if _asks_back(text):
        return _ASK
    if _concrete(text):
        return _DETAIL
    return ""


@dataclass(frozen=True)
class _Split:
    """Ответ, разобранный на фразы для всех и фразы про одну заявку."""

    kept: list[str]
    dropped: list[str]
    # Остаток зависит от отрезанного: в начале — действие за заявителя, остаток
    # ссылается на отрезанную фразу или из середины вынута частность. Спасать
    # такой остаток как ответ нельзя.
    tied: bool


def _split(answer: str) -> _Split:
    sentences = _sentences(answer)
    dropped: list[str] = []
    tied = False
    while sentences and (part := _case_part(sentences[0])):
        tied = tied or part == _ACTION
        dropped.append(sentences.pop(0))
    if dropped and sentences and _ANAPHORA.match(sentences[0]):
        tied = True
    tail: list[str] = []
    while sentences and _case_part(sentences[-1]):
        tail.insert(0, sentences.pop())
    kept: list[str] = []
    for sentence in sentences:
        part = _case_part(sentence)
        # Действие посреди текста чаще объясняет причину, чем отчитывается: «На ГИС
        # ЕИС создан черновик исполнения. Его необходимо удалить» — без первой фразы
        # вторая не читается. Поэтому оно остаётся на месте
        if not part or part == _ACTION:
            kept.append(sentence)
            continue
        # Состояние и просьба посреди текста снимаются без потерь, а частность
        # держит на себе соседние фразы: «…указан КПП 771601001. Обратитесь к
        # заказчику» без номера — совет ни о чём
        tied = tied or part == _DETAIL
        dropped.append(sentence)
    return _Split(kept=kept, dropped=dropped + tail, tied=tied)


def split_status(answer: str) -> tuple[str, str]:
    """(отписка, инструкция): фразы про одну заявку отделяются от того, что осталось.

    «На момент проверки исполнение в статусе „Подписание поставщиком“. Необходимо
    подписывать исполнение в ГИС ЕИС строго по ссылке с портала» — первая фраза
    про одну заявку, вторая годится всем, кто спросит то же самое. Отчёт снимается
    с обоих концов («…Скриншот приложен.»), а частности заявки («Ваш ИдТрПакет: …»)
    и встречные просьбы — где бы ни стояли.
    """
    split = _split(answer)
    return " ".join(split.dropped), " ".join(split.kept)


def _substance(text: str) -> str:
    """Текст без просьб к заявителю — даже «на случай»: они не инструкция."""
    return " ".join(s for s in _sentences(text) if not _ASKS_BACK.search(s))


def _is_instruction(text: str) -> bool:
    """Остаток годится в ответ всем: длинный, с инструкцией и без частностей заявки.

    Просьба «на случай» остаётся в тексте, но инструкцией не считается: «Ошибка
    исправлена. В случае возобновления просьба прислать скриншот» — отписка.
    """
    substance = _substance(text)
    return (
        len(substance) >= MIN_INSTRUCTION_CHARS and bool(_INSTRUCTS.search(substance)) and not _concrete(text)
    )


def classify_answer(answer: str) -> tuple[str, str]:
    """-> (вид записи, текст ответа статьи).

    Вид — kbkind.ANSWER (инструкция на всех) или kbkind.OPERATOR_CASE (отписка про
    одну заявку или про временный сбой). Если вокруг отписки есть настоящая
    инструкция, статьёй становится она, а отписка отбрасывается.
    """
    if _INCIDENT.search(answer):
        return kbkind.OPERATOR_CASE, answer
    split = _split(answer)
    if not split.dropped:
        if len(_substance(answer)) < MIN_SUBSTANCE_CHARS:
            return kbkind.OPERATOR_CASE, answer
        return kbkind.ANSWER, answer
    rest = " ".join(split.kept)
    if not split.tied and _is_instruction(rest):
        return kbkind.ANSWER, rest
    return kbkind.OPERATOR_CASE, answer


def _is_incident(impact: str) -> bool:
    """«Услуга недоступна» — инцидент, а не вопрос; RFI и RFC — запросы.

    Пустое влияние — не инцидент: в выгрузке без такой колонки все обращения
    иначе уехали бы на техническую линию.
    """
    head = impact.strip().casefold()
    return bool(head) and not head.startswith(_REQUEST_PREFIXES)


def _noisy(text: str) -> bool:
    return bool(_CASE_DETAILS.search(text))


def tidy_question(text: str) -> str:
    """Обращение → формулировка вопроса: без приветствия, благодарностей и хвоста.

    «Добрый день!Ошибка при попытке подписать допсоглашение на площадке. Спасибо» →
    «Ошибка при попытке подписать допсоглашение на площадке».
    """
    text = _TRAILING_THANKS.sub("", _LEADING_GREETING.sub("", _squash(text), count=1)).strip(" ,;-")
    # «Как подписать????????» и «Почему?!» — один знак: вопрос читается и так
    text = _REPEATED_MARKS.sub(lambda marks: "?" if "?" in marks.group(0) else "!", text)
    if len(text) > QUESTION_MAX_CHARS:
        # Длинное обращение — вопрос и предыстория; вопросом берём первую фразу
        first = _sentences(text)[0]
        text = first if len(first) >= QUESTION_MIN_CHARS else text
    if text.isupper():
        # Капслок в уточняющем вопросе бота читался бы как крик самого бота
        text = text.lower()
    return text[:1].upper() + text[1:] if text else text


def _words(text: str) -> frozenset[str]:
    # Ссылки — не слова: закодированный путь инструкции даёт десятки токенов
    # «d0», «bd», и два разных ответа со ссылкой на одну инструкцию казались одним
    return frozenset(_normalized_text(_URL.sub(" ", text)).split())


# Короче трёх букв — предлоги и частицы («в», «не», «по»); «УПД», «ЭДО» и «СТЕ»
# как раз трёхбуквенные и остаются
_MIN_WORD_CHARS = 3


def _stems(text: str) -> frozenset[str]:
    """Основы значимых слов: служебные и вежливые слова не говорят, о чём вопрос."""
    words = _WORD.findall(text.lower().replace("ё", "е"))
    return frozenset(stem(word) for word in words if len(word) >= _MIN_WORD_CHARS and word not in STOPWORDS)


def _jaccard(first: frozenset[str], second: frozenset[str]) -> float:
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def _ranked(texts: list[str], answer: str) -> list[tuple[str, frozenset[str]]]:
    """Годные в вопрос обращения группы с основами их слов, лучшие первыми.

    Лучшее — типичное для группы (его слова встречаются в других обращениях, а не
    в одном этом), совпадающее по словам с ответом и, при прочих равных, похожее
    на вопрос (знак вопроса, «как», «не могу…»). Номера заявок, даты и ссылки
    дисквалифицируют: такой вопрос — про одну заявку, а не про тему.
    """
    tidy = [tidy_question(text) for text in texts]
    fit = list(
        dict.fromkeys(t for t in tidy if QUESTION_MIN_CHARS <= len(t) <= QUESTION_MAX_CHARS and not _noisy(t))
    )
    stems = {t: _stems(t) for t in fit}
    fit = [t for t in fit if len(stems[t] - _VAGUE) >= QUESTION_MIN_STEMS]
    if not fit:
        return []
    bags = [_stems(t) for t in tidy]
    frequency = Counter(word for bag in bags for word in bag)
    answer_bag = _stems(answer)

    def rank(candidate: str) -> tuple[float, int]:
        own = stems[candidate]
        # Доля обращений группы, где встречается слово кандидата, в среднем по его
        # словам: слова темы («УПД», «подписать») тянут вверх, подробности одного
        # заявителя («школа», «вчера») — вниз
        typical = sum(frequency[word] for word in own) / (len(own) * len(bags))
        relevant = len(own & answer_bag) / len(own)
        form = (QUESTION_MARK_BONUS if candidate.endswith("?") else 0.0) + (
            QUESTION_OPENER_BONUS if _ASKED.match(candidate) else 0.0
        )
        # Короче — при прочих равных лучше: длинное — это рассказ, а не вопрос
        score = typical + ANSWER_RELEVANCE_WEIGHT * relevant + form - len(candidate) / 1000
        return (score, -len(candidate))

    # Сортировка устойчива: при равной оценке первым остаётся встретившийся раньше
    return [(t, stems[t]) for t in sorted(fit, key=rank, reverse=True)]


def pick_question(texts: list[str], answer: str = "") -> str:
    """Лучшее реальное обращение группы как вопрос статьи; пусто — не из чего."""
    ranked = _ranked(texts, answer)
    return ranked[0][0] if ranked else ""


def pick_phrasings(texts: list[str], answer: str = "") -> list[str]:
    """Самые типичные и непохожие друг на друга обращения группы, лучшее первым.

    Первой идёт та же формулировка, что стала вопросом статьи (pick_question),
    дальше — по убыванию типичности, но без перефразов уже взятых: каждая
    формулировка станет отдельной точкой векторного индекса, и десять почти
    одинаковых точек заняли бы место в выдаче, ничего не добавив к охвату.
    Чистка та же, что у вопроса: без приветствий, благодарностей, номеров заявок.
    """
    picked: list[str] = []
    taken: list[frozenset[str]] = []
    for text, stems in _ranked(texts, answer):
        if len(picked) >= MAX_PHRASINGS:
            break
        if any(_jaccard(stems, other) >= PHRASING_SIMILARITY for other in taken):
            continue
        picked.append(text)
        taken.append(stems)
    return picked


def _similar(words: frozenset[str], taken: list[frozenset[str]]) -> bool:
    return any(_jaccard(words, other) >= KEYWORD_SIMILARITY for other in taken if other)


def pick_keywords(texts: list[str], question: str, labels: tuple[str, ...]) -> list[str]:
    """Подтема, тема и до шести непохожих друг на друга формулировок обращения."""
    keywords = [label for label in labels if label]
    taken = [_words(question)]
    picked = 0
    for raw in texts:
        if picked >= MAX_KEYWORD_TEXTS:
            break
        text = tidy_question(raw)
        if not KEYWORD_MIN_CHARS <= len(text) <= KEYWORD_MAX_CHARS or text == question or _noisy(text):
            continue
        words = _words(text)
        if not words or _similar(words, taken):
            continue
        taken.append(words)
        keywords.append(text)
        picked += 1
    return keywords


def _most_common(values: Iterable[str]) -> str:
    """Самое частое непустое значение; при равенстве — встретившееся раньше."""
    counted = Counter(value for value in values if value)
    return counted.most_common(1)[0][0] if counted else ""


def _column(headers: list[str], name: str) -> int:
    """Индекс колонки по имени: точное совпадение раньше вхождения («Подтема»)."""
    low = [header.strip().casefold() for header in headers]
    if name in low:
        return low.index(name)
    return next((index for index, header in enumerate(low) if name in header), -1)


def _cell(cells: list[str], index: int) -> str:
    return cells[index] if 0 <= index < len(cells) else ""


@dataclass(frozen=True)
class _Layout:
    """Номера колонок выгрузки; «Влияние» необязательно."""

    topic: int
    description: int
    solution: int
    impact: int = -1

    @classmethod
    def of(cls, headers: list[str]) -> _Layout | None:
        """None — это не выгрузка поддержки."""
        topic, description, solution = (_column(headers, name) for name in REQUIRED_COLUMNS)
        if min(topic, description, solution) < 0:
            return None
        return cls(topic, description, solution, _column(headers, IMPACT_COLUMN))


@dataclass(frozen=True)
class _Appeal:
    """Одна строка выгрузки, уже разобранная."""

    row: int
    topic: str
    subtopic: str
    text: str
    answer: str
    incident: bool

    @classmethod
    def read(cls, row: int, cells: list[str], layout: _Layout) -> _Appeal | None:
        """None — строка без ответа по существу или без текста обращения."""
        answer = clean_answer(_cell(cells, layout.solution))
        subtopic, text = split_description(_cell(cells, layout.description))
        if len(answer) < MIN_ANSWER_CHARS or len(text) < MIN_TEXT_CHARS:
            return None
        return cls(
            row=row,
            topic=clean_topic(_cell(cells, layout.topic)),
            subtopic=subtopic,
            text=text,
            answer=answer,
            incident=_is_incident(_cell(cells, layout.impact)),
        )


def near_duplicates(first: str, second: str) -> bool:
    """Один ли это ответ в редакции разных операторов."""
    one, other = _words(first), _words(second)
    return _jaccard(one, other) >= NEAR_DUPLICATE and not (one ^ other) & _NEGATIONS


def _merge_near_duplicates(groups: list[list[_Appeal]]) -> list[list[_Appeal]]:
    """Сливает группы, чьи ответы — один текст в редакции разных операторов.

    Частые группы идут первыми и принимают в себя редкие: у частой и ответ
    отточеннее, и формулировок больше.
    """
    ordered = sorted(groups, key=len, reverse=True)
    merged: list[list[_Appeal]] = []
    answers: list[str] = []
    for group in ordered:
        answer = _most_common(appeal.answer for appeal in group)
        for index, other in enumerate(answers):
            if near_duplicates(answer, other):
                merged[index].extend(group)
                break
        else:
            merged.append(list(group))
            answers.append(answer)
    return merged


def _grouped(sheet: str, file: str, rows: list[list[str]], layout: _Layout) -> list[list[_Appeal]]:
    """Обращения по ответу, частые группы первыми; отброшенное — в лог.

    Обе потери видны только здесь: строка без ответа по существу и ответ без
    повтора выкидываются молча, а «статьи × 979» из листа на 25 000 строк
    человек прочитает как поломку, если рядом нет объяснения.
    """
    groups: dict[str, list[_Appeal]] = {}
    skipped = 0
    for index, cells in enumerate(rows, start=1):
        appeal = _Appeal.read(index, cells, layout)
        if appeal is None:
            skipped += 1
            continue
        groups.setdefault(answer_key(appeal.answer), []).append(appeal)
    kept = [group for group in groups.values() if len(group) >= MIN_REPEATS]
    if skipped:
        log.info(
            "Лист «%s» файла %s: пропущено строк без ответа по существу — %d из %d",
            sheet,
            file,
            skipped,
            len(rows),
        )
    if len(kept) < len(groups):
        log.info(
            "Лист «%s» файла %s: пропущено ответов без повтора — %d (статья требует %d обращений)",
            sheet,
            file,
            len(groups) - len(kept),
            MIN_REPEATS,
        )
    merged = _merge_near_duplicates(kept)
    if len(merged) < len(kept):
        log.info(
            "Лист «%s» файла %s: склеено почти одинаковых ответов — %d", sheet, file, len(kept) - len(merged)
        )
    # Частые вопросы первыми: их и проверять глазами в первую очередь. Порядок
    # внутри равных сохраняется — сортировка устойчива и в обратную сторону.
    merged.sort(key=len, reverse=True)
    return merged


@register
class SupportExportSource:
    name = "support_export"
    # Тай-брейк на случай равной уверенности: специализированнее любого
    # табличного источника, поэтому выше их всех.
    priority = 30

    async def sniff(self, blob: FileBlob) -> float:
        if blob.suffix not in SHEET_SUFFIXES:
            return 0.0
        try:
            sheets = blob.tables
        except Exception:  # noqa: BLE001 — битая книга: пусть пробует кто-то другой
            return 0.0
        for _, frame in sheets:
            if _Layout.of([str(column) for column in frame.columns]) is not None:
                return CONFIDENCE
        return 0.0

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[ArticleRecord]:
        for sheet_name, frame in blob.tables:
            layout = _Layout.of([str(column) for column in frame.columns])
            if layout is None:
                continue
            rows = [[str(cell) for cell in row] for row in frame.values.tolist()]
            kinds: Counter[str] = Counter()
            for group in _grouped(sheet_name, blob.name, rows, layout):
                record = self._article(group, sheet_name, blob, ctx)
                kinds[record.origin.kind] += 1
                yield record
            if kinds[kbkind.OPERATOR_CASE]:
                log.info(
                    "Лист «%s» файла %s: ответов-инструкций %d, отписок про одну заявку %d "
                    "(станут случаями для оператора, отвечать ими нельзя)",
                    sheet_name,
                    blob.name,
                    kinds[kbkind.ANSWER],
                    kinds[kbkind.OPERATOR_CASE],
                )

    def _article(self, group: list[_Appeal], sheet: str, blob: FileBlob, ctx: IngestContext) -> ArticleRecord:
        """Группа обращений с одним ответом → статья или случай для оператора."""
        topic = _most_common(appeal.topic for appeal in group)
        subtopic = _most_common(appeal.subtopic for appeal in group)
        texts = [appeal.text for appeal in group]
        # Самый частый вариант написания: у него меньше всего шансов оказаться
        # тем единственным, где оператор опечатался.
        kind, answer = classify_answer(_most_common(appeal.answer for appeal in group))
        phrasings = pick_phrasings(texts, answer)
        # Без подходящего обращения вопросом становится подтема: «Изменение
        # данных пользователя» — уже вопрос, пусть и без знака. Лучшая
        # формулировка и есть pick_question — второй раз её не ищем.
        question = (phrasings[0] if phrasings else "") or subtopic or topic
        incidents = sum(1 for appeal in group if appeal.incident)
        rows = [appeal.row for appeal in group]
        return ArticleRecord(
            question=question,
            answer=answer,
            # Категория есть в самих данных — колонка «Тема». Снаружи её не
            # спрашиваем: у одной выгрузки восемьсот тем.
            category=topic,
            line=TECHNICAL_LINE if incidents / len(group) > INCIDENT_SHARE else None,
            audience=ctx.params.get("audience", "all"),
            keywords=pick_keywords(texts, question, (subtopic, topic)),
            # Как этот вопрос задавали на самом деле: по формулировкам статью
            # находит тот, кто написал не так, как её вопрос
            phrasings=phrasings,
            # Ответ писал оператор, и уходил он заявителю как есть — значит,
            # и пользователю его можно отдать дословно.
            origin=Provenance(
                file=blob.name,
                locator=(
                    f"лист «{sheet}», строки {min(rows)}…{max(rows)} "
                    f"({len(group)} {plural(len(group), ('обращение', 'обращения', 'обращений'))})"
                ),
                source_name=self.name,
                authored=True,
                kind=kind,
            ),
        )
