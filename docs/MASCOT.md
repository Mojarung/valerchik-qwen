# Маскот — крыска-оператор

Милая крыска в гарнитуре колл-центра: шустрая, любопытная, мгновенно находит нужную карточку
в базе знаний (референс — сцена с перебором картотеки, только добрее и милее).

## Что уже реализовано

`frontend/src/components/Mascot.tsx` + `mascot.css` — **чистый SVG + CSS-анимации**, ноль
зависимостей, ~5 КБ. Крыска реагирует на события чата шестью состояниями:

| Состояние | Когда | Анимация |
|---|---|---|
| `idle` | ожидание | парит, моргает, хвост покачивается |
| `thinking` | агент ищет ответ | **перебирает карточки в выдвинутой картотеке**: лапка листает, карточки подпрыгивают волной, взгляд вниз, уши подрагивают |
| `speaking` | стриминг ответа | ротик «говорит» |
| `sorry` | ответа в БЗ нет | уши поникли, грустные брови, хвост опущен |
| `alert` | нарушение правил | строгие брови, красное кольцо, тряска |
| `happy` | ответ найден / оценка 5 | подпрыгивает, уши торчком, искры, хвост виляет |

Использование: `<Mascot mood="thinking" size={72} />`. Управление — из `ChatPage`
(маппинг WS-событий на настроение). Уважает `prefers-reduced-motion`.

## Промпт для генерации «богатой» 2D-версии (Midjourney/Flux/SDXL/GPT-image)

> Милая мультяшная крыска-маскот службы поддержки государственного портала закупок, стиль
> современный флэт-вектор с мягкими градиентами, округлые формы чиби, большая голова, огромные
> блестящие глаза с бликами, большие круглые уши с розовой серединкой, серая шёрстка со светлым
> пузиком, розовые щёчки, усики, длинный розовый хвостик, гарнитура оператора колл-центра
> с микрофоном, маленький красный бейдж поддержки на груди, белый фон, лист из 6 эмоций:
> нейтральная, ищет в картотеке с карточками, говорящая, извиняющаяся с поникшими ушами,
> строгая, радостная с искрами, консистентный персонаж, акцентный красный #D6001C, sticker sheet

Английский вариант (для Midjourney):

> cute chibi rat mascot for a government e-procurement support service, modern flat vector
> style with soft gradients, big round head, huge glossy sparkling eyes, large round ears with
> pink inner, grey fur with light belly, pink blush cheeks, whiskers, long pink tail, call
> center headset with mic, small red support badge, white background, character sheet with
> 6 emotions: neutral, searching through a card file drawer flipping index cards, talking,
> apologetic with drooped ears, stern, celebrating with sparkles, consistent character,
> accent red #D6001C, sticker sheet --v 7

## Как сделать анимацию «дороже»

1. **Lottie**: анимация в After Effects (или готовая с lottiefiles.com, CC-лицензия) → Bodymovin
   JSON → `npm i @lottiefiles/dotlottie-react` → `<DotLottieReact src="/mascot.lottie" />`.
   Состояния — сегменты таймлайна (`playSegment`).
2. **Rive**: персонаж + State Machine в rive.app (вход `mood`), на фронте `@rive-app/react-canvas`.
3. **Спрайты/видео**: WebM с альфой — просто, но тяжелее.

Текущий SVG — рабочий вариант; Lottie/Rive — drop-in замена внутри `Mascot.tsx`
(интерфейс `mood` сохраняется).

## 3D-крыска: где сгенерировать бесплатно

Рабочий пайплайн: **2D-реф (промпт выше) → image-to-3D → GLB → рендер/видео**.

| Сервис | Ссылка | Бесплатно |
|---|---|---|
| **Meshy AI** (text/image-to-3D) | https://www.meshy.ai | ежемесячные бесплатные кредиты |
| **Tripo AI** (сильные персонажи) | https://www.tripo3d.ai | стартовые + ежедневные кредиты |
| **Hunyuan3D** (опенсорс Tencent) | https://huggingface.co/spaces/tencent/Hunyuan3D-2 | полностью бесплатно (HF Space) |
| **TRELLIS** (опенсорс Microsoft) | https://huggingface.co/spaces/microsoft/TRELLIS | полностью бесплатно (HF Space) |
| **Luma Genie** | https://lumalabs.ai/genie | бесплатно |

Промпт для text/image-to-3D (EN — эти сервисы лучше понимают английский):

> cute chibi rat mascot character, big round head, huge glossy expressive eyes, large round
> ears with pink inner, grey fur with light belly, pink blush cheeks, long pink tail, call
> center headset with microphone, small red support badge on chest, stylized Pixar-like 3D
> toon character, rounded soft shapes, T-pose, game-ready, clean topology, vibrant solid
> colors, studio lighting

Экспорт в **GLB** — в веб через `<model-viewer>` (авто-вращение, ~100 КБ) или
three.js/react-three-fiber.

## Милое видео с крыской: бесплатные генераторы

Проще всего: картинка маскота → **image-to-video**.

| Сервис | Ссылка | Бесплатно |
|---|---|---|
| **Kling AI** | https://www.klingai.com | ежедневные бесплатные кредиты |
| **Hailuo (MiniMax)** | https://hailuoai.video | ежедневные бесплатные генерации |
| **Vidu** | https://www.vidu.com | бесплатные кредиты |
| **Pika** | https://pika.art | free-тариф |

Промпт для видео (image-to-video):

> RU: милая мультяшная крыска в гарнитуре оператора поддержки быстро перебирает карточки
> в выдвижном ящике картотеки, находит нужную, радостно подпрыгивает и машет лапкой,
> большие блестящие глаза, мягкое студийное освещение, пастельный фон, плавная пружинистая
> squash-and-stretch анимация, бесшовный цикл
>
> EN: cute cartoon chibi rat wearing a call-center headset quickly flips through index cards
> in a card file drawer, finds the right card, hops happily and waves its paw, huge glossy
> eyes, soft studio lighting, pastel background, smooth bouncy squash-and-stretch animation,
> seamless loop, 3D toon style

Готовое видео: WebM с альфа-каналом → приветственный экран чата; MP4-цикл — для презентации.

> Бонус: в проекте доступен Blender MCP с генераторами Hunyuan3D/Hyper3D — 3D-модель крыски
> можно сгенерировать прямо в Blender по этому же промпту и отрендерить видео локально.
