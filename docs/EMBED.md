# Встраиваемый мини-чат (виджет)

Чат «Валерчика» встраивается на любой сайт одной строкой — плавающая кнопка справа снизу
открывает мини-чат в iframe (со «слаймовой» анимацией, голосом и всеми функциями).

## Подключение

```html
<script src="https://ВАШ-ХОСТ/embed.js" defer></script>
```

Всё. Скрипт сам определит адрес сервера по своему `src`, добавит кнопку и iframe на `/widget`.

### Настройки (необязательные атрибуты)

```html
<script
  src="https://support.example.ru/embed.js"
  data-url="https://support.example.ru"  <!-- если скрипт раздаётся с CDN, а чат — с другого хоста -->
  data-color="#0b6e4f"                   <!-- цвет кнопки под бренд сайта -->
  data-position="left"                   <!-- угол: right (по умолчанию) | left -->
  defer></script>
```

## Как это устроено

- `frontend/public/embed.js` — самодостаточный vanilla-скрипт (~2 КБ, без зависимостей):
  кнопка + iframe, ленивая загрузка (iframe создаётся при первом открытии).
- `/widget` — тот же React-чат, но без шапки и навигации + плашка «открыть полный чат».
- iframe получает `allow="microphone"` — голосовой ввод работает и во встройке.
- Сервер не отправляет `X-Frame-Options`, поэтому встраивание разрешено с любого домена.
  Чтобы ограничить список сайтов, добавьте в nginx:
  `add_header Content-Security-Policy "frame-ancestors https://razreshennyj-sajt.ru";`

## Локальная проверка

Откройте `http://localhost:8090/widget` напрямую, либо создайте тестовую страницу:

```html
<!doctype html><html><body style="height:200vh;background:#f5f5f5">
  <h1>Любой сайт</h1>
  <script src="http://localhost:8090/embed.js" defer></script>
</body></html>
```
