import { Suspense, lazy } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import {
  BarChart3, BookOpenText, GitCompareArrows, Database, FileQuestion, FileUp, MessageSquareHeart, Plug,
  Route as RouteIcon, Settings2, TargetIcon,
} from "lucide-react";

const Dashboard = lazy(() => import("./Dashboard"));
const SourceConflictsPage = lazy(() => import("./SourceConflictsPage"));
const KbPage = lazy(() => import("./KbPage"));
const SettingsPage = lazy(() => import("./SettingsPage"));
const FeedbackPage = lazy(() => import("./FeedbackPage"));
const GapsPage = lazy(() => import("./GapsPage"));
const McpPage = lazy(() => import("./McpPage"));
const ImportPage = lazy(() => import("./ImportPage"));
const QualityPage = lazy(() => import("./QualityPage"));
const GuidesPage = lazy(() => import("./GuidesPage"));

const links = [
  { to: "/admin", end: true, label: "Дашборд", icon: BarChart3 },
  { to: "/admin/kb", end: false, label: "База знаний", icon: BookOpenText },
  { to: "/admin/source-conflicts", end: false, label: "Противоречия", icon: GitCompareArrows },
  { to: "/admin/import", end: false, label: "Загрузка документов", icon: FileUp },
  { to: "/admin/settings", end: false, label: "Настройки", icon: Settings2 },
  { to: "/admin/feedback", end: false, label: "Отзывы и качество", icon: MessageSquareHeart },
  { to: "/admin/gaps", end: false, label: "Пробелы БЗ", icon: FileQuestion },
  { to: "/admin/quality", end: false, label: "Регресс качества", icon: TargetIcon },
  { to: "/admin/guides", end: false, label: "Сценарии по порталу", icon: RouteIcon },
  { to: "/admin/mcp", end: false, label: "MCP-серверы", icon: Plug },
];

/**
 * Заголовок первого уровня для раздела админки.
 *
 * Визуально его заменяют карточки-шапки самих разделов, но в разметке он нужен:
 * скринридер строит по заголовкам оглавление страницы, а до этого во всей
 * админке не было ни одного `h1` — открыв раздел, незрячий администратор не
 * узнавал, куда попал. Название берём из той же карты, что и меню слева, чтобы
 * заголовок и пункт меню не разошлись.
 */
function SectionHeading() {
  const { pathname } = useLocation();
  const current = [...links]
    .sort((a, b) => b.to.length - a.to.length)
    .find(({ to, end }) => (end ? pathname === to : pathname.startsWith(to)));
  return <h1 className="sr-only">{current?.label ?? "Админка"}</h1>;
}

export default function AdminLayout() {
  return (
    <div className="mx-auto flex h-full w-full max-w-7xl flex-col gap-3 p-3 lg:flex-row">
      <aside className="card shrink-0 p-2 lg:w-56">
        <nav className="flex gap-1 overflow-x-auto lg:flex-col">
          {links.map(({ to, end, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={end}
              className={({ isActive }) =>
                `flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium transition-colors ${
                  isActive ? "bg-(--color-brand-soft) text-(--color-brand-ink)" : "hover:bg-(--color-surface-2)"
                }`}>
              <Icon size={15} /> {label}
            </NavLink>
          ))}
          <a href="/dbadmin" target="_blank" rel="noreferrer"
            className="flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium text-(--color-ink-soft) transition-colors hover:bg-(--color-surface-2)">
            <Database size={15} /> База данных ↗
          </a>
        </nav>
      </aside>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <SectionHeading />
        <Suspense fallback={<div className="p-8 text-center text-xs text-(--color-ink-soft)">Загрузка…</div>}>
          <Routes>
            <Route index element={<Dashboard />} />
            <Route path="source-conflicts" element={<SourceConflictsPage />} />
            <Route path="kb" element={<KbPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="feedback" element={<FeedbackPage />} />
            <Route path="gaps" element={<GapsPage />} />
            <Route path="guides" element={<GuidesPage />} />
            <Route path="mcp" element={<McpPage />} />
            <Route path="import" element={<ImportPage />} />
            <Route path="quality" element={<QualityPage />} />
            <Route path="*" element={<Navigate to="/admin" replace />} />
          </Routes>
        </Suspense>
      </div>
    </div>
  );
}
