# Быстрые команды (Linux/macOS/Git Bash). На Windows см. start.ps1
.PHONY: up down logs scale lite test lint build-front seed-reset backup restore

up:            ## весь стек одной командой
	docker compose up -d --build

up-voice:      ## + локальный голосовой ввод
	docker compose --profile voice up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api worker

scale:         ## пример: make scale N=3
	docker compose up -d --scale api=$(N)

lite:          ## демо без Docker: SQLite + fakeredis + mock-LLM
	cd backend && uv run uvicorn app.main:app --port 8090

test:
	cd backend && uv run pytest --cov -q

lint:
	cd backend && uv run ruff check app tests

build-front:
	cd frontend && npm run build

seed-reset:    ## пересоздать демо-данные (lite)
	rm -f backend/data/support.sqlite3 && $(MAKE) lite

backup:        ## копия базы и файлов -> backups/valerchik-<дата>/
	./scripts/backup.sh

restore:       ## восстановить: make restore FROM=backups/valerchik-...
	./scripts/restore.sh $(FROM)
