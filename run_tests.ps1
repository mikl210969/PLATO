# run_tests.ps1 — быстрый запуск всех chaos-тестов
Write-Host "🚀 Запуск всех интеграционных и chaos-тестов..." -ForegroundColor Cyan
pytest tests/test_integration_chaos.py tests/test_bus_message_loss.py tests/test_position_lifecycle.py tests/test_chaos_real_conditions.py -v --tb=short