# Видео-промпты для крыски-маскота

Исходник: `docs/mascot-sheet.png` — стикер-лист с 6 эмоциями (сгенерирован).
Референс одной позы: `docs/mascot-ref.png`.

## Как генерировать (важно)

1. **Режим:** Image-to-Video (не text-to-video!) — так стиль останется консистентным.
2. **Стартовый кадр:** вырежи из стикер-листа НУЖНУЮ позу (кроп одного стикера)
   и загружай именно её. Для «поиска» — стикер №2, для «радости» — №6 и т.д.
3. **Камера:** всегда статичная. Фон: белый/пастельный, без текста.
4. **Длительность:** в Kling выбирай **5s** (для сценарных — 10s), в Hailuo стандарт ~6s,
   в Vidu — 4–8s. Для веба ролик потом зацикливается, поэтому просим «seamless loop».
5. **Куда идёт результат:** MP4 — в презентацию; для встройки в интерфейс — прогнать через
   unscreen.com (уберёт фон) → WebM с альфой.

Каждый промпт ниже: назначение → длительность → EN-промпт (основной, сервисы понимают лучше)
→ RU-дубль.

---

## 1. NEUTRAL — ожидание (idle-цикл в шапке чата)

**Длительность: 5 сек, бесшовный цикл.**

> EN: cute chibi grey rat mascot in a call-center headset sits calmly, gentle breathing,
> slow blinking of huge glossy eyes, ears twitch slightly, long pink tail sways softly,
> subtle idle animation, static camera, plain white background, consistent 2D cartoon
> sticker style, smooth squash-and-stretch, seamless loop, no text

> RU: милая крыска в гарнитуре сидит спокойно, мягко дышит, медленно моргает большими
> блестящими глазами, ушки слегка подрагивают, розовый хвост плавно покачивается,
> статичная камера, белый фон, бесшовный цикл

## 2. SEARCHING — ищет в базе знаний (экран «думает»)

**Длительность: 10 сек (в Kling — 10s; можно 5s для короткой версии).**

> EN: cute chibi grey rat in a headset rapidly flips through index cards in an open card
> file drawer, paws riffling the cards one by one, focused frown, ears perked, then pulls
> out one card and looks at it curiously, static camera, plain white background, consistent
> 2D cartoon sticker style, smooth bouncy animation, seamless loop, no text

> RU: крыска в гарнитуре быстро перебирает лапками карточки в выдвижном ящике картотеки,
> сосредоточенно хмурится, ушки торчком, затем вытаскивает одну карточку и с любопытством
> её рассматривает, статичная камера, белый фон, бесшовный цикл

## 3. TALKING — отвечает пользователю

**Длительность: 5 сек, цикл.**

> EN: cute chibi grey rat in a headset talks cheerfully into the microphone, mouth moving,
> one paw gesturing as if explaining, friendly smile, occasional nod, ears bounce slightly,
> static camera, plain white background, consistent 2D cartoon sticker style, seamless loop

> RU: крыска в гарнитуре радостно говорит в микрофон, ротик двигается, одной лапкой
> жестикулирует, будто объясняет, дружелюбная улыбка, кивает, статичная камера, белый фон,
> бесшовный цикл

## 4. APOLOGETIC — извиняется (нет ответа в базе)

**Длительность: 5 сек.**

> EN: cute chibi grey rat in a headset looks sad and apologetic, ears droop down slowly,
> big glossy eyes look down, small sigh, paws folded together shyly, tail lies still,
> static camera, plain white background, consistent 2D cartoon sticker style, gentle
> slow animation, no text

> RU: крыска в гарнитуре грустно извиняется: ушки медленно опускаются, большие глаза
> смотрят вниз, лёгкий вздох, лапки виновато сложены, хвостик неподвижен, статичная камера,
> белый фон, медленная мягкая анимация

## 5. STERN — строгая (нарушение правил)

**Длительность: 5 сек.**

> EN: cute chibi grey rat in a headset looks stern and disapproving, arms crossed,
> eyebrows furrowed, slowly shakes head no, taps foot impatiently, ears slightly back,
> static camera, plain white background, consistent 2D cartoon sticker style, no text

> RU: крыска в гарнитуре строго качает головой, лапки скрещены, брови нахмурены,
> нетерпеливо притопывает, ушки чуть назад, статичная камера, белый фон

## 6. CELEBRATING — радуется (ответ найден / оценка 5)

**Длительность: 5 сек, цикл.**

> EN: cute chibi grey rat in a headset celebrates joyfully, jumps up with both paws raised,
> eyes closed in happiness, big smile, red and white sparkles pop around, tail wiggles fast,
> bouncy squash-and-stretch jump, static camera, plain white background, consistent 2D
> cartoon sticker style, seamless loop, no text

> RU: крыска в гарнитуре радостно подпрыгивает с поднятыми лапками, глаза зажмурены от
> счастья, широкая улыбка, вокруг разлетаются красно-белые искорки, хвостик быстро виляет,
> пружинистый прыжок, статичная камера, белый фон, бесшовный цикл

## 7. WELCOME — приветствие (hero-экран, страница логина)

**Длительность: 5 сек, цикл.**

> EN: cute chibi grey rat in a headset waves its paw at the camera in greeting, friendly
> wink, slight head tilt, ears perk up, tail curls happily, welcoming smile, static camera,
> plain white background, consistent 2D cartoon sticker style, seamless loop, no text

> RU: крыска в гарнитуре приветливо машет лапкой в камеру, дружелюбно подмигивает,
> слегка наклоняет голову, ушки торчком, хвостик радостно изгибается, статичная камера,
> белый фон, бесшовный цикл

## 8. STORY — сценарный ролик для презентации

**Длительность: 10 сек (Kling 10s). Стартовый кадр — стикер №2 (searching).**

> EN: cute chibi grey rat in a call-center headset quickly flips through index cards in a
> card file drawer, pulls out the right card, eyes light up, then jumps up celebrating with
> sparkles around, waves the card proudly at the camera, smooth bouncy squash-and-stretch
> animation, static camera, plain white background, consistent 2D cartoon sticker style

> RU: крыска в гарнитуре быстро перебирает карточки в картотеке, вытаскивает нужную,
> глаза загораются, радостно подпрыгивает с искорками вокруг и гордо показывает карточку
> в камеру, пружинистая анимация, статичная камера, белый фон

---

## Сводка длительностей

| # | Ролик | Длительность | Цикл |
|---|---|---|---|
| 1 | Neutral (idle) | 5 c | да |
| 2 | Searching (картотека) | 10 c | да |
| 3 | Talking | 5 c | да |
| 4 | Apologetic | 5 c | нет |
| 5 | Stern | 5 c | нет |
| 6 | Celebrating | 5 c | да |
| 7 | Welcome | 5 c | да |
| 8 | Story (презентация) | 10 c | нет |

Минимальный набор для демо: **№7 (welcome) + №2 (searching) + №8 (story)**.
