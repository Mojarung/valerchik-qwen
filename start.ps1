# Подъём всего стека одной командой (Windows).
#   .\start.ps1              — базовый стек
#   .\start.ps1 -Voice       — + голосовой ввод (STT)
#   .\start.ps1 -Docs        — + разбор PDF с картинками (docling)
#   .\start.ps1 -S3          — + объектное хранилище картинок (MinIO)
#   .\start.ps1 -Ollama      — + своя LLM qwen3.8:27b в Ollama (скачивает ~18 ГБ)
#   .\start.ps1 -Scale 3     — 3 инстанса API за nginx
param(
    [switch]$Voice,
    [switch]$Rerank,
    [switch]$Docs,
    [switch]$S3,
    [switch]$Ollama,
    [int]$Scale = 1
)

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    # Секреты генерируем, а не берём из примера: стенд, поднятый одной командой,
    # раньше получал пароль admin12345 и такой же уезжал на сервер.
    function New-Secret([int]$Length) {
        $bytes = [byte[]]::new($Length)
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        -join ($bytes | ForEach-Object { "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"[$_ % 62] })
    }
    $secrets = @{ APP_SECRET = New-Secret 48; ADMIN_PASSWORD = New-Secret 16;
                  OPERATOR_PASSWORD = New-Secret 16; POSTGRES_PASSWORD = New-Secret 24 }
    $lines = Get-Content ".env"
    foreach ($name in $secrets.Keys) {
        if ($lines -match "^$name=") {
            $lines = $lines -replace "^$name=.*", "$name=$($secrets[$name])"
        } else {
            $lines += "$name=$($secrets[$name])"
        }
    }
    Set-Content ".env" $lines -Encoding utf8
    Write-Host "Создан .env со случайными секретами." -ForegroundColor Yellow
    Write-Host "  admin / $($secrets.ADMIN_PASSWORD)"
    Write-Host "  op_maria / $($secrets.OPERATOR_PASSWORD)"
}

# Флаг -Ollama разворачивает qwen3.8:27b рядом со стеком. Адрес модели пуст —
# подставляем локальную Ollama; заданный вручную (например, GPU-сервер) не трогаем.
# Адрес из .env применяется на первом старте — дальше его меняют в админке.
if ($Ollama) {
    $lines = Get-Content ".env"
    if (-not ($lines -match "^LLM_BASE_URL=.+")) {
        $lines = $lines -replace "^LLM_BASE_URL=.*", "LLM_BASE_URL=http://ollama:11434/v1"
        $lines = $lines -replace "^LLM_API_KEY=$", "LLM_API_KEY=ollama"
        if (-not ($lines -match "^LLM_MODEL=.+")) { $lines = $lines -replace "^LLM_MODEL=.*", "LLM_MODEL=qwen3.8:27b" }
        Set-Content ".env" $lines -Encoding utf8
        Write-Host "LLM: локальная Ollama (http://ollama:11434/v1)" -ForegroundColor Yellow
    }
}

$compose = @("compose")
if ($Voice) { $compose += @("--profile", "voice") }
if ($Rerank) { $compose += @("--profile", "rerank") }
if ($Docs) { $compose += @("--profile", "docs") }
if ($S3) { $compose += @("--profile", "s3") }
if ($Ollama) { $compose += @("--profile", "ollama") }
$compose += @("up", "-d", "--build", "--scale", "api=$Scale")

docker @compose
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "Валерчик запускается: http://localhost:8090" -ForegroundColor Green
    Write-Host "  чат:      http://localhost:8090/"
    Write-Host "  оператор: http://localhost:8090/operator   (op_maria, пароль в .env)"
    Write-Host "  админка:  http://localhost:8090/admin      (admin, пароль в .env)"
    Write-Host "  API-доки: в боевом режиме выключены, API_DOCS=true в .env включает /api/docs"
    Write-Host "Первый старт скачивает модель эмбеддингов — подождите 1-2 минуты."
}
