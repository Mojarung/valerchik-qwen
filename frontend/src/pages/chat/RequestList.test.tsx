import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RequestList, whenText } from "./RequestList";
import type { RequestCard } from "./useRequestHistory";

const card = (over: Partial<RequestCard>): RequestCard => ({
  session_id: "s1", owner: "o1", status: "active", line: null, title: "Что такое СТЕ?",
  created_at: "2026-09-13T08:00:00+00:00", updated_at: "2026-09-13T08:05:00+00:00", closed_at: null,
  last_message_preview: "", last_message_role: "assistant", line_label: "", addressee_label: "",
  unread: false, unread_count: 0, ...over,
});

const noop = () => undefined;

afterEach(cleanup);

describe("RequestList", () => {
  it("открытые отдельно от завершённых, статус и адресат словами", () => {
    render(<RequestList loading={false} error={null} currentId={null} onOpen={noop} onNew={noop} onRetry={noop}
      items={[
        card({ session_id: "a", status: "waiting_operator", line: 2, line_label: "Техническая поддержка",
               title: "Не уходит УПД" }),
        card({ session_id: "b", status: "resolved", title: "Как подписать контракт" }),
        card({ session_id: "c", status: "closed_violation", title: "Грубое обращение" }),
      ]} />);

    const open = screen.getByRole("heading", { name: "Открытые" }).closest("section")!;
    const done = screen.getByRole("heading", { name: "Завершённые" }).closest("section")!;
    expect(within(open).getByText("Не уходит УПД")).toBeTruthy();
    expect(within(open).getByText("Ждёт специалиста")).toBeTruthy();
    expect(within(open).getByText("Кому: Техническая поддержка")).toBeTruthy();
    expect(within(done).getByText("Завершено")).toBeTruthy();
    expect(within(done).getByText("Закрыто за нарушение правил")).toBeTruthy();
  });

  it("внешний адресат вместо линии, текущий диалог отмечен, новый ответ виден", () => {
    render(<RequestList loading={false} error={null} currentId="a" onOpen={noop} onNew={noop} onRetry={noop}
      items={[
        card({ session_id: "a", status: "operator_active", line: 1, line_label: "Консультации",
               addressee_label: "СКК", title: "Блокировка кабинета" }),
        card({ session_id: "b", status: "operator_active", unread: true, title: "Смена ответственного" }),
      ]} />);

    expect(screen.getByText("Кому: СКК")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Блокировка кабинета/ }).getAttribute("aria-current")).toBe("true");
    expect(within(screen.getByRole("button", { name: /Смена ответственного/ })).getByText("Новый ответ")).toBeTruthy();
  });

  it("клик по карточке открывает именно это обращение", async () => {
    const onOpen = vi.fn();
    const item = card({ session_id: "z", title: "Как создать оферту" });
    render(<RequestList loading={false} error={null} currentId={null} onOpen={onOpen} onNew={noop} onRetry={noop}
      items={[item]} />);

    await userEvent.click(screen.getByRole("button", { name: /Как создать оферту/ }));
    expect(onOpen).toHaveBeenCalledWith(item);
  });

  it("пустой список объясняет, что здесь будет; ошибка предлагает повторить", async () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      <RequestList items={[]} loading={false} error={null} currentId={null} onOpen={noop} onNew={noop} onRetry={onRetry} />);
    expect(screen.getByText(/Здесь появятся ваши обращения/)).toBeTruthy();

    rerender(<RequestList items={[]} loading={false} error="Не удалось загрузить обращения" currentId={null}
      onOpen={noop} onNew={noop} onRetry={onRetry} />);
    await userEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Повторить" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("пока грузится первый раз — заготовки карточек, а не пустое состояние", () => {
    render(<RequestList items={[]} loading error={null} currentId={null} onOpen={noop} onNew={noop} onRetry={noop} />);
    expect(screen.getByRole("status").textContent).toContain("Загружаем обращения");
    expect(screen.queryByText(/Здесь появятся/)).toBeNull();
  });
});

describe("whenText", () => {
  const now = new Date(2026, 8, 13, 15, 0);
  it("сегодня — время, вчера — словом, раньше — дата", () => {
    expect(whenText(new Date(2026, 8, 13, 9, 7).toISOString(), now)).toBe("09:07");
    expect(whenText(new Date(2026, 8, 12, 23, 0).toISOString(), now)).toBe("вчера");
    expect(whenText(new Date(2026, 8, 1, 12, 0).toISOString(), now)).toMatch(/^1 сент/);
    expect(whenText(new Date(2025, 11, 30, 12, 0).toISOString(), now)).toBe("30.12.2025");
    expect(whenText("не дата", now)).toBe("");
  });
});

describe("RequestList: имя карточки для скринридера", () => {
  it("читается фразами, а не склеенной разметкой", () => {
    render(<RequestList loading={false} error={null} currentId={null} onOpen={noop} onNew={noop} onRetry={noop}
      items={[card({ status: "operator_active", line: 2, line_label: "Техническая поддержка", unread: true,
                     title: "Не уходит УПД", updated_at: "не дата" })]} />);
    expect(screen.getByRole("button", { name: /^Не уходит УПД\./ }).getAttribute("aria-label"))
      .toBe("Не уходит УПД. У специалиста. Кому: Техническая поддержка. Новый ответ");
  });
});
