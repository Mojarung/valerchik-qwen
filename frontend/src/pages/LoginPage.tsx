import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { LogIn } from "lucide-react";
import { api } from "@/lib/api";
import type { Api } from "@/lib/types";
import { useAuth, useMeta } from "@/lib/store";
import { Mascot } from "@/components/Mascot";

/**
 * Ответ входа в том объёме, который нужен интерфейсу.
 *
 * JWT сервер по-прежнему кладёт и в тело — для curl, тестов и mcp-сервера, —
 * но страница его не читает: права подтверждает httpOnly-кука, поставленная
 * тем же ответом. Поле не описано здесь намеренно, чтобы никто не «починил»
 * страницу, снова положив токен в JS.
 */
type LoginResponse = Pick<Api["TokenResponse"], "role" | "display_name">;

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const setUser = useAuth((s) => s.setUser);
  // Демо-доступы подсказываем, только пока сервер заводит демо-пароли: в боевом
  // режиме подсказка вела бы в тупик, раскрывая логины сотрудников.
  const demo = useMeta((s) => s.demo);
  const navigate = useNavigate();
  // Откуда нас сюда отправил Guard: сотрудник вернётся ровно на тот раздел
  const from = (useLocation().state as { from?: string } | null)?.from ?? "";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const data = await api.post<LoginResponse>("/api/auth/login", { username, password });
      setUser(data.role, data.display_name);
      const home = data.role === "admin" ? "/admin" : data.role === "operator" ? "/operator" : "/";
      // Возврат только внутрь приложения и только туда, куда роль пускает
      const allowed = from.startsWith("/") && !from.startsWith("//")
        && (data.role === "admin" || !from.startsWith("/admin"));
      navigate(allowed && from ? from : home);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка входа");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full items-center justify-center px-4">
      <form onSubmit={submit} className="card animate-(--animate-pop) w-full max-w-sm p-6">
        <div className="mb-4 flex flex-col items-center gap-2">
          <Mascot size={80} mood="happy" />
          <h1 className="text-lg font-bold">Вход для сотрудников</h1>
          <p className="text-center text-xs text-(--color-ink-soft)">
            Операторы и администраторы службы поддержки
          </p>
        </div>
        <label className="label" htmlFor="login">Логин</label>
        <input id="login" className="input mb-3" value={username} autoComplete="username"
          onChange={(e) => setUsername(e.target.value)} placeholder={demo ? "admin / op_maria" : undefined} />
        <label className="label" htmlFor="password">Пароль</label>
        <input id="password" type="password" className="input mb-4" value={password}
          autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
        {error && <div className="mb-3 text-center text-xs text-(--color-brand-ink)">{error}</div>}
        <button className="btn btn-primary w-full" disabled={busy || !username || !password}>
          <LogIn size={15} /> Войти
        </button>
        {demo && (
          <p className="mt-4 text-center text-xs leading-relaxed text-(--color-ink-soft)">
            Демо-доступы: <code>admin</code> / <code>admin12345</code>,{" "}
            <code>op_maria</code> / <code>operator12345</code>
          </p>
        )}
      </form>
    </div>
  );
}
