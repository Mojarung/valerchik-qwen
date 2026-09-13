import { useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Modal } from "./Modal";

/**
 * Проверяем ровно то, чего не было у прежних модалок и из-за чего жюри
 * поставило доступности четвёрку: закрытие с клавиатуры, имя диалога,
 * удержание и возврат фокуса.
 */
// Глобальные хуки vitest выключены, поэтому размонтирование после каждого
// теста делаем сами: иначе предыдущая модалка остаётся в документе и следующий
// запрос по роли находит две.
afterEach(cleanup);

describe("Modal", () => {
  it("закрывается по Escape", async () => {
    const onClose = vi.fn();
    render(<Modal title="Источник ответа" onClose={onClose}><button>Внутри</button></Modal>);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("объявляется как модальный диалог со своим именем", () => {
    render(<Modal title="Источник ответа" onClose={() => {}}><button>Внутри</button></Modal>);
    const dialog = screen.getByRole("dialog", { name: "Источник ответа" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
  });

  it("ставит фокус внутрь при открытии", () => {
    render(<Modal title="Заголовок" onClose={() => {}}><button>Первая</button></Modal>);
    expect(document.activeElement).toBe(screen.getByRole("dialog", { name: "Заголовок" }));
  });

  it("показывает доступную кнопку закрытия и широкий размер", async () => {
    const onClose = vi.fn();
    render(<Modal title="Разбор" size="xl" onClose={onClose}><button>Внутри</button></Modal>);
    const dialog = screen.getByRole("dialog", { name: "Разбор" });
    const close = screen.getByRole("button", { name: "Закрыть" });
    expect(dialog.className).toContain("max-w-5xl");
    expect(close.className).toContain("size-11");
    await userEvent.click(close);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("уважает initialFocus", () => {
    function Harness() {
      const ref = useRef<HTMLButtonElement>(null);
      return (
        <Modal title="Удалить?" onClose={() => {}} initialFocus={ref}>
          <button>Удалить</button>
          <button ref={ref}>Отмена</button>
        </Modal>
      );
    }
    render(<Harness />);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Отмена" }));
  });

  it("не выпускает Tab за пределы диалога", async () => {
    render(
      <Modal title="Заголовок" onClose={() => {}}>
        <button>Первая</button>
        <button>Вторая</button>
      </Modal>,
    );
    const dialog = screen.getByRole("dialog", { name: "Заголовок" });
    const close = screen.getByRole("button", { name: "Закрыть" });
    const first = screen.getByRole("button", { name: "Первая" });
    const second = screen.getByRole("button", { name: "Вторая" });
    expect(document.activeElement).toBe(dialog);
    await userEvent.tab();
    expect(document.activeElement).toBe(close);
    await userEvent.tab();
    expect(document.activeElement).toBe(first);
    await userEvent.tab();
    expect(document.activeElement).toBe(second);
    await userEvent.tab();
    expect(document.activeElement).toBe(close);
    await userEvent.tab({ shift: true });
    expect(document.activeElement).toBe(second);
  });

  it("возвращает фокус на кнопку, открывшую диалог", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>Открыть</button>
          {open && (
            <Modal title="Заголовок" onClose={() => setOpen(false)}>
              <button>Внутри</button>
            </Modal>
          )}
        </>
      );
    }
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Открыть" });
    await userEvent.click(opener);
    expect(document.activeElement).toBe(screen.getByRole("dialog", { name: "Заголовок" }));
    await userEvent.keyboard("{Escape}");
    expect(document.activeElement).toBe(opener);
  });

  it("включает раскрывающиеся summary в клавиатурный цикл", async () => {
    render(
      <Modal title="Разбор" onClose={() => {}}>
        <details><summary>Источник</summary><p>Текст</p></details>
      </Modal>,
    );
    await userEvent.tab();
    await userEvent.tab();
    expect(document.activeElement).toBe(screen.getByText("Источник"));
  });

  it("возвращает прокрутку страницы после закрытия", async () => {
    function Harness() {
      const [open, setOpen] = useState(true);
      return open ? (
        <Modal title="Заголовок" onClose={() => setOpen(false)}><button>Внутри</button></Modal>
      ) : null;
    }
    render(<Harness />);
    expect(document.body.style.overflow).toBe("hidden");
    await userEvent.keyboard("{Escape}");
    expect(document.body.style.overflow).toBe("");
  });

  it("не уводит фокус, пока родитель перерисовывается от ввода", async () => {
    // Страницы передают onClose новой стрелкой на каждый рендер. Эффект модалки
    // зависел от неё и на каждой букве возвращал фокус инициатору, а потом
    // первому полю: во втором поле формы печаталась только первая буква.
    function Form() {
      const [question, setQuestion] = useState("");
      const [answer, setAnswer] = useState("");
      return (
        <Modal title="Черновик" onClose={() => setQuestion("")}>
          <input aria-label="Вопрос" value={question} onChange={(e) => setQuestion(e.target.value)} />
          <textarea aria-label="Ответ" value={answer} onChange={(e) => setAnswer(e.target.value)} />
        </Modal>
      );
    }
    render(<Form />);

    await userEvent.type(screen.getByLabelText("Ответ"), "Раздел");

    expect((screen.getByLabelText("Ответ") as HTMLTextAreaElement).value).toBe("Раздел");
    expect((screen.getByLabelText("Вопрос") as HTMLInputElement).value).toBe("");
  });
});
