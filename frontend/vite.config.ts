import { fileURLToPath } from "node:url";
// defineConfig берётся из vitest, а не из vite: та же функция плюс поле `test`.
// Отдельный vitest.config.ts пришлось бы держать в синхронизации по алиасу «@»,
// а рассинхрон здесь стоит дорого — см. комментарий к alias ниже.
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // Алиас объявлен явно, а не только в tsconfig. Без этого «@/lib/store» и
    // «./store» приезжали к сборщику разными адресами одного файла, и модуль
    // попадал в бандл дважды: страница входа писала токен в один экземпляр
    // хранилища, а запросы читали пустой второй — админка отвечала «Не
    // авторизован» сразу после успешного входа.
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  // jsdom, а не node: модули интерфейса читают window.location прямо при
  // загрузке (адрес сервера), и в голом node такой импорт падает.
  test: { environment: "jsdom", include: ["src/**/*.test.ts", "src/**/*.test.tsx"] },
  server: {
    // тот же порт, что и у контейнера frontend (APP_PORT в docker-compose):
    // dev-сервер подменяет его, а не соседствует на своём — так закладки/расширение остаются рабочими
    port: 8090,
    // Windows иногда слушает только IPv6-loopback на "localhost" — браузер,
    // резолвящий его в IPv4, получает ERR_CONNECTION_REFUSED. 127.0.0.1 явно.
    host: "127.0.0.1",
    // без этого при занятом порте (например, ещё жив контейнер frontend на 8090)
    // vite молча уезжает на следующий свободный — и открываешь совсем не то окно
    strictPort: true,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
  // Ручной чанк для recharts убран намеренно: правило втягивало в чанк charts
  // ещё и неймспейс react, из-за чего entry получал СТАТИЧЕСКИЙ импорт графиков —
  // и 400 КБ кода дашборда скачивал каждый посетитель чата. Дефолтное разбиение
  // Rolldown кладёт recharts в ленивый чанк Dashboard, куда ему и надо.
});
