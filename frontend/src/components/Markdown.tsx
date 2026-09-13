import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/* Плагины и словарь компонентов — в модульной области, а не в теле функции.
   Литерал внутри рендера создаёт новые ссылки на каждый вызов, и react-markdown
   (он не кеширует ничего: новый processor + parse + runSync на каждый рендер)
   считал бы разметку заново даже под memo. */
const PLUGINS = [remarkGfm];
const COMPONENTS: Components = {
  a: ({ node: _node, children, ...props }) => (
    <a {...props} target="_blank" rel="noreferrer">{children}</a>
  ),
  // Таблицу модель строит из данных, ширину которых никто не ограничивал: пять
  // колонок со ссылками не влезают в пузырь и просто обрезаются по краю — конец
  // строки не увидеть никак. Оборачиваем в прокручиваемый блок: пузырь держит
  // свою ширину, а таблица листается вбок.
  table: ({ node: _node, ...props }) => (
    <div className="md-scroll">
      <table {...props} />
    </div>
  ),
};

/** Рендер Markdown в сообщениях чата (GFM: списки, таблицы, ссылки).
 *  memo обязателен: во время стрима ответа страница перерисовывается двадцать
 *  пять раз в секунду, и без него каждый уже готовый ответ переразбирался бы
 *  заново на каждом кадре. */
export const Markdown = memo(function Markdown({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={PLUGINS} components={COMPONENTS}>
        {children}
      </ReactMarkdown>
    </div>
  );
});
