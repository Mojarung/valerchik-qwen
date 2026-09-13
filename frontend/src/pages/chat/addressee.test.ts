import { describe, expect, it } from "vitest";
import { addresseeText, transferText } from "./addressee";

/** Кому адресовано обращение — словами, которые поймёт человек не из поддержки. */
describe("addressee", () => {
  const tech = { line: 2, lineLabel: "Техническая поддержка", externalLabel: "" };
  const skk = { line: 1, lineLabel: "Консультации", externalLabel: "Служба контроля качества портала (СКК)" };

  it("шапка: наша линия с названием или внешний адресат; без названия — хотя бы номер", () => {
    expect(addresseeText(tech)).toBe("Техническая поддержка");
    expect(addresseeText(skk)).toBe("Служба контроля качества портала (СКК)");
    expect(addresseeText({ line: 3, lineLabel: "", externalLabel: "" })).toBe("Поддержка");
  });

  it("плашка: куда передано и кто подключится к чату", () => {
    expect(transferText(tech))
      .toBe("Обращение передано в направление «Техническая поддержка». Специалист подключится к этому чату.");
    // внешний адресат в чат не придёт — человеку говорят, что пишет всё равно наш специалист
    expect(transferText(skk)).toBe("Этот вопрос рассматривает Служба контроля качества портала (СКК). "
      + "В чате появится инструкция, как туда обратиться.");
  });
});
