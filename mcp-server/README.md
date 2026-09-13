# MCP-сервер «Валерчик»

Даёт любому MCP-совместимому агенту (Claude Code, Claude Desktop, кастомные боты) инструменты
службы поддержки: задать вопрос, продолжить диалог, открыть статью-источник, поиск по БЗ, оценка.

## Тулы

| Тул | Что делает | Доступ |
|---|---|---|
| `ask_support(question)` | новый диалог → ответ + источники + уверенность | публичный |
| `continue_dialog(session_id, message)` | продолжение диалога | публичный |
| `get_source_article(article_id)` | полный текст статьи БЗ | публичный |
| `suggest_questions(prefix)` | автодополнение вопросов | публичный |
| `rate_dialog(session_id, rating, comment)` | оценка 1–5 | публичный |
| `search_knowledge_base(query)` | прямой гибридный поиск | staff (env-учётка) |
| `support_health()` | здоровье сервиса | публичный |

## Подключение к Claude Code

```bash
claude mcp add valerchik \
  --env VALERCHIK_URL=http://localhost:8090 \
  -- uv run --project C:/projects/zakupki-support-ai/mcp-server python server.py
```

## Подключение к Claude Desktop (claude_desktop_config.json)

```json
{
  "mcpServers": {
    "valerchik": {
      "command": "uv",
      "args": ["run", "--project", "C:/projects/zakupki-support-ai/mcp-server", "python", "server.py"],
      "env": {
        "VALERCHIK_URL": "http://localhost:8090",
        "VALERCHIK_STAFF_LOGIN": "op_maria",
        "VALERCHIK_STAFF_PASSWORD": "operator12345"
      }
    }
  }
}
```

## Отладка

```bash
cd mcp-server
uv run mcp dev server.py   # MCP Inspector в браузере
```
