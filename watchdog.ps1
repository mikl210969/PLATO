# watchdog.ps1 — Умный Сторож для платформы PLATO
# Защищает от бесконечных циклов перезапуска (макс 2 раза в час)

$pythonExe = "C:\Users\ongul\AppData\Local\Programs\Python\Python311\python.exe"
$scriptPath = "D:\platform\PLATO\main.py"
$stateFile = "D:\platform\PLATO\restart_state.json"
$maxRestartsPerHour = 2
$cooldownSeconds = 15 # Пауза перед перезапуском, чтобы биржа "остыла"

function Test-RestartLimit {
    $now = Get-Date
    $oneHourAgo = $now.AddHours(-1)
    
    # Читаем или создаем состояние
    $state = @{}
    if (Test-Path $stateFile) {
        try {
            $state = Get-Content $stateFile -Raw | ConvertFrom-Json -AsHashtable
        } catch {
            $state = @{ restarts = @() }
        }
    } else {
        $state = @{ restarts = @() }
    }

    if (-not $state.ContainsKey("restarts")) { 
        $state["restarts"] = @() 
    }

    # Фильтруем только те перезапуски, которые были в последний час
    $validRestarts = @()
    foreach ($ts in $state["restarts"]) {
        $restartTime = [DateTime]::Parse($ts)
        if ($restartTime -gt $oneHourAgo) {
            $validRestarts += $ts
        }
    }
    
    $state["restarts"] = $validRestarts

    # ПРОВЕРКА ЛИМИТА
    if ($validRestarts.Count -ge $maxRestartsPerHour) {
        Write-Host "🛑 КРИТИЧЕСКАЯ ОШИБКА: Достигнут лимит перезапусков ($maxRestartsPerHour в час)." -ForegroundColor Red
        Write-Host "🛑 Платформа остановлена. Требуется ручное вмешательство." -ForegroundColor Red
        
        # Сохраняем очищенное состояние
        $state | ConvertTo-Json | Set-Content $stateFile
        return $false
    }

    # Добавляем текущий перезапуск в историю
    $state["restarts"] += $now.ToString("o")
    $state | ConvertTo-Json | Set-Content $stateFile
    return $true
}

Write-Host "🐶 [WATCHDOG] Запущен. Мониторинг платформы PLATO..." -ForegroundColor Cyan
Write-Host "📁 Состояние сохраняется в: $stateFile" -ForegroundColor Gray

while ($true) {
    # 1. Проверяем, можно ли перезапускать
    if (-not (Test-RestartLimit)) {
        break # Выходим из цикла, если лимит исчерпан
    }

    Write-Host "▶️  [WATCHDOG] Запуск платформы..." -ForegroundColor Green
    Write-Host "==================================================" -ForegroundColor Gray
    
    # 2. Запускаем main.py и ЖДЕМ его завершения
    $process = Start-Process -FilePath $pythonExe -ArgumentList $scriptPath -NoNewWindow -Wait -PassThru
    $exitCode = $process.ExitCode
    
    Write-Host "==================================================" -ForegroundColor Gray
    Write-Host "⚠️ [WATCHDOG] Платформа завершила работу с кодом: $exitCode" -ForegroundColor Yellow

    # 3. Анализируем код завершения
    if ($exitCode -eq 0) {
        Write-Host "✅ [WATCHDOG] Платформа остановлена нормально. Сторож завершает работу." -ForegroundColor Green
        break
    }
    elseif ($exitCode -eq 42) {
        Write-Host "🔄 [WATCHDOG] Код 42: Обнаружена полная слепота (BLIND)." -ForegroundColor Magenta
        Write-Host "⏳ [WATCHDOG] Ожидание $cooldownSeconds сек. перед безопасным перезапуском..." -ForegroundColor Magenta
        Start-Sleep -Seconds $cooldownSeconds
        # Цикл продолжается, платформа будет перезапущена
    }
    else {
        Write-Host "❌ [WATCHDOG] Неожиданная ошибка (Код $exitCode). Автоматический перезапуск запрещен." -ForegroundColor Red
        Write-Host "❌ [WATCHDOG] Проверьте логи платформы. Требуется ручное вмешательство." -ForegroundColor Red
        break # Останавливаемся при неизвестных ошибках (например, синтаксическая ошибка в коде)
    }
}

Write-Host "🛑 [WATCHDOG] Работа завершена." -ForegroundColor Red
Read-Host "Нажмите Enter для выхода..."