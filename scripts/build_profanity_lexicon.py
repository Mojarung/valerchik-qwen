"""Собирает словарь грубых слов для guardrails из готовых списков.

Запуск (нужен pymorphy3 из окружения бэкенда):
    cd backend && uv run python ../scripts/build_profanity_lexicon.py <папка со списками>

Списки — txt, по слову в строке. Брать их целиком нельзя: там «аборт», «анус»,
«буфер», «бордель» — обычные слова закупок и интерфейса, и обращение с ними
получило бы предупреждение. Поэтому отбор такой:

- слова, которых нет в словаре OpenCorpora, — почти сплошь производные мата
  («вымандохивать», «хитрожопый»): нормальная речь из словаря не выпадает;
- слова с пометкой «разговорное» или «жаргон» (Infr, Slng): «мудак», «херня»;
- известные словарю слова без пометок выбрасываются: грубые из них («дурак»,
  «жопа») и так ловят корни в guardrails.py, остальные — нейтральны.

К словам добавляются их начальные формы, кроме тех, что совпали с обычным
словом: разбор незнакомого слова — догадка, и «ебсти» даёт начальную форму
«есть», которой в словаре грубостей делать нечего.
"""

import sys
from pathlib import Path

import pymorphy3

OUT = Path(__file__).resolve().parents[1] / "backend/app/services/lexicons/profanity.txt"
MARKS = {"Infr", "Slng"}
MIN_LEN = 3
# Скрипт и папка со списками — ничего другого на вход не принимаем
EXPECTED_ARGS = 2
# Грубоватые или нейтральные слова, которые часто встречаются в обычных жалобах:
# «ни фига не работает» — это не оскорбление.
STOP = {"фига", "матюг", "анал"}


def _read(src: Path) -> set[str]:
    words: set[str] = set()
    for path in sorted(p for p in src.iterdir() if p.is_file()):
        for line in path.read_text(encoding="utf-8").splitlines():
            word = line.strip().lower().replace("ё", "е")
            if len(word) >= MIN_LEN and all("а" <= ch <= "я" for ch in word):
                words.add(word)
    return words


def build(src: Path) -> list[str]:
    morph = pymorphy3.MorphAnalyzer()

    def marked(word: str) -> bool:
        return any(set(map(str, p.tag.grammemes)) & MARKS for p in morph.parse(word)[:2])

    def neutral(word: str) -> bool:
        return morph.word_is_known(word) and not marked(word)

    picked = {w for w in _read(src) if not neutral(w)} - STOP
    lemmas = {morph.parse(w)[0].normal_form.replace("ё", "е") for w in picked}
    return sorted(picked | {lemma for lemma in lemmas if not neutral(lemma)} - STOP)


def main() -> None:
    if len(sys.argv) != EXPECTED_ARGS:
        sys.exit(__doc__)
    entries = build(Path(sys.argv[1]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    header = "# Сгенерировано scripts/build_profanity_lexicon.py — правьте списки и пересоберите.\n"
    OUT.write_text(header + "\n".join(entries) + "\n", encoding="utf-8")
    print(f"{OUT}: {len(entries)} слов")


if __name__ == "__main__":
    main()
