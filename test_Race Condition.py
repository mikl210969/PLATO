import pytest
import asyncio
from datetime import datetime
from unittest.mock import Mock, patch, AsyncMock

class TestRiskManagerDriftMonitorRaceCondition:
    """Тест на проверку синхронизации между RiskManager и DriftMonitor"""
    
    @pytest.mark.asyncio
    async def test_risk_manager_processes_before_drift_monitor(self):
        """
        Проверяет, что RiskManager успевает обработать уровни TP/SL
        до того как DriftMonitor обнаружит расхождение
        """
        # Setup
        risk_manager = Mock()
        drift_monitor = Mock()
        
        # Имитируем задержки
        risk_manager.process_time = 0.001  # 1ms
        drift_monitor.check_time = 0.0005  # 0.5ms
        
        # Сценарий: цена быстро достигает TP1
        passport_id = "PASS_TEST_001"
        tp1_price = 106.25
        current_price = 106.56
        
        # Проверяем временную метку
        with patch('time.time') as mock_time:
            mock_time.side_effect = [
                1788687215.000,  # Создание паспорта
                1788687215.001,  # RiskManager начинает обработку
                1788687215.002,  # DriftMonitor проверяет
                1788687215.003,  # Цена достигает TP1
            ]
            
            # Эмуляция гонки
            risk_task = asyncio.create_task(
                self.simulate_risk_manager_update(risk_manager, passport_id, tp1_price)
            )
            drift_task = asyncio.create_task(
                self.simulate_drift_monitor_check(drift_monitor, passport_id, current_price)
            )
            
            await asyncio.gather(risk_task, drift_task)
            
            # Проверяем, что RiskManager успел обновить guard
            risk_manager.guard_registered.assert_called()
            # И DriftMonitor не нашел расхождений
            drift_monitor.detect_drift.assert_not_called()
    
    async def simulate_risk_manager_update(self, rm, passport_id, price):
        """Симуляция обновления RiskManager"""
        await asyncio.sleep(0.001)
        rm.guard_registered(passport_id=passport_id, tp1=price)
    
    async def simulate_drift_monitor_check(self, dm, passport_id, price):
        """Симуляция проверки DriftMonitor"""
        await asyncio.sleep(0.0005)
        dm.detect_drift(passport_id=passport_id, price=price)