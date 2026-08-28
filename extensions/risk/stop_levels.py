"""Ступенчатый SL, BE, аварийный стоп, конвертация spot→futures
(SL_TP.txt v2.2, разделы 2, 5, 6 + улучшения из финального плана).
Pure functions: вся математика изолирована от биржи и БД."""

BUFFER_MULTIPLIERS = {"high": 0.5, "normal": 0.3, "low": 0.2}


def calculate_staircase_sl(edge_price: float, atr: float, volatility_mode: str,
                           side: str = "LONG",
                           sl2_atr_multiplier: float = 1.0) -> tuple:
    """SL1 — за краем стены с ATR-буфером (50% позиции).
    SL2 — за SL1 + 1 ATR (оставшиеся 50%).
    sl2_atr_multiplier — ручка для динамического буфера: при экстремальной
    волатильности в момент срабатывания SL1 менеджер может увеличить множитель,
    чтобы один импульс не выбил оба стопа."""
    if volatility_mode not in BUFFER_MULTIPLIERS:
        raise ValueError(f"Unknown volatility mode: {volatility_mode}")
    if atr <= 0:
        raise ValueError("ATR must be positive")

    buffer = atr * BUFFER_MULTIPLIERS[volatility_mode]
    if side == "LONG":
        sl1 = edge_price - buffer
        sl2 = sl1 - atr * sl2_atr_multiplier
    else:  # SHORT — зеркально
        sl1 = edge_price + buffer
        sl2 = sl1 + atr * sl2_atr_multiplier
    return sl1, sl2


def calculate_break_even(entry_price: float, atr: float, side: str) -> float:
    """BE со смещением 0.25*ATR внутрь позиции — защита от 'сбора стопов'."""
    offset = atr * 0.25
    return entry_price - offset if side == "LONG" else entry_price + offset


def calculate_emergency_sl(entry_price: float, r_value: float, side: str) -> float:
    """Этап 1 аварийного стопа: ENTRY ∓ 2R (защита от катастрофы)."""
    return entry_price - 2 * r_value if side == "LONG" else entry_price + 2 * r_value


def convert_spot_to_futures(spot_price: float, basis: float, side: str = "LONG",
                            slippage_buffer_pct: float = 0.0005) -> float:
    """Конвертация спотового уровня в фьючерсный через basis + микро-буфер
    0.05% на проскальзывание/комиссию (улучшение финального плана),
    чтобы стоп не сработал ложно из-за конвертации."""
    futures_price = spot_price * (1 + basis)
    if side == "LONG":
        return futures_price * (1 - slippage_buffer_pct)  # даём стопу "воздух" вниз
    return futures_price * (1 + slippage_buffer_pct)      # и вверх для шорта


def build_risk_plan(entry_price: float, edge_price: float, atr: float,
                    volatility_mode: str, side: str, basis: float) -> dict:
    """Собирает полный пакет уровней для Advanced Risk (Этапы 3-6 WallFade)."""
    sl1_spot, sl2_spot = calculate_staircase_sl(edge_price, atr, volatility_mode, side)
    sl1 = convert_spot_to_futures(sl1_spot, basis, side)
    sl2 = convert_spot_to_futures(sl2_spot, basis, side)
    r = abs(entry_price - sl1)
    return {
        "side": side,
        "sl1_spot": sl1_spot, "sl2_spot": sl2_spot,
        "sl1": sl1, "sl2": sl2,
        "r": r,
        "be": calculate_break_even(entry_price, atr, side),
        "emergency_sl": calculate_emergency_sl(entry_price, r, side),
    }