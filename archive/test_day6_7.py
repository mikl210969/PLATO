import asyncio
import logging
from types import SimpleNamespace
from extensions.risk.advanced_risk_service import AdvancedRiskService

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("test_day6_7")

svc = AdvancedRiskService()

print("--- Теневая оценка синтетических сигналов ---")
scenarios = [
    # (symbol, side, entry, edge, rr, confidence, atr, mode, basis)
    ("SOLUSDT", "LONG",  140.0, 138.0, 2.6, 75, 2.0, "normal", 0.001),  # → Grade A
    ("SOLUSDT", "LONG",  140.0, 138.8, 2.2, 60, 2.0, "normal", 0.001),  # → Grade B
    ("SOLUSDT", "SHORT", 140.0, 141.2, 1.8, 90, 2.0, "high",   0.001),  # → Grade C
    ("SOLUSDT", "LONG",  140.0, 139.2, 1.4, 99, 2.0, "normal", 0.001),  # → REJECT
]
for sc in scenarios:
    d = svc.evaluate(*sc)
    log.info(f"DECISION: {d['action']} | grade={d.get('grade')} | size={d.get('size_multiplier')}")

print("\n--- on_signal: legacy-сигнал без edge_price (должен мягко пропустить) ---")
class FakeEvent:
    payload = {"signal": SimpleNamespace(symbol="SOLUSDT", side="short",
                                         entry_price=96.95, confidence=0.7)}
asyncio.run(svc.on_signal(FakeEvent()))
log.info("Shadow-сервис готов к подключению EventBus в Фазе 4")