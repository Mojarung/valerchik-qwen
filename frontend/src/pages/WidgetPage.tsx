import { Suspense, lazy, useEffect } from "react";
import { ExternalLink, Moon, Sun } from "lucide-react";
import { RequestsButton } from "@/components/RequestsButton";
import { API_BASE } from "@/lib/api";
import { listenForPageContext, sendThemeToHost } from "@/lib/pageContext";
import { useTheme } from "@/lib/store";

// Путь через алиас, как в App.tsx: относительный импорт того же файла сборщик
// считал отдельным модулем и клал ChatPage в бандл дважды (два чанка по 21 КБ).
const ChatPage = lazy(() => import("@/pages/ChatPage"));

/** Встраиваемый мини-чат для iframe (см. public/embed.js и docs/EMBED.md). */
export default function WidgetPage() {
  // Раздел портала присылает embed.js из родительского окна: он же обновляет
  // его при переходах, поэтому подписка живёт всё время работы виджета.
  useEffect(() => listenForPageContext(), []);
  const theme = useTheme((s) => s.theme);
  const setChoice = useTheme((s) => s.setChoice);
  // Расширение рисует кнопку и подсказки на самом портале — им нужна та же
  // тема, что и чату, иначе светлая карточка подсказки бьёт по глазам поверх
  // тёмного чата. Шлём при каждой смене, включая системную.
  useEffect(() => sendThemeToHost(theme), [theme]);
  const goingDark = theme === "light";
  // Ссылка на полный чат ведёт на сервер: внутри расширения виджет живёт на
  // своём origin, и window.location.origin указывал бы на само расширение
  const fullChat = API_BASE || window.location.origin;

  return (
    <div className="flex h-full flex-col bg-(--color-surface)">
      <div className="min-h-0 flex-1">
        <Suspense fallback={
          <div className="flex h-full items-center justify-center">
            <div className="size-7 animate-spin rounded-full border-2 border-(--color-line) border-t-(--color-brand)" />
          </div>
        }>
          <ChatPage />
        </Suspense>
      </div>
      {/* Подвал: тема здесь, а не в шапке — у виджета шапки нет, а тумблер
          нужен и во фрейме на чужом сайте, и в расширении */}
      <div className="flex items-center gap-2 border-t border-(--color-line) px-2 py-1 text-xs text-(--color-ink-soft)">
        <a
          href={fullChat}
          target="_blank"
          rel="noreferrer"
          className="flex min-w-0 flex-1 items-center justify-center gap-1 transition-colors hover:text-(--color-brand-ink)"
        >
          Работает на «Валерчике» — открыть полный чат <ExternalLink size={10} />
        </a>
        <RequestsButton compact />
        <button
          type="button"
          className="rate-btn shrink-0"
          onClick={() => setChoice(goingDark ? "dark" : "light")}
          title={goingDark ? "Включить тёмную тему" : "Включить светлую тему"}
          aria-label={goingDark ? "Включить тёмную тему" : "Включить светлую тему"}
        >
          {goingDark ? <Moon size={12} /> : <Sun size={12} />}
        </button>
      </div>
    </div>
  );
}
