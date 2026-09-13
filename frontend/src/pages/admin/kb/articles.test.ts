import { describe, expect, it } from "vitest";
import { audienceOf, plainPreview } from "./articles";

/** Превью ответа в таблице и роль статьи из значения списка. */
describe("статьи базы знаний", () => {
  it("превью ответа без разметки: картинки убраны, у ссылок — подпись", () => {
    const answer = "## Подписание\n**Откройте** [карточку контракта](https://portal/x) ![Рисунок 3](a.png)\n"
      + "> нажмите `Подписать`";
    expect(plainPreview(answer)).toBe("Подписание Откройте карточку контракта нажмите Подписать");
  });

  it("роль — поставщик или заказчик, всё прочее — для всех", () => {
    expect(audienceOf("supplier")).toBe("supplier");
    expect(audienceOf("customer")).toBe("customer");
    expect(audienceOf("")).toBe("all");
    expect(audienceOf("всем")).toBe("all");
  });
});
