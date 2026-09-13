# Валерчик — расширение для zakupki.mos.ru

Готовое расширение (Manifest V3, без сборки) для Chrome / Edge / Firefox: чат
поддержки на страницах Портала поставщиков и пошаговые подсказки прямо на
портале — Валерчик подсвечивает кнопку и объясняет, что нажать.

- Установка и устройство: [docs/EXTENSION.md](../docs/EXTENSION.md)
- Селекторы и маршруты портала: [docs/PORTAL_MAP.md](../docs/PORTAL_MAP.md)
- Сценарии шагов: `backend/app/guides/portal.json`

```bash
# Chrome / Edge: chrome://extensions → режим разработчика → «Загрузить распакованное» → эта папка
# Firefox:       about:debugging#/runtime/this-firefox → «Загрузить временное дополнение» → manifest.json
node scripts/icons.mjs    # перерисовать иконки из icons/icon.svg
node scripts/e2e.mjs      # живая проверка на портале (нужен сервер на localhost:8090)
```
