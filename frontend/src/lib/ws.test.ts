import { afterEach, describe, expect, it, vi } from "vitest";
import { connectWs, ownerProtocols } from "./ws";

/**
 * Владение гостевым диалогом браузер доказывает подпротоколом сокета: адрес с
 * токеном оседал в логах прокси и в истории браузера.
 */
class FakeSocket {
  static last: FakeSocket | null = null;
  static OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  url: string;
  protocols: string | string[] | undefined;

  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = protocols;
    FakeSocket.last = this;
  }

  send() {}

  close() {}
}

afterEach(() => {
  vi.unstubAllGlobals();
  FakeSocket.last = null;
});

describe("connectWs", () => {
  it("передаёт подпротоколы сокету, а адрес оставляет без токена", () => {
    vi.stubGlobal("WebSocket", FakeSocket);

    const conn = connectWs("ws://host/ws/chat/abc", () => {}, undefined, ["valerchik.v1", "owner.t0ken"]);

    expect(FakeSocket.last?.url).toBe("ws://host/ws/chat/abc");
    expect(FakeSocket.last?.protocols).toEqual(["valerchik.v1", "owner.t0ken"]);
    conn.close();
  });

  it("подпротоколы гостевого чата — версия и токен владельца", () => {
    expect(ownerProtocols("t0ken")).toEqual(["valerchik.v1", "owner.t0ken"]);
  });
});
