"""
ExchangeReconciler — непрерывный гарант принципа «биржа — единственный источник правды».

Каждые interval_sec сверяет биржу с локальными паспортами и приводит локальное
состояние к биржевому:
1. Ордер на бирже без живого паспорта (или у мёртвого) → отмена (орфан).
2. Protect-ордера (reduceOnly=True) НЕ трогаем — ими владеет Guard/RiskManager.
3. Паспорт ждёт ордер, которого нет на бирже (и позиции нет, и возраст > порога)
   → паспорт приводится к CANCELED.
4. Паспорт OPEN без позиции на бирже → публикуем SYNC_REQUEST (логика sync уже
   живёт в account_handler/state_manager — не дублируем).

Любой сбой REST (бан, таймаут) = цикл пропускается, а НЕ принимается решение
на основе незнания. Решение принимается только когда биржа ответила.
"""
import asyncio
import time
from datetime import datetime
from typing import List, Optional, Set

from core.logger import get_logger
from core.types import PassportStatus

logger = get_logger(__name__)

TERMINAL_STATUSES = (
    PassportStatus.CLOSED.value,
    PassportStatus.CANCELED.value,
    PassportStatus.FAILED.value,
)

ENTRY_WAIT_STATUSES = (
    PassportStatus.ORDER_SENT.value,
    PassportStatus.ORDER_ACK.value,
    PassportStatus.LIMIT_ON_BOOK.value,
)


def _ts_of(iso: Optional[str]) -> float:
    """ISO-строку created_at в unix-time; при ошибке — сейчас (не карать паспорт)."""
    if not iso:
        return time.time()
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return time.time()


class ExchangeReconciler:
    def __init__(
        self,
        rest_client,
        passport_manager,
        repository,
        event_bus,
        symbols: List[str],
        interval_sec: float = 60.0,
        stale_order_age_sec: float = 120.0,
    ):
        self.rest = rest_client
        self.passport_manager = passport_manager
        self.repository = repository
        self.bus = event_bus
        self.symbols = symbols
        self.interval_sec = interval_sec
        self.stale_order_age_sec = stale_order_age_sec
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"🔁 [RECONCILER] Started for {self.symbols} (interval={self.interval_sec}s)")

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 [RECONCILER] Stopped")

    async def _loop(self):
        # 🔥 ФАЗА 4: все запросы reconciler помечены своим caller-тегом
        from adapters.binance_rest import REST_CALLER
        REST_CALLER.set("reconciler")

        while self._running:
            try:
                await asyncio.sleep(self.interval_sec)
                for symbol in self.symbols:
                    await self._reconcile_symbol(symbol)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ [RECONCILER] cycle error: {type(e).__name__}: {e}")
                await asyncio.sleep(5)

    async def _safe_position_size(self, symbol: str) -> Optional[float]:
        """Размер позиции с биржи или None, если биржа не ответила (тогда НЕ решаем)."""
        try:
            pos = await self.rest.get_position(symbol)
            if pos is None:
                return None
            return abs(float(pos.get('size', 0) or 0))
        except Exception:
            return None

    async def _reconcile_symbol(self, symbol: str):
        # Бан активен → ничего не решаем, ждём следующий цикл
        if getattr(self.rest, '_ban_active', lambda: False)():
            logger.debug("⏸️ [RECONCILER] REST ban active — cycle skipped")
            return

        #Truth #1: живые ордера с биржи. None = биржа не ответила → цикл мимо.
        open_orders = await self.rest.get_open_orders_strict(symbol)
        if open_orders is None:
            logger.warning(f"⚠️ [RECONCILER] {symbol}: биржа не ответила на openOrders — cycle skipped")
            return

        passports = self.passport_manager.get_by_symbol(symbol)
        active = [p for p in passports if p.status not in TERMINAL_STATUSES]

        active_cids: Set[str] = set()
        for p in active:
            for o in (getattr(p, 'orders', []) or []):
                cid = o.get('client_order_id')
                if cid:
                    active_cids.add(str(cid))

        # ── ПРАВИЛО 1+2: орфан-ордера на бирже → отмена ─────────────
        for o in open_orders:
            status = str(o.get('status', ''))
            if status not in ('NEW', 'PARTIALLY_FILLED'):
                continue
            if bool(o.get('reduceOnly', False)):
                continue  # protect-ордер под Guard — не трогаем
            cid = str(o.get('clientOrderId', '') or '')
            if cid in active_cids:
                continue  # принадлежит живому паспорту — норма
            order_id = str(o.get('orderId', '') or '')
            result = await self.rest.cancel_order(symbol, order_id)
            logger.warning(
                f"🧹 [RECONCILER] {symbol}: орфан-ордер {order_id} ({cid}) отменён | "
                f"success={result.get('success')} | error={result.get('error', '')}"
            )

        # ── ПРАВИЛО 3: паспорт ждёт ордер, которого нет на бирже ────
        exchange_cids = {str(o.get('clientOrderId', '') or '') for o in open_orders}
        now = time.time()
        for p in active:
            if p.status not in ENTRY_WAIT_STATUSES:
                continue
            orders = getattr(p, 'orders', []) or []
            if not orders:
                continue
            last_cid = str(orders[-1].get('client_order_id', '') or '')
            if last_cid in exchange_cids:
                continue  # ордер на месте — норма
            age = now - _ts_of(p.created_at)
            if age < self.stale_order_age_sec:
                continue  # даём WS-событиям и verifier время
            size = await self._safe_position_size(symbol)
            if size is None:
                continue  # биржа молчит — не решаем
            if size > 0.001:
                continue  # позиция есть — ордер исполнился, события догонят
            p.transition_to(PassportStatus.CANCELED.value, "RECONCILER: order absent on exchange")
            self.repository.save(p)
            logger.warning(
                f"🧹 [RECONCILER] {symbol}: паспорт {p.passport_id} → CANCELED "
                f"(ордера нет на бирже, позиции нет, возраст {age:.0f}s)"
            )

        # ── ПРАВИЛО 4: паспорт OPEN без позиции → делегируем sync ───
        for p in active:
            if p.status != PassportStatus.OPEN.value:
                continue
            size = await self._safe_position_size(symbol)
            if size is None:
                break  # биржа молчит — не решаем
            if size < 0.001:
                await self.bus.publish(
                    event_type="SYNC_REQUEST",
                    source="reconciler",
                    payload={"symbol": symbol},
                    symbol=symbol,
                )
                logger.warning(
                    f"🧹 [RECONCILER] {symbol}: паспорт {p.passport_id} OPEN без позиции → SYNC_REQUEST"
                )
            break  # позиция на символ одна — одной проверки достаточно