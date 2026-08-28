"""Грейды качества сделки (SL_TP.txt v2.2, раздел 1).
Pure function: на вход — цифры, на выход — (грейд, множитель размера)."""


def calculate_position_grade(rr_ratio: float, confidence_score: float) -> tuple:
    """
    R:R >= 2.5      → Grade A  → 100% позиции
    R:R 2.0 - 2.49  → Grade B  → 75%
    R:R 1.5 - 1.99  → Grade C  → 50%
    R:R < 1.5       → REJECT   → жёсткий блок (даже при confidence 100)
    """
    if rr_ratio >= 2.5:
        return "A", 1.0
    if rr_ratio >= 2.0:
        return "B", 0.75
    if rr_ratio >= 1.5:
        # ⚠️ НЮАНС ДОКУМЕНТА v2.2: ветка confidence >= 85 ("исключение для сильных
        # сигналов") в исходном коде возвращает тот же Grade C. Сохранено буквально.
        # Если бизнес-решение будет иным ("C только при confidence >= 85, иначе
        # REJECT") — в else достаточно вернуть ("REJECT", 0.0). Тесты ниже это
        # зафиксируют.
        if confidence_score >= 85:
            return "C", 0.5
        return "C", 0.5
    return "REJECT", 0.0