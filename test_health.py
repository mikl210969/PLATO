#!/usr/bin/env python3
"""
PLATO Health Check - быстрая проверка всех критических компонентов
Запуск: python test_health.py
"""

import sys
from pathlib import Path

GREEN = '\033[92m'
RED = '\033[91m'
BLUE = '\033[94m'
BOLD = '\033[1m'
RESET = '\033[0m'

class HealthChecker:
    def __init__(self):
        self.passed = 0
        self.failed = 0
    
    def check_file(self, filepath, desc):
        exists = Path(filepath).exists()
        symbol = "✅" if exists else "❌"
        color = GREEN if exists else RED
        print(f"{color}{symbol}{RESET} {desc}")
        if exists: self.passed += 1
        else: self.failed += 1
        return exists
    
    def check_pattern(self, filepath, pattern, desc):
        try:
            content = Path(filepath).read_text(encoding='utf-8')
            found = pattern in content
            symbol = "✅" if found else "❌"
            color = GREEN if found else RED
            print(f"{color}{symbol}{RESET} {desc}")
            if found: self.passed += 1
            else: self.failed += 1
            return found
        except Exception as e:
            print(f"{RED}❌{RESET} {desc} (ошибка: {e})")
            self.failed += 1
            return False
    
    def run(self):
        print(f"\n{BOLD}{BLUE}{'='*70}{RESET}")
        print(f"{BOLD}{BLUE}🔍 PLATO Health Check{RESET}")
        print(f"{BOLD}{BLUE}{'='*70}{RESET}\n")
        
        print(f"{BOLD}📁 Критические файлы:{RESET}")
        self.check_file("extensions/analytics/delta_monitor.py", "DeltaMonitor")
        self.check_file("extensions/analytics/monitor_factory.py", "MonitorFactory")
        self.check_file("strategies/absorption_v2.py", "AbsorptionV2")
        self.check_file("strategies/wall_fade_v3.py", "WallFadeV3")
        self.check_file("strategies/breakout_v1.py", "BreakoutV1")
        
        print(f"\n{BOLD}🔒 Фиксы защиты от сирот:{RESET}")
        self.check_pattern("trading/lifecycle_manager.py", "PENDING_STATUSES", 
                          "LifecycleManager: белый список статусов")
        self.check_pattern("trading/lifecycle_manager.py", "self._timers[passport_id] = task",
                          "LifecycleManager: сохранение таймера")
        self.check_pattern("trading/handlers/order_handler.py", "_reconcile_position_from_exchange",
                          "OrderHandler: сверка с биржей")
        self.check_pattern("trading/handlers/order_handler.py", "TTL_KEEP_OPEN",
                          "OrderHandler: защита от закрытия")
        self.check_pattern("trading/risk_manager.py", "account.get('P', [])",
                          "RiskManager: парсинг Binance a.P[].pa")
        self.check_pattern("trading/risk_manager.py", "_full_close_qty",
                          "RiskManager: закрытие по реальному остатку")
        
        print(f"\n{BOLD}📊 DeltaMonitor и стратегии:{RESET}")
        self.check_pattern("extensions/analytics/delta_monitor.py", "_determine_regime",
                          "DeltaMonitor: определение режима")
        self.check_pattern("extensions/analytics/delta_monitor.py", "DIVERGENCE_DETECTED",
                          "DeltaMonitor: публикация дивергенций")
        self.check_pattern("strategies/absorption_v2.py", "_last_divergence",
                          "AbsorptionV2: поддержка дивергенций")
        self.check_pattern("strategies/wall_fade_v3.py", "_last_divergence",
                          "WallFadeV3: поддержка дивергенций")
        self.check_pattern("strategies/breakout_v1.py", "_last_divergence",
                          "BreakoutV1: поддержка дивергенций")
        
        print(f"\n{BOLD}⚙️  Конфигурация:{RESET}")
        self.check_file("config/trading.json", "trading.json")
        self.check_file("config/main.json", "main.json")
        
        print(f"\n{BOLD}{BLUE}{'='*70}{RESET}")
        print(f"{BOLD}📊 ИТОГ:{RESET} {GREEN}✅ {self.passed}{RESET} пройдено, {RED}❌ {self.failed}{RESET} провалено")
        
        if self.failed == 0:
            print(f"\n{GREEN}{BOLD}🎉 ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ! Система готова.{RESET}\n")
            return 0
        else:
            print(f"\n{RED}{BOLD}⚠️  ОБНАРУЖЕНЫ ПРОБЛЕМЫ!{RESET}\n")
            return 1

if __name__ == "__main__":
    sys.exit(HealthChecker().run())