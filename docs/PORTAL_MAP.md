# Карта Портала поставщиков (zakupki.mos.ru)

Результат разбора портала «изнутри» — без распознавания экрана. На этой карте
стоят пошаговые сценарии расширения (`backend/app/guides/portal.json`) и импорт
базы знаний портала (`scripts/portal_kb.py`). Снято 2 сентября 2026 с боевого
портала, версия фронта `v.20260811`.

## Как портал устроен

- **SPA на React** (webpack, один `main.<hash>.js` ~5,6 МБ + 330 ленивых
  чанков), стили — styled-components. В бандле 367 маршрутов с заголовками
  страниц; русские строки лежат как `\uXXXX`, поэтому перед grep их надо
  декодировать.
- **Рантайм-конфиг** публичный: `GET /newapi/api/Js/GetJsSettings` отдаёт адреса
  бэкендов (`webApiUrl`, `classifierApiUrl`, `cmsUrl`), флаги и имя
  телеграм-бота.
- **Три бэкенда**: `/newapi` (реестры, каталог, база знаний, чат), `/classifier`
  (карточка СТЕ), `/cms` (Orchard-подобная CMS: GraphQL `/cms/api/graphql`,
  документы `/cms/Media/docs/*.pdf`). Авторизация — Keycloak на том же домене:
  `/auth/realms/PpRealm/…`; кнопка «Зарегистрироваться» ведёт на страницу входа
  Keycloak, регистрация — по ссылке под формой.
- **Свой маскот** (`/lib/maskot-animation/maskot.js`, anime.js) и свой чат
  (`mos.ditokc.ru`), а также встроенные туры на `react-joyride`
  (`OperableInteractiveTourTooltipStyles__*` на формах регистрации).

## База знаний портала (открытое API)

Без авторизации, GET, JSON:

| Эндпоинт | Что отдаёт |
|---|---|
| `/newapi/api/KnowledgeBase/GetArticlesBySectionType?sectionType=supplier\|customer\|general` | Категории → услуги → статьи (HTML в `selfServicePortal`, `sectionTypes`, `staticId`) |
| `/newapi/api/KnowledgeBase/GetQuestionsBySectionType?sectionType=…&count=N` | Популярные вопросы раздела |
| `/newapi/api/KnowledgeBase/GetServicesBySectionType?sectionType=…&count=N` | Услуги раздела со статьями |
| `/newapi/api/KnowledgeBase/QuerySmartSearch` (POST) | Умный поиск по базе знаний |
| `/newapi/api/KnowledgeBase/GetArticlesPreview?query=…` | Превью статей |

На 02.09.2026: **252** статьи для поставщиков, **45** для заказчиков, **62** общих
(после снятия дублей — 326). Категории: «Закупочные процедуры», «Работа с
контрактами», «Работа с СТЕ», «Работа с СТЕ и офертами», «Личный кабинет»,
«Уполномоченный орган (Региональный Заказчик)», «Карточка поставщика».
Именно эта рубрикация по ролям легла в основу переключателя «поставщик /
заказчик» в Валерчике. Импорт: `uv run --no-project python scripts/portal_kb.py --out kb.json`.

Инструкции в PDF (`/cms/Media/docs/…`): регистрация (с ЭП и без), создание
оферты и СТЕ, участие в КС, электронное актирование, МЧД, YML, для заказчика,
для уполномоченного органа.

## Маршруты, которые нужны сценариям

| Путь | Заголовок | Кому |
|---|---|---|
| `/` | Главная | все |
| `/registration` → `/auth/realms/PpRealm/protocol/openid-connect/auth?…` | Вход / регистрация (Keycloak) | все |
| `/auth/realms/PpRealm/login-actions/registration` | Форма регистрации пользователя | все |
| `/account-type-selection` | Выбор типа учётной записи (юрлицо / ИП / физлицо, присоединение к организации) | после входа |
| `/registration-user-change-request/:id` | Заявка на регистрацию компании | после входа |
| `/registration/congratulation` | Регистрация завершена | после входа |
| `/instruction/supplier`, `/instruction/customer` | «Как стать поставщиком / заказчиком» (6 и 4 шага) | все |
| `/knowledgebase/main/:section` | Центр поддержки (general / supplier / customer) | все |
| `/purchase/list` | Единый реестр закупок (вкладки: все, КС, потребности, конкурентные, B2B) | все |
| `/auction/:id` | Котировочная сессия | все |
| `/sku/catalog`, `/sku/view/:id` | Каталог СТЕ и карточка товара (предложения, «В корзину») | все |
| `/contract/list`, `/contract/:id` | Реестр контрактов | все |
| `/electronicSignature` | Электронная подпись | все |
| `/profile/user`, `/profile/company/:id?` | Профиль пользователя / компании | кабинет |
| `/profile/my-offers/list` | Мои оферты | поставщик |
| `/profile/sku-change-requests-new/:requestId` | Создать предложение и СТЕ | поставщик |
| `/profile/my-auctions/list`, `/profile/my-auctions/createNew` | Мои котировочные сессии / создание КС | заказчик |
| `/cart` | Корзина (прямая закупка по оферте) | заказчик |
| `/profile/contract/list`, `/profile/contractExecution/list` | Мои контракты, реестр исполнений | кабинет |
| `/profile/mrpa/:userId?` | Машиночитаемые доверенности | кабинет |
| `/profile/school` | Школа поставщика | поставщик |

Полный список (367 маршрутов с заголовками) восстанавливается из бандла:
`grep -aoE '"[a-zA-Z.]+":\{(exact:!0,)?path:"/[^"]*",title:"[^"]*"' main.decoded.js`.

## Устойчивые селекторы

CSS-классы у портала хешированные, но styled-components собраны с
`displayName`, поэтому у каждого элемента есть стабильный префикс вида
`Компонент__Часть-sc-xxxx`. Сценарии используют `[class*="Компонент__Часть"]`.

| Элемент | Селектор |
|---|---|
| Кнопка «Войти» | `[data-testid="top-menu__login-btn"]` |
| Кнопка «Зарегистрироваться» | `[data-testid="top-menu__register-btn"]` |
| Ссылка «Центр поддержки» в шапке | `a[href="/knowledgebase/main"]` |
| Корзина в шапке | `[class*="CartPopupStyles__CartButtonContainer"]`, `a[href="/cart"]` |
| Служба поддержки (гарнитура) | `[class*="SupportModalStyles__Trigger"]` |
| Бургер главного меню | `[class*="MainMenuButtonStyles__BurgerMenu"]` |
| Keycloak: логин / пароль | `[data-testid="login-form__login-input"]`, `[data-testid="login-form__password-input"]` |
| Keycloak: «Нет аккаунта? Зарегистрируйтесь» | `a[href*="login-actions/registration"]` |
| Keycloak: способы входа | `[class*="LoginFormStyles__MethodBlock"]` |
| Реестр закупок: вкладки | `[class*="PublicListTabSelectorStyles__Tab"]` |
| Реестр: «Показать фильтры» | `[class*="PublicListHeaderActionsStyles__ShowFilterButton"]` |
| Карточка закупки / контракта: название | `a[class*="CardStyles__MainInfoNameHeader"]` |
| Каталог: строка поиска | `input[placeholder="Введите название товара или услуги"]` |
| Каталог: популярные карточки | `a[class*="SkuCatalogStyles__PopularCard"]` |
| Карточка товара: «В корзину» | `[class*="CartButtonStyles__AddToCartButton"]` |
| База знаний: поиск | `input[placeholder="Введите тему или ключевое слово"]` |
| Инструкция: шаги | `a[href="/instruction/supplier#N"]` |
| Выбор типа учётной записи | `[class*="SelectAccountTypePageStyles__OptionsList"]` |
| Заявка на регистрацию компании | `[class*="OperableFormBlockStyles__FormBlocksContainer"]` |
| Создание КС: наименование | `input[placeholder="Введите наименование закупки"]` |

Ограничения, о которых стоит помнить:

- Страницы Keycloak (`/auth/…`) шлют `Content-Security-Policy: frame-src 'self'`
  — обычный iframe там не откроется; расширение вставляет фрейм своей
  страницы (`chat.html`), на которую CSP страницы не распространяется.
- Chrome (2026) блокирует обращения публичных https-страниц к localhost
  (`ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS`) — ещё одна причина вложенного
  фрейма расширения. Для `embed.js` на стороннем сайте это означает: сервер
  Валерчика должен быть на публичном https, а не на localhost.
- Разделы личного кабинета доступны только после входа; сценарии для них
  помечены `optional` и показывают текст шага даже без найденного элемента.
- Каталог СТЕ: `/newapi/api/Sku/QueryIndex`, `/newapi/api/SkuSearch/QueryAutocomplete`,
  `/classifier/api/Sku/GetSku` — см. `zakupki-mcp/portal.py`.

## Как обновить карту

1. `curl https://zakupki.mos.ru/` → имя `main.<hash>.js`, скачать, декодировать `\uXXXX`.
2. Маршруты: регулярка выше; ленивые чанки страниц — карта `.u=t=>t+"."+{…}[t]+".js"`.
3. Живой DOM: Playwright (`frontend/node_modules/playwright`, канал `msedge`)
   открывает страницы и собирает ссылки, кнопки, поля, `data-testid` и имена
   styled-компонентов — так были проверены все селекторы из таблицы.
4. Проверка сценариев на живом портале: `node extension/scripts/e2e.mjs`.
