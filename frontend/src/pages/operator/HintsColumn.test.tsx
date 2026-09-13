import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { casesOf, parseCases } from "./caseHints";
import type { Suggestion } from "./useConversation";
import { HintsColumn } from "./HintsColumn";

/**
 * Подсказки оператору: статьи базы знаний и похожие решённые обращения порознь.
 * Отчёт о чужом обращении («проверили заявку, направили заказчику») — не
 * готовый ответ, и в общей куче его легко отправить клиенту как есть.
 */

/** Статья — поля подсказки из базы знаний; вид «answer» — обычная статья, не чужое обращение. */
const ARTICLE: Suggestion = {
  article_id: "k1", question: "Как подписать контракт на портале?", answer: "Раздел «Контракты».",
  category: "Контракты", score: 0.82, kind: "answer",
};
/** Обращение — ровно те поля, что кладёт в `cases` copilot_flow._case_cards. */
const CASE_CARD = {
  article_id: "c1", question: "Не подписывается контракт", answer: "Проверили сертификат, направили инструкцию.",
  score: 0.6, category: "Контракты", line: 2, kind: "operator_case",
};
/** Событие copilot в той форме, в какой его публикует бэкенд (_push_copilot). */
const COPILOT_EVENT = {
  type: "copilot", suggestions: [ARTICLE], cases: [CASE_CARD], reply_to: "m2", draft_pending: true,
};

afterEach(cleanup);

describe("casesOf", () => {
  it("достаёт обращения из поля cases события copilot и помечает их видом", () => {
    expect(casesOf(COPILOT_EVENT)).toEqual([CASE_CARD]);
  });

  it("сервер без поля cases и кривые записи не роняют панель", () => {
    expect(casesOf({ suggestions: [ARTICLE] })).toEqual([]);
    expect(casesOf({ cases: null })).toEqual([]);
    expect(parseCases([
      { article_id: "c2", question: "Без ответа", score: 0.5 },
      "мусор",
      { ...CASE_CARD, line: undefined, category: undefined, kind: undefined },
    ])).toEqual([{ ...CASE_CARD, line: null, category: null }]);
  });
});

describe("HintsColumn", () => {
  it("событие copilot как есть: статьи — в подсказках, обращения — своей группой", async () => {
    const onInsert = vi.fn();
    render(<HintsColumn open onBack={vi.fn()} suggestions={COPILOT_EVENT.suggestions}
      cases={casesOf(COPILOT_EVENT)} onInsert={onInsert} />);

    await userEvent.click(screen.getByText("Похожие обращения (1)"));
    expect(screen.getByText(CASE_CARD.question)).toBeTruthy();
    expect(screen.getByText("Похожее обращение:")).toBeTruthy();
    // статья стоит в своей группе, без пометки обращения
    expect(screen.getByText("Подходит к вопросу")).toBeTruthy();
    expect(screen.getByText(ARTICLE.question)).toBeTruthy();
    expect(screen.getAllByText("Похожее обращение:")).toHaveLength(1);

    expect(screen.getAllByRole("button", { name: "Вставить в ответ" })).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Вставить в ответ" }));
    expect(onInsert).toHaveBeenCalledWith(ARTICLE.answer);
  });

  it("без похожих обращений подзаголовка нет", () => {
    render(<HintsColumn open onBack={vi.fn()} suggestions={[ARTICLE]} onInsert={vi.fn()} />);

    expect(screen.getByText("Подходит к вопросу")).toBeTruthy();
    expect(screen.queryByText(/Похожие обращения/)).toBeNull();
  });

  it("одни похожие обращения: подзаголовок статей не висит пустым, «пусто» не пишем", () => {
    render(<HintsColumn open onBack={vi.fn()} suggestions={[]} cases={casesOf(COPILOT_EVENT)} onInsert={vi.fn()} />);

    expect(screen.queryByText("Подходит к вопросу")).toBeNull();
    expect(screen.getByText("Похожие обращения (1)")).toBeTruthy();
    expect(screen.queryByText(/Откройте обращение/)).toBeNull();
  });
});
