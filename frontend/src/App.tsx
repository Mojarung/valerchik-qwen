import { Suspense, lazy, useEffect } from "react";
import {
  BrowserRouter, Link, Navigate, Route, Routes, useLocation, useNavigate,
} from "react-router-dom";
import { Headset, LayoutDashboard, LogOut, MessageCircle, Moon, Sun } from "lucide-react";
import { RequestsButton } from "@/components/RequestsButton";
import { useAuth, useMeta, useTheme } from "@/lib/store";

// ленивые чанки: пользователь чата не грузит код админки и графиков
const ChatPage = lazy(() => import("@/pages/ChatPage"));
const LoginPage = lazy(() => import("@/pages/LoginPage"));
const OperatorPage = lazy(() => import("@/pages/operator/OperatorPage"));
const AdminLayout = lazy(() => import("@/pages/admin/AdminLayout"));
const WidgetPage = lazy(() => import("@/pages/WidgetPage"));

/** Переключатель темы: светлая ⇄ тёмная.
 *  Отдельного состояния «как в системе» в интерфейсе нет: пока в куке пусто,
 *  тема и так берётся из системы и следует за ней. Первое же нажатие
 *  фиксирует выбор человека в куке — с этого момента система не перебивает.
 *  Иконка показывает, куда переключит нажатие, а не что сейчас. */
function ThemeToggle() {
  const theme = useTheme((s) => s.theme);
  const setChoice = useTheme((s) => s.setChoice);
  const goingDark = theme === "light";
  const label = goingDark ? "Включить тёмную тему" : "Включить светлую тему";
  return (
    <button className="btn btn-ghost !min-h-0 !px-3.5 !py-2"
      onClick={() => setChoice(goingDark ? "dark" : "light")}
      title={label} aria-label={label}>
      {goingDark ? <Moon size={15} /> : <Sun size={15} />}
    </button>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const { role, displayName, ready, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  // Выход — это запрос к серверу: куку снимает он, JS её не видит. Уводим на
  // экран входа сами, потому что сотрудник мог нажать «Выйти» из админки, и
  // остаться на ней после выхода он не должен.
  const signOut = async () => {
    await logout();
    navigate("/login");
  };
  const nav = [
    { to: "/", label: "Чат", icon: MessageCircle, show: true },
    { to: "/operator", label: "Оператор", icon: Headset, show: role === "operator" || role === "admin" },
    { to: "/admin", label: "Админ", icon: LayoutDashboard, show: role === "admin" },
  ];
  // Рабочие разделы шире чата, и шапка стоит на той же сетке, что и содержимое
  // под ней. Общая ширина (max-w-5xl) не совпадала ни с одной из двух: над
  // лентой чата шапка вылезала на 128 px с каждой стороны, а над админкой,
  // наоборот, не доходила до края таблицы — края «ступенькой».
  const wide = location.pathname.startsWith("/admin") || location.pathname.startsWith("/operator");
  // Чат на широком экране — с боковой панелью «Моих обращений» (открытой или свёрнутой в
  // полоску): шапка по ширине панели и ленты и не прыгает при сворачивании.
  const pageWidth = wide ? "max-w-7xl px-3"
    : location.pathname === "/" ? "max-w-3xl px-3 sm:px-4 lg:max-w-6xl" : "max-w-3xl px-3 sm:px-4";
  return (
    <div className="flex h-full flex-col">
      {/* Первым в порядке обхода: тот, кто ходит с клавиатуры, попадает к
          содержимому одним нажатием, а не после всей шапки. Виден только в
          фокусе — мышью его никто не встретит. */}
      <a href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-50 focus:rounded-full focus:bg-(--color-brand) focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-white">
        Перейти к содержимому
      </a>
      <div className={`sticky top-2 z-20 mx-auto mt-2 w-full ${pageWidth}`}>
        <header className="card flex items-center gap-3 !rounded-full px-5 py-2 backdrop-blur">
          <div className="mr-auto leading-tight">
            <div className="text-sm font-bold tracking-tight">Валерчик</div>
            <div className="hidden text-xs text-(--color-ink-soft) sm:block">
              ИИ-поддержка Портала поставщиков
            </div>
          </div>
          <nav className="flex items-center gap-1" aria-label="Основная навигация">
            {nav.filter((n) => n.show).map(({ to, label, icon: Icon }) => (
              <Link
                key={to}
                to={to}
                // На телефоне подпись скрыта, и без aria-label пункт меню
                // остаётся безымянной иконкой для скринридера и голосового ввода.
                aria-label={label}
                aria-current={location.pathname === to ? "page" : undefined}
                className={`btn ${location.pathname === to || (to !== "/" && location.pathname.startsWith(to)) ? "btn-primary" : "btn-ghost"} !min-h-0 !px-3.5 !py-2`}
              >
                <Icon size={15} />
                <span className="hidden sm:inline">{label}</span>
              </Link>
            ))}
            {/* К прошлым обращениям — из навигации, а не из шапки чата: там на
                телефоне и так тесно имени специалиста и адресату. На широком
                экране у панели свой переключатель, он же возвращает свёрнутую. */}
            {location.pathname === "/" && <RequestsButton className="lg:hidden" />}
            {/* Смена темы — в шапке на всех страницах и ширинах, как у оператора.
                Плавающая кнопка в углу чата терялась и ложилась на поле ввода и
                боковые панели. */}
            <ThemeToggle />
            {/* Пока сервер не подтвердил сессию, роли ещё нет — покажи мы здесь
                «Вход», вошедший сотрудник видел бы приглашение войти на каждом
                обновлении вкладки. Место под кнопку остаётся пустым доли
                секунды и ничем не занято намеренно. */}
            {!ready ? null : role ? (
              <button type="button" className="btn btn-ghost !min-h-0 !px-3.5 !py-2" onClick={() => void signOut()}
                aria-label={`Выйти (${displayName ?? ""})`}
                title={`Выйти (${displayName ?? ""})`}>
                <LogOut size={15} />
              </button>
            ) : (
              <Link to="/login"
                className={`btn ${location.pathname === "/login" ? "btn-primary" : "btn-ghost"} !min-h-0 !px-3.5 !py-2 text-xs`}>
                <span className="sm:hidden">Вход</span>
                <span className="hidden sm:inline">Вход для сотрудников</span>
              </Link>
            )}
          </nav>
        </header>
      </div>
      <main id="main" className="min-h-0 flex-1 pt-2">{children}</main>
    </div>
  );
}

function Guard({ roles, children }: { roles: string[]; children: React.ReactNode }) {
  const role = useAuth((s) => s.role);
  const ready = useAuth((s) => s.ready);
  const location = useLocation();
  // Сессия живёт в httpOnly-куке, и до ответа `/api/auth/me` браузер не знает,
  // вошёл человек или нет. Судить в этот момент нельзя: страж отправил бы на
  // экран входа вошедшего сотрудника при каждом обновлении вкладки.
  if (!ready) return spinner;
  // Куда шли — помним: расширение открывает админку сразу на нужном разделе
  // (например, на сценариях с записанным черновиком), и после входа человек
  // должен попасть туда же, а не на дашборд с потерянной работой.
  if (!role || !roles.includes(role)) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <>{children}</>;
}

const spinner = (
  <div className="flex h-full items-center justify-center">
    <div className="size-8 animate-spin rounded-full border-2 border-(--color-line) border-t-(--color-brand)" />
  </div>
);

function AppRoutes() {
  const location = useLocation();
  // /widget — встраиваемый мини-чат (iframe): без шапки и навигации. Внутри
  // расширения браузера страница лежит по своему пути и помечена ?widget
  if (location.pathname.startsWith("/widget") || new URLSearchParams(location.search).has("widget")) {
    return (
      <Suspense fallback={spinner}>
        <WidgetPage />
      </Suspense>
    );
  }
  return (
    <Shell>
      <Suspense fallback={spinner}>
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/operator"
            element={<Guard roles={["operator", "admin"]}><OperatorPage /></Guard>}
          />
          <Route path="/admin/*" element={<Guard roles={["admin"]}><AdminLayout /></Guard>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </Shell>
  );
}

export default function App() {
  const loadMeta = useMeta((s) => s.load);
  const restoreSession = useAuth((s) => s.restore);
  const watchSystemTheme = useTheme((s) => s.watchSystem);
  useEffect(() => {
    void loadMeta();
  }, [loadMeta]);
  // Кто вошёл — знает только сервер: кука httpOnly, из JS её не прочитать.
  // Спрашиваем один раз при старте; до ответа страж держит разделы закрытыми.
  useEffect(() => {
    void restoreSession();
  }, [restoreSession]);
  // Пока выбрана системная тема, следим за ней: пользователь может переключить
  // её на ходу (в macOS и Windows это делает и расписание «тёмная вечером»).
  useEffect(() => watchSystemTheme(), [watchSystemTheme]);
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
