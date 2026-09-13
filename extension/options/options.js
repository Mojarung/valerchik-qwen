/** Настройки расширения: адрес сервера и угол кнопки. Хранятся в storage.sync. */
const api = globalThis.browser ?? globalThis.chrome;
const DEFAULTS = { serverUrl: "https://194-31-175-172.sslip.io", side: "right", editorMode: false };
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

const form = document.getElementById("form");
const urlInput = document.getElementById("serverUrl");
// statusRow, а не status: в глобальной области у страницы уже есть window.status
const statusRow = document.getElementById("status");
const note = document.getElementById("note");
const editorMode = document.getElementById("editorMode");

/** Chrome отдаёт результат в колбэк, Firefox — промисом; принимаем любой. */
function call(fn, ...args) {
  return new Promise((resolve, reject) => {
    try {
      const maybe = fn(...args, (result) => {
        const failure = api.runtime?.lastError;
        if (failure) reject(new Error(failure.message));
        else resolve(result);
      });
      if (maybe && typeof maybe.then === "function") maybe.then(resolve, reject);
    } catch (error) {
      reject(error);
    }
  });
}

call(api.storage.sync.get.bind(api.storage.sync), DEFAULTS).then((items) => {
  const settings = { ...DEFAULTS, ...(items || {}) };
  urlInput.value = settings.serverUrl;
  const side = document.getElementById(`side-${settings.side === "left" ? "left" : "right"}`);
  if (side) side.checked = true;
  editorMode.checked = Boolean(settings.editorMode);
}).catch(() => {
  urlInput.value = DEFAULTS.serverUrl;
});

/**
 * Право ходить на чужой адрес: без него Chrome применяет к серверу обычные
 * правила страницы, а к локальной сети — ещё и свою блокировку. Для localhost
 * право выдано манифестом. Отказ (или браузер без optional_host_permissions,
 * как Firefox) не ошибка: сервер Валерчика разрешает CORS, чат заработает и так.
 */
async function requestAccess(origin) {
  if (LOCAL_HOSTS.has(new URL(origin).hostname)) return true;
  if (!api.permissions?.request) return false;
  try {
    return Boolean(await call(api.permissions.request.bind(api.permissions), { origins: [`${origin}/*`] }));
  } catch {
    return false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  let serverUrl;
  try {
    serverUrl = new URL(urlInput.value.trim()).href.replace(/\/+$/, "");
  } catch {
    urlInput.setCustomValidity("Нужен полный адрес, например https://194-31-175-172.sslip.io или http://localhost:8090");
    urlInput.reportValidity();
    return;
  }
  urlInput.setCustomValidity("");
  const side = form.querySelector('input[name="side"]:checked')?.value === "left" ? "left" : "right";
  // запрос прав — только из обработчика клика, иначе браузер его молча отклонит
  const granted = requestAccess(new URL(serverUrl).origin);
  call(api.storage.sync.set.bind(api.storage.sync),
       { serverUrl, side, editorMode: editorMode.checked }).then(async () => {
    statusRow.classList.add("on");
    setTimeout(() => statusRow.classList.remove("on"), 2500);
    note.textContent = (await granted)
      ? ""
      : "Доступ к этому адресу расширению не выдан — чат пойдёт к серверу по обычным правилам браузера (CORS).";
  }).catch((error) => {
    note.textContent = `Не удалось сохранить: ${error instanceof Error ? error.message : String(error)}`;
  });
});
