import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RatingCard } from "./Rating";

/**
 * Форма отзыва после обращения: звёзды, два вопроса «да / нет» и комментарий.
 * Вопросы необязательны — пропущенный уходит как null, а не как «нет».
 */
afterEach(cleanup);

const setup = () => {
  const onRate = vi.fn(async () => undefined);
  render(<RatingCard onRate={onRate} />);
  return { onRate, user: userEvent.setup() };
};

const question = (name: string) => screen.getByRole("group", { name });
const option = (group: string, name: "Да" | "Нет") =>
  question(group).querySelector(`button:nth-child(${name === "Да" ? 1 : 2})`) as HTMLButtonElement;

describe("RatingCard", () => {
  it("вопросы появляются после звёзд", async () => {
    const { user } = setup();
    expect(screen.queryByRole("group", { name: "Проблема решена?" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "4 из 5" }));
    expect(question("Проблема решена?")).toBeTruthy();
    expect(question("Понятно объяснили?")).toBeTruthy();
  });

  it("отправляет оценку, комментарий и ответы на оба вопроса", async () => {
    const { onRate, user } = setup();
    await user.click(screen.getByRole("button", { name: "3 из 5" }));
    await user.click(option("Проблема решена?", "Да"));
    await user.click(option("Понятно объяснили?", "Нет"));
    await user.type(screen.getByRole("textbox", { name: "Комментарий" }), "Решили, но сложно");
    await user.click(screen.getByRole("button", { name: "Отправить оценку" }));
    expect(onRate).toHaveBeenCalledWith(3, "Решили, но сложно", { solved: true, clear: false });
  });

  it("пропущенные вопросы уходят как null", async () => {
    const { onRate, user } = setup();
    await user.click(screen.getByRole("button", { name: "5 из 5" }));
    await user.click(screen.getByRole("button", { name: "Отправить оценку" }));
    expect(onRate).toHaveBeenCalledWith(5, "", { solved: null, clear: null });
  });

  it("повторное нажатие снимает ответ, выбранный отмечен", async () => {
    const { onRate, user } = setup();
    await user.click(screen.getByRole("button", { name: "2 из 5" }));
    const no = option("Проблема решена?", "Нет");
    await user.click(no);
    expect(no.getAttribute("aria-pressed")).toBe("true");
    await user.click(no);
    expect(no.getAttribute("aria-pressed")).toBe("false");
    await user.click(screen.getByRole("button", { name: "Отправить оценку" }));
    expect(onRate).toHaveBeenCalledWith(2, "", { solved: null, clear: null });
  });

  it("«Не сейчас» есть, только когда карточку можно закрыть", async () => {
    const onDismiss = vi.fn();
    render(<RatingCard onRate={async () => undefined} onDismiss={onDismiss} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Не сейчас" }));
    expect(onDismiss).toHaveBeenCalled();
    cleanup();
    render(<RatingCard onRate={async () => undefined} />);
    expect(screen.queryByRole("button", { name: "Не сейчас" })).toBeNull();
  });
});
