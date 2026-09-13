import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
// Шрифт едет с приложением: раньше Inter только объявлялся в токенах и на
// машине без него интерфейс молча уезжал в Segoe UI.
import "@fontsource-variable/inter";
import "./styles/global.css";
import { captureGuideDraft } from "@/lib/guideDraft";

// Расширение открывает админку с записанным сценарием в якоре адреса: снимаем
// его до роутера — путь может лежать через экран входа, и якорь потеряется.
captureGuideDraft();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
