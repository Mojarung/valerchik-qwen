import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// Сборка в один index.html: скрипты, стили и скриншоты внутри. Такую презентацию можно
// открыть двойным щелчком с флешки, без сервера и без интернета на площадке (кроме шрифтов).
export default defineConfig({
  base: "./",
  plugins: [react(), viteSingleFile()],
  build: { assetsInlineLimit: 100_000_000 },
  server: { port: 5190 },
});
