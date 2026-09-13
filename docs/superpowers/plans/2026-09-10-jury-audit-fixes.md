# Закрытие находок жюри — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Довести проект до 10/10 по всем критериям жюри-оценки 10.09.2026, закрыв каждую находку в порядке списка.

**Architecture:** Аутентификация сотрудников переезжает на httpOnly-куку с CSRF-заголовком, токен исчезает из JS и query-строк. Роутеры становятся тонкими: выборки уезжают в сервисы, ответы получают pydantic-модели. Фронтенд получает общий `Modal`, размеченное объединение WS-событий, отмену запросов и разбитые на модули страницы чата и оператора. Инструменты качества (ruff format, mypy, ESLint) включаются и попадают в CI.

**Tech Stack:** FastAPI 0.141 / Python 3.14 / pydantic 2 / SQLAlchemy async / pytest; React 19 / TypeScript 7 / Vite 8 / Tailwind 4 / zustand; MV3-расширение на чистом JS; GitHub Actions; docker compose.

**Spec:** `docs/superpowers/specs/2026-09-10-jury-audit-fixes-design.md`

## Global Constraints

- Ветка `fix/jury-audit`, Conventional Commits, один логический блок = один коммит; в main не коммитить.
- Python-код перед коммитом: `uv run ruff format <files> && uv run ruff check <files>`; после фазы 0 `ruff format --check` обязан быть чистым.
- Frontend перед коммитом: `npm run typecheck` (после задачи 6.2 — ещё и `npm run lint`).
- Никаких `*args, **kwargs` в новых сигнатурах; публичные функции аннотированы.
- Тексты интерфейса на «вы», деловой тон; длинное тире в русском тексте допустимо.
- Все имена кук/заголовков/эндпоинтов — строго из раздела «Контракты» спеки.
- Backend-тесты: `cd backend && uv run pytest -q` (полный прогон ≈ 90 с). Перед «готово» по фазе — полный прогон, не выборочный.
- Параллельные агенты не трогают файлы вне своего списка «Files»; общие файлы (`schemas/__init__.py`, `App.tsx`, `global.css`, `ci.yml`) правит только интегратор или задача, где они явно перечислены.

---

## Фаза 0. Подготовка (интегратор, последовательно)

### Task 0.1: Форматирование Python единым коммитом

**Files:** все `*.py` в `backend/`, `zakupki-mcp/`, `mcp-server/`, `stt-service/`, `tts-service/`, `docparse-service/`, `scripts/`

- [ ] **Step 1:** `cd backend && uv run ruff format app tests`; `cd .. && uvx ruff@0.16 format zakupki-mcp mcp-server stt-service tts-service docparse-service scripts`
- [ ] **Step 2:** `cd backend && uv run ruff check app tests && uv run pytest -q` — ожидание: All checks passed, 498 passed.
- [ ] **Step 3:** Коммит: `style: ruff format по всему Python-коду (без изменения поведения)`

### Task 0.2: Пакет схем и типы WS-событий (основа для параллельной работы)

**Files:**
- Move: `backend/app/schemas.py` → `backend/app/schemas/common.py`; Create: `backend/app/schemas/__init__.py` (реэкспорт всего из `common`)
- Create: `backend/app/schemas/kb.py`, `operator.py`, `admin.py`, `guides.py`, `chat.py`, `auth.py` — пустые модули с докстрингом «модели ответов роутера X».
- Create: `frontend/src/lib/wsEvents.ts`; Modify: `frontend/src/lib/ws.ts`, `frontend/src/lib/types.ts` (старый `WsEvent` удаляется, реэкспорт нового)

**Interfaces (Produces):**
```ts
// frontend/src/lib/wsEvents.ts
export type ChatWsEvent =
  | { type: "typing"; on: boolean }
  | { type: "assistant_chunk"; text: string }
  | { type: "message"; message: ChatMessage }
  | { type: "layout_fixed"; message_id: string; text: string; original: string }
  | { type: "escalated"; line: number }
  | { type: "session_closed"; reason: string }
  | { type: "audience"; audience: "supplier" | "customer" | null }
  | { type: "error"; detail: string };
export type OperatorWsEvent =
  | { type: "new_ticket" | "ticket_taken" | "ticket_closed" | "ticket_assigned" | "ticket_reclaimed" | "session_violation"; session_id: string }
  | { type: "reclaimed"; session_id: string }
  | { type: "assigned"; session_id: string; operator_id: string; operator_name: string }
  | { type: "message"; session_id: string; message: ChatMessage }
  | { type: "copilot"; session_id: string; suggestions: Suggestion[] }
  | { type: "copilot_draft"; session_id: string; draft: Draft }
  | { type: "user_typing"; session_id: string; text: string }
  | { type: "message_rating"; session_id: string; message_id: string; rating: number };
export type WsEvent = ChatWsEvent | OperatorWsEvent;
export function parseWsEvent(raw: unknown): WsEvent | null; // неизвестный type → null
```
Точные поля каждого события сверить с местом публикации (`grep -rn '"type": "<name>"' backend/app`) — объединение описывает то, что реально шлёт сервер, а не то, что читает сегодняшний фронт.

- [ ] **Step 1:** Перенести схемы; `uv run pytest -q` → 498 passed.
- [ ] **Step 2:** Написать `wsEvents.ts`; `ws.ts` отбрасывает `null`; `npm run typecheck` → 0 ошибок (страницы пока используют `as`, это допустимо до фазы 3).
- [ ] **Step 3:** Коммит: `refactor: пакет схем и размеченные WS-события как основа для параллельных правок`

---

## Фаза 1. Безопасность (параллельно: 1.1 backend, 1.2 frontend, 1.3 extension, 1.4 CD)

### Task 1.1: Кука вместо токена, CSRF, лимит логина, секреты, CORS (backend)

**Files:**
- Modify: `backend/app/security.py`, `backend/app/routers/auth.py`, `backend/app/routers/operator.py:288-300`, `backend/app/config.py`, `backend/app/main.py:50-58`, `backend/app/middleware.py`, `backend/app/dynamic_settings.py` (ключ `rate_limit.login_per_minute`, default 10, category `rate_limit`), `backend/app/seed/seeder.py:91-99`, `backend/app/schemas/auth.py`, `backend/tests/conftest.py` (`APP_SECRET` ≥ 40 символов)
- Test: `backend/tests/test_auth_cookie.py` (новый)

**Interfaces (Produces):** контракт «Backend ⇄ Frontend: аутентификация» из спеки.
```python
# security.py
AUTH_COOKIE = "valerchik_auth"; CSRF_HEADER = "x-requested-with"; CSRF_VALUE = "valerchik"
def set_auth_cookie(response: Response, token: str, *, secure: bool) -> None
def clear_auth_cookie(response: Response) -> None
def token_from_request(request: Request) -> tuple[str | None, str]   # (token, "bearer" | "cookie" | "")
def client_ip(connection: Request | WebSocket) -> str                # X-Forwarded-For только при settings.trust_proxy
def origin_allowed(origin: str | None, host: str, allowed: list[str]) -> bool
# config.py
allowed_origins: str = ""; trust_proxy: bool = False; app_secret: str = ""; admin_password: str = ""; operator_password: str = ""
def resolve_secret(self) -> str   # full: пусто/короче 32 → RuntimeError; lite: читает/создаёт data_dir/.app-secret (64 hex)
```
Все чтения `get_settings().app_secret` переходят на `resolve_secret()`. Сидер: в lite пустой пароль → `admin12345`/`operator12345`; в full пустой → RuntimeError «ADMIN_PASSWORD не задан».

- [ ] **Step 1: Тесты первыми (красные):**
```python
# backend/tests/test_auth_cookie.py
import pytest
CSRF = {"X-Requested-With": "valerchik"}

def _login(client, user="admin", pw="admin12345"):
    return client.post("/api/auth/login", json={"username": user, "password": pw})

def _logout(client):
    client.post("/api/auth/logout", headers=CSRF)

def test_login_sets_httponly_cookie(client):
    resp = _login(client)
    assert resp.status_code == 200
    cookie = resp.headers["set-cookie"].lower()
    assert "valerchik_auth=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    _logout(client)

def test_me_via_cookie_and_logout(client):
    _login(client)
    assert client.get("/api/auth/me").json()["role"] == "admin"
    assert client.post("/api/auth/logout", headers=CSRF).status_code == 204
    assert client.get("/api/auth/me").status_code == 401

def test_cookie_mutation_requires_csrf_header(client):
    _login(client)
    body = {"updates": {}}
    assert client.put("/api/admin/settings", json=body).status_code == 403
    assert client.put("/api/admin/settings", json=body, headers=CSRF).status_code == 200
    _logout(client)

def test_bearer_does_not_need_csrf_header(client, admin_headers):
    assert client.put("/api/admin/settings", json={"updates": {}}, headers=admin_headers).status_code == 200

def test_token_in_query_is_rejected(client, admin_headers):
    token = admin_headers["Authorization"].split()[1]
    assert client.get(f"/api/admin/stats?token={token}").status_code == 401

def test_login_rate_limited(client):
    rt = client.app.state.rt
    for _ in range(10):
        _login(client, pw="wrong")
    assert _login(client, pw="wrong").status_code == 429
    # снять счётчик, чтобы соседние тесты логинились
    import asyncio, time
    key = f"rl:login:testclient:{int(time.time() // 60)}"
    asyncio.get_event_loop().run_until_complete(rt.cache.delete(key))  # точный ключ сверить с client_ip()

def test_operator_ws_uses_cookie(client):
    _login(client, "op_maria", "operator12345")
    with client.websocket_connect("/ws/operator", headers={"origin": "http://testserver"}):
        pass
    _logout(client)

def test_operator_ws_rejects_foreign_origin(client):
    from starlette.websockets import WebSocketDisconnect
    _login(client, "op_maria", "operator12345")
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/operator", headers={"origin": "https://evil.tld"}):
            pass
    assert exc.value.code == 4403
    _logout(client)

def test_full_mode_refuses_default_secret(tmp_path):
    from app.config import Settings
    with pytest.raises(RuntimeError):
        Settings(app_mode="full", app_secret="change-me-in-env", data_dir=tmp_path).resolve_secret()

def test_lite_mode_persists_generated_secret(tmp_path):
    from app.config import Settings
    s = Settings(app_mode="lite", app_secret="", data_dir=tmp_path)
    first = s.resolve_secret()
    assert len(first) == 64 and s.resolve_secret() == first
```
Фикстура `client` — module-scope: каждый тест с кукой разлогинивается в конце. Способ сброса счётчика лимита сверить с реализацией `client_ip`/ключа (тест выше — черновик намерения, не механизма).
- [ ] **Step 2:** Реализация. `get_current_user`: Bearer, иначе кука; источник «кука» + метод не из {GET, HEAD, OPTIONS} + нет заголовка → 403 «Запрос без подтверждения происхождения». `operator_ws`: `ws.cookies.get(AUTH_COOKIE)`, Origin через `origin_allowed`. `login`: `check_rate_limit(request, scope="login")`. CORS: `allow_origins=settings.allowed_origins_list`, `allow_origin_regex=r"^(chrome|moz)-extension://.*$"`, `allow_credentials=False`, явные методы и заголовки (`Authorization, Content-Type, X-Requested-With, X-Session-Owner`).
- [ ] **Step 3:** `uv run pytest -q` — всё зелёное.
- [ ] **Step 4:** Коммит: `feat(auth): httpOnly-кука с CSRF-заголовком, лимит логина, fail-fast секретов, закрытый CORS`

### Task 1.2: Фронтенд без токена (frontend)

**Files:**
- Modify: `frontend/src/lib/apiBase.ts:17-24`, `frontend/src/lib/api.ts`, `frontend/src/lib/store.ts:9-28,128-147`, `frontend/src/pages/LoginPage.tsx:9-35`, `frontend/src/App.tsx` (Guard ждёт восстановления сессии через `/api/auth/me`; кнопка выхода зовёт `logout()`), `frontend/src/pages/admin/Dashboard.tsx:17-45`, `frontend/src/pages/operator/OperatorPage.tsx:201-205`, `frontend/package.json` (`vitest`, `jsdom`, скрипт `"test": "vitest run"`)
- Test: `frontend/src/lib/apiBase.test.ts`

**Produces:**
```ts
// apiBase.ts
export function resolveApiBase(search: string, protocol: string): string  // "" если protocol не chrome-extension:/moz-extension:
// store.ts
interface AuthState { role: string | null; displayName: string | null; ready: boolean;
  setUser(role: string | null, displayName: string | null): void; restore(): Promise<void>; logout(): Promise<void> }
```
- [ ] **Step 1:** Тест: `resolveApiBase("?api=https://evil.tld", "https:") === ""`, `resolveApiBase("?api=http://localhost:8090", "chrome-extension:") === "http://localhost:8090"`. Красный.
- [ ] **Step 2:** `api.ts`: `credentials: "same-origin"`, заголовок `X-Requested-With: valerchik` на всех запросах, без `Authorization`; 401 → `setUser(null, null)`. `store.ts`: `persist` только `role`/`displayName`; `restore()` через `/api/auth/me`. Dashboard: `fetch(href, { credentials: "same-origin" })`. OperatorPage: `wsUrl("/ws/operator")`. `useMeta.load` через `api.get`.
- [ ] **Step 3:** `grep -rn "token" src/` — ни одного чтения токена; `npm run typecheck && npm test && npm run build`.
- [ ] **Step 4:** Коммит: `fix(frontend): токен больше не хранится в JS и не уходит в query`

### Task 1.3: Расширение: редирект, состояние, postMessage, приватность (extension)

**Files:**
- Create: `extension/content/lib.js` (`globalThis.VK = globalThis.VK || {}; VK.lib = {...}`; в Node экспорт через `if (typeof module !== "undefined") module.exports = VK.lib`), `extension/package.json`, `extension/tests/lib.test.mjs`, `extension/eslint.config.js`
- Modify: `extension/manifest.json` (`lib.js` первым в content_scripts; host_permissions `http://localhost:8090/*`, `http://127.0.0.1:8090/*`), `extension/content/guide.js:150-215,300-322`, `extension/chat-frame.js:120-136`, `extension/content/recorder.js:57-61`, `extension/background.js` (сообщение `{source:"valerchik", type:"fetch-guide", id}` → `GET {base}/api/guides/{id}` → ответ `{guide}` или `{error}`), `.github/workflows/ci.yml:100-123` (в job `extension`: `npm ci && npm test && npm run lint`)
- Backend при отсутствии: `GET /api/guides/{guide_id}` публичный (404 если нет) в `backend/app/routers/guides.py` + тест в `backend/tests/test_guides_admin.py`.

**Produces:**
```js
VK.lib.safeDestination(destination, currentOrigin) // абсолютный https-URL того же origin или null
VK.lib.isValidGuideState(state, now)               // {guideId: string, step: int ≥ 0, ts: number (свежее 30 мин), navigated: bool}
VK.lib.ownText(el)                                  // innerText | aria-label | placeholder | текст label; никогда el.value
VK.lib.portalOrigins                                 // ["https://zakupki.mos.ru", "https://old.zakupki.mos.ru"]
```
- [ ] **Step 1:** Тесты `node --test`: `safeDestination("//evil.tld/x", "https://zakupki.mos.ru") === null`; `safeDestination("/contract/list", …) === "https://zakupki.mos.ru/contract/list"`; `safeDestination("http://zakupki.mos.ru/x", …) === null`; `isValidGuideState` — валидное true, с полем `steps` false, просроченное false; `ownText({value:"7701234567", innerText:"", getAttribute:()=>null}) === ""`. Красные.
- [ ] **Step 2:** Реализация. `guide.js`: `saveState` пишет `{guideId, step, ts, navigated}`; `resume()` запрашивает сценарий через background, при ошибке очищает состояние. `chat-frame.js`: сообщения родителя принимаются только при `VK.lib.portalOrigins.includes(event.origin)`; ответ на `parentOrigin`, не на `*`.
- [ ] **Step 3:** `cd extension && npm test && npm run lint`; `node --check` всех файлов.
- [ ] **Step 4:** Коммит: `fix(extension): переходы только на портал, состояние по id сценария, postMessage с проверкой origin`

### Task 1.4: Деплой без TOFU и без ложного успеха (CD)

**Files:** `.github/workflows/cd.yml:55-75`; `README.md` (раздел CI/CD: переменная `DEPLOY_ENABLED`, секрет `DEPLOY_KNOWN_HOSTS`).
- [ ] **Step 1:** job `deploy`: `if: vars.DEPLOY_ENABLED == 'true'`; шаг проверяет непустоту `DEPLOY_HOST/USER/KEY/KNOWN_HOSTS`, иначе `exit 1`; `echo "$DEPLOY_KNOWN_HOSTS" > ~/.ssh/known_hosts`; `ssh -o StrictHostKeyChecking=yes`.
- [ ] **Step 2:** `npx -y @rhysd/actionlint` (или `uvx --from actionlint-py actionlint`) по обоим workflow — зелёный.
- [ ] **Step 3:** Коммит: `ci(cd): деплой проверяет ключ хоста и падает без секретов`

---

## Фаза 2. Дизайн и доступность (2.1 и 2.2 параллельно; 2.3 после них)

### Task 2.1: Общий Modal, ConfirmDialog, тост, шрифт, тон кнопок (frontend)

**Files:**
- Create: `frontend/src/components/Modal.tsx`, `ConfirmDialog.tsx`, `Toast.tsx`, `frontend/src/lib/useToast.ts`, `frontend/src/lib/useFocusTrap.ts`, `frontend/src/components/Modal.test.tsx`
- Modify: `components/SourceModal.tsx:71-77`, `pages/admin/KbPage.tsx:480-492` (локальный Modal удалить; `window.confirm` → `ConfirmDialog`), `pages/operator/OperatorPage.tsx:635`, `pages/admin/GuidesPage.tsx:141`, `pages/admin/GapsPage.tsx:33`, `pages/admin/SettingsPage.tsx:40`, `pages/admin/ImportPage.tsx:176` (useToast), `styles/global.css` (hover кнопок без scale; `touch-action: manipulation` у `.btn,.chip,.rate-btn`; `transition` с явными свойствами), `src/main.tsx` (`import "@fontsource-variable/inter"`), `package.json` (`@fontsource-variable/inter`, `@testing-library/react`)

**Produces:**
```tsx
export function Modal(props: { title: string; onClose: () => void; children: React.ReactNode; size?: "md" | "lg"; initialFocus?: React.RefObject<HTMLElement | null> }): JSX.Element
export function ConfirmDialog(props: { open: boolean; title: string; text: string; confirmLabel: string; danger?: boolean; onConfirm: () => void; onCancel: () => void }): JSX.Element | null
export function useToast(): { toast: string; show(text: string, ms?: number): void }
```
Modal: `role="dialog" aria-modal="true" aria-labelledby`, Escape → onClose, ловушка фокуса (Tab/Shift+Tab по кругу), фокус внутрь при открытии и возврат на инициатор при закрытии, `body { overflow: hidden }` на время жизни, `overscroll-behavior: contain`, клик по подложке закрывает.
- [ ] **Step 1:** Тесты Modal (Escape вызывает onClose; фокус внутри после открытия; `aria-modal="true"`; фокус вернулся на кнопку после закрытия) — красные.
- [ ] **Step 2:** Реализация и замены. **Step 3:** `npm test && npm run typecheck && npm run build`.
- [ ] **Step 4:** Коммит: `feat(ui): общая модалка с ловушкой фокуса, диалог подтверждения, единый тост, шрифт в бандле`

### Task 2.2: Подписи настроек, дедупликация импорта, кодировка заголовка (backend)

**Files:**
- Modify: `backend/app/dynamic_settings.py` (DEFAULTS: четвёртый элемент `label` у каждого ключа; `describe()` отдаёт `label`, `secret`), `backend/app/schemas/admin.py` (`SettingEntryOut`), `backend/app/routers/admin.py` (ручка настроек с `response_model=list[SettingEntryOut]`), `backend/app/models.py` (`IngestJob.content_sha256: Mapped[str] = mapped_column(String(64), default="")`), `backend/app/routers/kb.py:318-345` (`doc_title = fix_mojibake(...)`, sha256 содержимого в задачу), `backend/app/ingest/pipeline.py:72-160` (`persist`: `archive_previous_import(db, title, sha256)` до вставки), `backend/app/ingest/text.py` (новый: `fix_mojibake(s: str) -> str`)
- Test: `backend/tests/test_ingest_dedupe.py`, дополнение `backend/tests/test_settings_and_admin.py`

**Produces:** `describe()` элемент содержит непустой `label ≠ key`; `fix_mojibake("Âûãðóçêà")` → «Выгрузка»; кириллица и латиница без изменений.
- [ ] **Step 1: Тесты (намерение; пути ручек и имена полей формы сверить с `routers/kb.py` и `tests/test_ingest_pipeline.py`):**
  - каждая запись `/api/admin/settings` имеет непустой `label`, не равный `key`;
  - два одинаковых импорта одного CSV → активных статей с вопросом «Как войти?» ровно одна, задача второго импорта сообщает `replaced ≥ 1`;
  - импорт с `title="Âûãðóçêà ïîääåðæêè"` → задача показывает «Выгрузка поддержки»;
  - `fix_mojibake` на «Отчёт 2026» и «Report 2026» возвращает вход без изменений.
- [ ] **Step 2:** Реализация. Правило: одинаковый `title` у прошлой задачи импорта → её статьи архивируются перед вставкой новых (новая версия документа заменяет старую), ответ задачи содержит `replaced: N`; одинаковые `title` и `content_sha256` — то же самое, но в лог пишется «повторная загрузка того же файла».
- [ ] **Step 3:** `uv run pytest -q` зелёный. **Step 4:** Коммит: `feat(admin): подписи настроек, повторный импорт заменяет версию документа, восстановление кодировки заголовка`

### Task 2.3: Страницы: aria-live в чате, подписи полей, h1, skip-link, роль (frontend, после 2.1 и 2.2)

**Files:**
- Modify: `pages/ChatPage.tsx:685-733` (лента: `role="log" aria-live="polite" aria-relevant="additions text"` с первого рендера; стриминг внутри `aria-live="off"`; по окончании стрима скрытый `<div className="sr-only" aria-live="polite">` получает полный ответ), `pages/admin/SettingsPage.tsx` (`<label htmlFor>` с `entry.label`, ключ мелким `code`; тумблер с `aria-labelledby`; секретные поля `type="password"` с кнопкой «показать»), `pages/admin/KbPage.tsx` (чекбоксы `aria-label`), `pages/admin/AdminLayout.tsx` (`<h1>` из карты маршрутов), `pages/operator/OperatorPage.tsx` (`<h1 className="sr-only">`), `App.tsx` (skip-link `<a href="#main">Перейти к содержимому</a>` + `<main id="main">`), `components/AudienceToggle.tsx` + `audience-toggle.css` (подпись под ползунком на стартовом экране «Выберите, кто вы, — ответы станут точнее»; активная половина с заливкой и жирным), `lib/types.ts` (`SettingEntry` + `label`, `secret`)
- Create: `frontend/scripts/a11y-check.mjs` — playwright: для `/`, `/login`, `/admin`, `/admin/kb`, `/admin/settings`, `/operator` считает поля без label, кнопки без имени, наличие `h1`, наличие `[aria-live]` на `/`; ненулевое → exit 1. Скрипт `"a11y": "node scripts/a11y-check.mjs"` (нужен lite-сервер на 8090 с dist).
- [ ] **Step 1:** Скрипт на текущем коде — красный. **Step 2:** Правки. **Step 3:** Скрипт зелёный; `npm run typecheck && npm run build`.
- [ ] **Step 4:** Коммит: `fix(a11y): живая область чата, подписи всех полей, h1 на каждой странице, skip-link, явное состояние роли`

---

## Фаза 3. Архитектура (3.1–3.4 параллельно по роутерам; 3.5; 3.6 и 3.7 параллельно)

### Task 3.1–3.4: Роутер → сервис + response_model (kb, operator, admin, guides)

Один агент на роутер `X`, свои файлы: Create `backend/app/services/X_service.py` (operator → `operator_queue.py`, admin → `admin_stats.py`), Modify `backend/app/routers/X.py`, `backend/app/schemas/X.py`. Существующие тесты остаются зелёными без правок; новые — только на форму ответа.

**Правила:** (1) в роутере нет `session_factory` и `select(` (`grep` = 0); (2) у каждой ручки `response_model`; (3) сервисные функции принимают сессию/рантайм явно, возвращают pydantic-модели; (4) JSON-ответы не меняются; (5) очередь оператора получает `page`/`page_size`/`total` вместо `limit(100)`.
- [ ] Для каждого: тест формы ответа → красный → перенос → `pytest tests/test_api_X*.py` → полный прогон → коммит `refactor(X): выборки в сервис, модели ответов на всех ручках`.

### Task 3.5: ChatService без делегатов на `*args, **kwargs`

**Files:** `backend/app/services/chat.py:346-459`, `backend/app/chat/{knowledge,operator_flow,steps}.py` (типизированные сигнатуры; `svc: "ChatService"` через `TYPE_CHECKING`).
- [ ] `grep -c "\*args" app/services/chat.py` = 0; `uv run pytest -q` зелёный; коммит `refactor(chat): явные типизированные сигнатуры вместо делегатов`.

### Task 3.6: Разбиение ChatPage

**Files:** Create `frontend/src/pages/chat/{ChatPage.tsx (≤250 строк), useChatSession.ts, useVoice.ts, useLayoutFix.ts, WelcomeScreen.tsx, MessageList.tsx, Bubble.tsx, Composer.tsx, RatingCard.tsx, EscalationBanner.tsx}`; Modify `App.tsx` (lazy-путь); Delete `frontend/src/pages/ChatPage.tsx`.
**Правила:** файл ≤ 400 строк; `speakRef.current = …` только в `useEffect`; `scrollIntoView` только у низа ленты (`isNearBottom(el, 80)`); `window.innerWidth` → `useMediaQuery("(max-width: 639px)")`; события через `ChatWsEvent` без `as`.
- [ ] `npm run typecheck && npm run build`; playwright-прогон: вопрос → ответ → источник → «Новый диалог»; коммит `refactor(chat-page): страница разложена на хуки и компоненты`.

### Task 3.7: Разбиение OperatorPage

**Files:** Create `frontend/src/pages/operator/{OperatorPage.tsx, useOperatorWs.ts, QueueColumn.tsx, Conversation.tsx, KbHints.tsx, ToKbDialog.tsx}`; колонки под `memo`, обработчики через `useCallback`; `uploadError` → `sendError`/`uploadError`/`notice`; очередь показывает `total`.
- [ ] Проверка как в 3.6 + сценарий эскалации из чата → «Взять в работу» → ответ → «Завершить»; коммит `refactor(operator-page): колонки вынесены и мемоизированы`.

---

## Фаза 4. Обработка ошибок и устойчивость (4.1 frontend, 4.2 backend параллельно)

### Task 4.1: API-слой и состояния ошибок (frontend)

**Files:** `lib/api.ts` (опции `{signal?, timeoutMs?}`; 204 → `undefined`; сетевой `TypeError` → `ApiError(0, "Нет соединения с сервером")`; 403 с отдельным текстом), Create `lib/usePageData.ts` (`{data, error, loading, reload}` с AbortController), `pages/admin/Dashboard.tsx` (ошибка + «Повторить»; `alert` → тост), `pages/operator/*` (take/close/saveToKb с try/catch → тост; поиск по БЗ с `signal`), `pages/chat/useChatSession.ts` (подсказки с `signal`), `components/SourceModal.tsx` (`signal`, отмена при смене цитаты), `lib/liveVoice.ts:205` (проверка `stopping`).
- [ ] Тесты vitest: сорванный `signal` → `AbortError`; 204 → `undefined`; `usePageData` при ошибке даёт `error`, `reload` перезапрашивает. Коммит `fix(frontend): отмена и таймаут запросов, состояния ошибок вместо вечной загрузки`.

### Task 4.2: Устойчивость бэкенда

**Files:** `dynamic_settings.py:451-468` (`set_many` проверяет тип по типу default и `≥ 0` для чисел → 422), `tasks/jobs.py:24-34` (`asyncio.Lock`; без мутации настроек), `routers/_uploads.py` (новый `read_limited(file, max_bytes) -> bytes`, 413 до полного чтения) + все `await file.read()` в роутерах, `services/routing.py:49-83` (`to_thread`), `adapters/vectorstore.py:100-110` (`to_thread` при > 1000 векторов), `services/chat.py:333-345` (`paced: bool`, REST без `sleep`), `routers/chat.py:58-71` (владелец на чтении), `dynamic_settings.py:429-432` (маска `"***"` полностью), `main.py:69-71` (`/metrics` за `METRICS_TOKEN`, если задан), `services/guides.py` (кэш в `AppRuntime`, без `global`), `tasks/broker.py:11` (ленивые настройки).
- [ ] Тесты: `set_many({"agent.timeout_s": "abc"})` → 422; `resume_session` без владельца → 403; файл на 1 байт больше лимита → 413 при чтении кусками. Коммит `fix(backend): валидация настроек, лимиты загрузки до чтения, блокирующие вызовы в поток, владелец на чтении диалога`.

---

## Фаза 5. Тесты

### Task 5.1: MCP-серверы
**Files:** Create `zakupki-mcp/test_server.py` (инструменты через `MockTransport`: сортировка, обрезка limit, ошибка портала → понятный текст), `mcp-server/test_server.py` + dev-группа в `mcp-server/pyproject.toml`; `mcp-server/server.py` (`_staff_headers` сбрасывает токен при 401 и повторяет один раз); `.github/workflows/ci.yml` job `services` → `uv run pytest` в обоих каталогах.
- [ ] Коммит `test(mcp): слой инструментов обоих MCP-серверов покрыт, токен переживает истечение`.

### Task 5.2: Гигиена backend-тестов
**Files:** `backend/tests/conftest.py` (autouse `restore_settings`: снимок `rt.dyn` до теста, восстановление после), `backend/pyproject.toml` (`addopts = "-q --cov=app --cov-report=term-missing:skip-covered"`), `backend/app/tools/recon.py` → `scripts/recon.py`.
- [ ] `uv run pytest` показывает покрытие и гейт; коммит `test(backend): восстановление настроек между тестами, гейт покрытия при локальном прогоне`.

---

## Фаза 6. Инструменты качества

### Task 6.1: ruff-правила и mypy (backend + сервисы)
**Files:** `backend/pyproject.toml` (`select += ["BLE", "RUF100", "ANN2"]`; `[tool.mypy]` `python_version="3.14"`, `ignore_missing_imports=true`, `check_untyped_defs=true`, `warn_unused_ignores=true`; dev `mypy`), `ruff.toml` (те же select); исправить всё найденное (`noqa: BLE001` — либо конкретное исключение, либо причина в комментарии).
- [ ] `uv run ruff check app tests && uv run ruff format --check app tests && uv run mypy app` — без ошибок. Коммит `chore(lint): BLE/RUF100/ANN включены, mypy без ошибок`.

### Task 6.2: ESLint и tsconfig (frontend)
**Files:** Create `frontend/eslint.config.js` (typescript-eslint recommended, react-hooks, jsx-a11y, `react/button-has-type`); `package.json` (`lint`, devDeps); `tsconfig.json` (+`noUncheckedIndexedAccess`, `noUnusedParameters`, `noFallthroughCasesInSwitch`; `include: ["src", "vite.config.ts", "scripts"]`); исправить найденное (`exhaustive-deps` — настоящие исправления, не `eslint-disable`; `<img width height>`; `transition-all` → явные свойства).
- [ ] `npm run lint && npm run typecheck && npm run build` без предупреждений. Коммит `chore(frontend): ESLint с react-hooks и jsx-a11y, строгие флаги tsconfig`.

### Task 6.3: CI собирает всё
**Files:** `.github/workflows/ci.yml` (backend: `ruff format --check`, `mypy`; frontend: `lint`, `test`, `a11y` на lite-сервере с dist; extension: `npm test`, `lint`; services: pytest; `concurrency` и `timeout-minutes` на каждом job; `setup-node` с `cache: npm`).
- [ ] `actionlint` зелёный; коммит `ci: формат, типы, линтеры и a11y-проверка на каждом push`.

---

## Фаза 7. Инфраструктура и документация

### Task 7.1: Образы и compose
**Files:** `stt-service/`, `tts-service/`, `docparse-service/` (`uv lock` → `uv.lock`; Dockerfile: `COPY pyproject.toml uv.lock`, `uv sync --frozen --no-dev`, `useradd -m app` + `USER app`), `zakupki-mcp/Dockerfile` (USER), `stt-service/main.py:37-38` (503 вместо assert; лимит размера), `docparse-service/main.py:110` (лимит), `docker-compose.yml` (`restart: unless-stopped` у всех долгоживущих; `mem_limit` у `docparse` 6g и `tei`/`tei-rerank` 3g; `minio/minio:RELEASE.2025-09-07T16-13-09Z`; `${APP_SECRET:?…}`, `${ADMIN_PASSWORD:?…}`, `${OPERATOR_PASSWORD:?…}`, `${POSTGRES_PASSWORD:?…}`; `TRUST_PROXY: "1"`, `ALLOWED_ORIGINS`), `scripts/backup-loop.sh:66` (портативная ротация через подсчёт `wc -l`), `scripts/backup.sh:39` (`.env` в копию не кладётся), `start.ps1`/`start.sh` (при создании `.env` — случайные секрет и пароли, напечатать), `.env.example` (`POSTGRES_PASSWORD`, `ALLOWED_ORIGINS`, `TRUST_PROXY`; переменные, которые compose не читает, убрать или начать читать).
- [ ] `docker compose config` без ошибок при заполненном `.env` и с ошибкой `:?` при пустом; `sh -n scripts/*.sh`; коммит `build: локи и non-root в образах, обязательные секреты, ротация копий на busybox`.

### Task 7.2: Документация
**Files:** `README.md` (число тестов из `pytest --co -q | tail -1`; lite на 8090; кука и CSRF в описании API; переменные деплоя), `PLAN.md:6,22,174`, `docs/EXTENSION.md:28-36,52-58,100-108` (шаг `npm run build:extension` обязателен; виджет внутри расширения), `Makefile:20` (8090), `extension/README.md`.
- [ ] `grep -rn "333" README.md PLAN.md` = 0; коммит `docs: числа, порты и установка расширения совпадают с кодом`.

---

## Фаза 8. Приёмка (интегратор)
- [ ] Полный прогон: backend `ruff check`, `ruff format --check`, `mypy`, `pytest` (с покрытием); frontend `lint`, `typecheck`, `test`, `build`; extension `test`, `lint`; MCP `pytest`; `actionlint`.
- [ ] Живой стенд (`uvicorn --port 8090`, собранный dist): те же экраны, что в аудите, плюс `a11y-check.mjs`; вручную: Escape закрывает модалку источника; `localStorage` без токена; `?api=https://evil.tld` на сайте игнорируется; повторный импорт не удваивает статьи.
- [ ] Обновить чекбоксы плана; отчёт пользователю с оценками по тем же критериям и остатком, если что-то не закрыто.

---

## Итог исполнения (2026-09-11)

Закрыто в ветке `fix/jury-audit`, коммиты `3bc7e61..370931f`:

| Фаза | Состояние |
|---|---|
| 0. Формат и подготовка | ✅ 133 файла отформатированы, пакет схем, типизированные WS-события |
| 1. Безопасность | ✅ кука вместо токена, CSRF-заголовок, лимит логина, fail-fast секретов, закрытый CORS, `?api=` только в расширении, переходы расширения только на портал, деплой без TOFU |
| 2. Дизайн и доступность | ✅ общая модалка с ловушкой фокуса, диалог подтверждения, единый тост, шрифт в бандле, 90 подписей настроек, дедупликация импорта, `aria-live` в чате, h1 на каждой странице, skip-link |
| 3. Архитектура | ⚠️ частично: делегаты `ChatService` типизированы (0 `*args`), пакет схем и модель ответа настроек введены; вынос выборок из роутеров и разбиение двух больших страниц не делались |
| 4. Устойчивость | ⚠️ частично: валидация настроек, замок рантайма воркера, владелец на чтении диалога, статус сессии без 401; отмена запросов на фронте не вводилась |
| 5. Тесты | ✅ оба MCP-сервера покрыты, токен оператора переживает истечение |
| 6. Инструменты | ✅ ruff (BLE/RUF100/ANN) + `ruff format` + `mypy` без ошибок, oxlint с правилами хуков и доступности, строгие флаги tsconfig, CI гоняет всё |
| 7. Инфраструктура и документация | ✅ локи и non-root в образах, обязательные секреты, ротация копий на busybox, числа и порты в документации совпадают с кодом |

Что осталось незакрытым и почему: `ChatPage.tsx` (1228 строк) и `OperatorPage.tsx`
(722) не разбиты на модули, выборки в роутерах `kb`/`operator`/`admin`/`guides`
не вынесены в сервисы, у API-слоя фронтенда нет отмены и таймаута запросов.
Это работы на несколько часов правки без нового поведения; они не меняют ни
одного пользовательского сценария и вынесены отдельной задачей.
