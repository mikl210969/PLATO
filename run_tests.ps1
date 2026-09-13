# run_tests.ps1 — Полный запуск тестов с информативным выводом (Исправленная версия)

# Не останавливать скрипт из-за предупреждений stderr от Python (например, urllib3 warning)
$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PLATO Test Runner" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. Информация о версии
Write-Host "📦 Версия платформы:" -ForegroundColor Yellow
$commitHash = git rev-parse --short HEAD 2>$null
$commitDate = git log -1 --format="%cd" --date=short 2>$null
$commitAuthor = git log -1 --format="%an" 2>$null
$commitMessage = git log -1 --format="%s" 2>$null

if ($commitHash) {
    Write-Host "   Коммит: $commitHash" -ForegroundColor Gray
    Write-Host "   Дата: $commitDate" -ForegroundColor Gray
    Write-Host "   Автор: $commitAuthor" -ForegroundColor Gray
    Write-Host "   Сообщение: $commitMessage" -ForegroundColor Gray
} else {
    Write-Host "   ⚠️ Не удалось получить информацию о коммите" -ForegroundColor Red
}

Write-Host ""

# 2. Проверка незакоммиченных изменений
Write-Host "🔍 Проверка незакоммиченных изменений..." -ForegroundColor Yellow
$gitStatus = git status --porcelain 2>$null

if ($gitStatus) {
    Write-Host "   ⚠️ Обнаружены незакоммиченные изменения:" -ForegroundColor Yellow
    Write-Host $gitStatus | ForEach-Object { "      $_" }
    Write-Host ""
    Write-Host "   Рекомендация: закоммить изменения перед запуском тестов" -ForegroundColor Yellow
} else {
    Write-Host "   ✅ Рабочая директория чиста" -ForegroundColor Green
}

Write-Host ""

# 3. Запуск тестов
Write-Host "🚀 Запуск интеграционных и chaos-тестов..." -ForegroundColor Yellow
Write-Host ""

$testFiles = @(
    "tests/test_integration_chaos.py",
    "tests/test_bus_message_loss.py",
    "tests/test_position_lifecycle.py",
    "tests/test_chaos_real_conditions.py",
    "tests/test_health_and_recovery.py"
)

$allPassed = $true

foreach ($testFile in $testFiles) {
    if (Test-Path $testFile) {
        Write-Host "📋 $testFile" -ForegroundColor Cyan
        
        # Запускаем pytest. PowerShell теперь не прервет выполнение из-за предупреждений Python
        pytest $testFile -v --tb=short
        
        # Проверяем реальный код возврата pytest (0 = успех, >0 = ошибки)
        if ($LASTEXITCODE -eq 0) {
            Write-Host "   ✅ Пройден успешно" -ForegroundColor Green
        } else {
            Write-Host "   ❌ Обнаружены ошибки (Exit code: $LASTEXITCODE)" -ForegroundColor Red
            $allPassed = $false
        }
        Write-Host ""
    } else {
        Write-Host "   ⚠️ Файл не найден: $testFile" -ForegroundColor Yellow
    }
}

# 4. Итоговый отчёт
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Итоговый отчёт" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if ($allPassed) {
    Write-Host "✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ!" -ForegroundColor Green
    Write-Host ""
    Write-Host "🎉 Платформа готова к запуску!" -ForegroundColor Green
} else {
    Write-Host "❌ ОБНАРУЖЕНЫ ОШИБКИ!" -ForegroundColor Red
    Write-Host ""
    Write-Host "⚠️ НЕ РЕКОМЕНДУЕТСЯ запускать платформу!" -ForegroundColor Red
    Write-Host "   Исправьте ошибки и запустите тесты снова." -ForegroundColor Yellow
}

Write-Host ""

# 5. Время последнего успешного прогона
$lastRunFile = "last_test_run.txt"
$currentTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

if ($allPassed) {
    $currentTime | Out-File $lastRunFile -Force
    Write-Host "📅 Последний успешный прогон: $currentTime" -ForegroundColor Green
} else {
    if (Test-Path $lastRunFile) {
        $lastSuccess = Get-Content $lastRunFile
        Write-Host "📅 Последний успешный прогон: $lastSuccess" -ForegroundColor Yellow
    } else {
        Write-Host "📅 Успешных прогонов ещё не было" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Нажмите Enter для выхода..." -ForegroundColor Gray
Read-Host