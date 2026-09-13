import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * Договор между api.ts и хранилищем сессии: чем подтверждаются права, что
 * происходит при 401 и что переживает перезагрузку вкладки.
 *
 * Модули поднимаются заново на каждый случай (`vi.resetModules` + динамический
 * импорт): хранилище — синглтон с состоянием, и номер сессии, и запись в
 * localStorage переживают тест. Проверять миграцию persist иначе нельзя вовсе:
 * она случается один раз, в момент создания хранилища.
 */

// jsdom не реализует matchMedia, а хранилище на старте спрашивает системную
// тему. Заглушка нужна только чтобы модуль поднялся; тема здесь не проверяется.
// Обычным присваиванием, а не через vi.stubGlobal: она должна пережить
// vi.unstubAllGlobals() между случаями.
window.matchMedia = ((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener: () => {},
  removeListener: () => {},
  addEventListener: () => {},
  removeEventListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

/** Ответ сервера в том объёме, в каком его читает `request()`. */
function jsonResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: () => Promise.resolve(body),
  };
}

const ME_OK = { role: "admin", display_name: "Админ", user_id: 1, username: "admin" };
const UNAUTHORIZED = { detail: "Не авторизован" };

/** Свежие модули и, если надо, заранее подложенная запись в localStorage. */
async function freshSession(persisted?: unknown) {
  localStorage.clear();
  if (persisted !== undefined) localStorage.setItem("valerchik-auth", JSON.stringify(persisted));
  vi.resetModules();
  const store = await import("@/lib/store");
  const client = await import("@/lib/api");
  return { useAuth: store.useAuth, api: client.api, ApiError: client.ApiError };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("хранилище сессии", () => {
  it("миграция выбрасывает старый токен из записи valerchik-auth", async () => {
    const { useAuth } = await freshSession({
      state: { token: "jwt.секрет.подпись", role: "admin", displayName: "Админ" },
      version: 0,
    });

    const state = useAuth.getState() as unknown as Record<string, unknown>;
    expect(state.role).toBe("admin");
    expect(state.displayName).toBe("Админ");
    expect(state.token).toBeUndefined();
    // Роль из прошлой вкладки — только подсказка для меню: прав она не даёт,
    // пока сервер не ответил на /api/auth/me.
    expect(state.ready).toBe(false);

    const stored = JSON.parse(localStorage.getItem("valerchik-auth") ?? "{}") as {
      state: Record<string, unknown>;
      version: number;
    };
    expect(stored.version).toBe(1);
    expect(stored.state).not.toHaveProperty("token");
    expect(stored.state.role).toBe("admin");
  });

  it("вход во время висящего restore() не затирается опоздавшим 401", async () => {
    const { useAuth } = await freshSession();
    let answerMe: (resp: unknown) => void = () => {};
    const pendingMe = new Promise((resolve) => {
      answerMe = resolve;
    });
    vi.stubGlobal("fetch", vi.fn((input: string) =>
      String(input).includes("/api/auth/me") ? pendingMe : Promise.resolve(jsonResponse(200, {}))));

    const restoring = useAuth.getState().restore();
    // Человек пришёл прямо на /login и успел войти раньше, чем ответил /me.
    useAuth.getState().setUser("admin", "Админ");
    answerMe(jsonResponse(401, UNAUTHORIZED));
    await restoring;

    expect(useAuth.getState().role).toBe("admin");
    expect(useAuth.getState().displayName).toBe("Админ");
    expect(useAuth.getState().ready).toBe(true);
  });

  it("restore() при живой куке берёт роль с сервера", async () => {
    const { useAuth } = await freshSession();
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, ME_OK))));

    await useAuth.getState().restore();

    expect(useAuth.getState().role).toBe("admin");
    expect(useAuth.getState().displayName).toBe("Админ");
    expect(useAuth.getState().ready).toBe(true);
  });

  it("сломанный сервер отличим от гостя: 502 попадает в консоль, 401 — нет", async () => {
    const { useAuth } = await freshSession();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(401, UNAUTHORIZED))));
    await useAuth.getState().restore();
    expect(warn).not.toHaveBeenCalled();

    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(502, {}))));
    await useAuth.getState().restore();
    expect(warn).toHaveBeenCalledTimes(1);
    expect(useAuth.getState().role).toBeNull();
  });
});

describe("api-клиент", () => {
  it("каждый запрос несёт куку и подтверждение происхождения, но не Authorization", async () => {
    const { api } = await freshSession();
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(200, {})));
    vi.stubGlobal("fetch", fetchMock);

    await api.get("/api/meta");
    await api.post("/api/auth/logout");

    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const call of fetchMock.mock.calls) {
      const init = (call as unknown as [string, RequestInit])[1];
      const headers = init.headers as Record<string, string>;
      expect(init.credentials).toBe("same-origin");
      expect(headers["X-Requested-With"]).toBe("valerchik");
      expect(headers).not.toHaveProperty("Authorization");
    }
  });

  it("401 на обычном запросе сбрасывает сессию, но не разблокирует стража", async () => {
    const { useAuth, api, ApiError } = await freshSession();
    useAuth.getState().setUser("admin", "Админ");
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(401, UNAUTHORIZED))));

    await expect(api.get("/api/admin/stats")).rejects.toBeInstanceOf(ApiError);

    expect(useAuth.getState().role).toBeNull();
    expect(useAuth.getState().displayName).toBeNull();
    // `ready` остаётся за `restore()`: 401 на постороннем запросе не должен
    // выдавать стражу разрешение судить о правах.
    expect(useAuth.getState().ready).toBe(false);
  });

  it("204 не разбирается как JSON — выход из системы проходит успешно", async () => {
    const { useAuth, api } = await freshSession();
    vi.stubGlobal("fetch", vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 204,
        statusText: "",
        json: () => Promise.reject(new Error("тела нет")),
      })));

    useAuth.getState().setUser("admin", "Админ");
    await expect(api.post("/api/auth/logout")).resolves.toBeUndefined();
    await useAuth.getState().logout();

    expect(useAuth.getState().role).toBeNull();
  });
});
