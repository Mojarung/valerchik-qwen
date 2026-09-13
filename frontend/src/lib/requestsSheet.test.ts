import { afterEach, describe, expect, it, vi } from "vitest";

const KEY = "valerchik-requests-panel";

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe("useRequestsSheet: боковая панель", () => {
  it("по умолчанию свёрнута, развёрнутая запоминается и переживает перезагрузку", async () => {
    const first = (await import("./requestsSheet")).useRequestsSheet;
    expect(first.getState().panelCollapsed).toBe(true);

    first.getState().setPanelCollapsed(false);
    expect(localStorage.getItem(KEY)).toBe("expanded");

    vi.resetModules();
    const reloaded = (await import("./requestsSheet")).useRequestsSheet;
    expect(reloaded.getState().panelCollapsed).toBe(false);

    reloaded.getState().setPanelCollapsed(true);
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it("без хранилища (приватный режим) панель работает, выбор живёт до перезагрузки", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });
    const sheet = (await import("./requestsSheet")).useRequestsSheet;

    expect(sheet.getState().panelCollapsed).toBe(true);
    expect(() => sheet.getState().setPanelCollapsed(false)).not.toThrow();
    expect(sheet.getState().panelCollapsed).toBe(false);
  });
});
