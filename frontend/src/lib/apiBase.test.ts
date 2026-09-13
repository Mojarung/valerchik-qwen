import { describe, expect, it } from "vitest";
import { resolveApiBase } from "@/lib/apiBase";

/**
 * `?api=` — это указание, куда слать запросы, то есть выбор сервера чужой
 * строкой адреса. Внутри расширения так и задумано: интерфейс лежит на
 * `chrome-extension://…`, а сервер — на localhost. На обычном сайте та же
 * возможность превращается в фишинг: ссылка вида `/?api=https://evil.tld`
 * оставляет наш домен в адресной строке, а переписку и пароли уводит чужому
 * серверу. Поэтому параметр читается только под протоколом расширения.
 */
describe("resolveApiBase", () => {
  it("на обычном сайте игнорирует ?api= и оставляет запросы на своём origin", () => {
    expect(resolveApiBase("?api=https://evil.tld", "https:")).toBe("");
    expect(resolveApiBase("?api=http://localhost:8090", "http:")).toBe("");
  });

  it("внутри расширения берёт сервер из ?api=", () => {
    expect(resolveApiBase("?api=http://localhost:8090", "chrome-extension:")).toBe(
      "http://localhost:8090",
    );
    expect(resolveApiBase("?api=http://localhost:8090", "moz-extension:")).toBe(
      "http://localhost:8090",
    );
  });

  it("берёт только origin и переживает мусор в параметре", () => {
    expect(resolveApiBase("?api=http://localhost:8090/api/meta?x=1", "chrome-extension:"))
      .toBe("http://localhost:8090");
    expect(resolveApiBase("?api=не-адрес", "chrome-extension:")).toBe("");
    expect(resolveApiBase("", "chrome-extension:")).toBe("");
  });
});
