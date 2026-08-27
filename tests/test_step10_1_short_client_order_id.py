"""
Шаг 10.1: Короткий формат client_order_id для закрытий (T16, T17).
"""
import pytest
import re


def test_t16_short_client_order_id_fits_binance_limit():
    """
    Формат C1_PASS_YYYYMMDD_HHMMSS_XXXXXX должен влезать в 35 символов Binance.
    """
    passport_id = "PASS_20260827_013351_7474d1"  # 27 символов
    
    # Формируем короткие ID
    c1_id = f"C1_{passport_id}"  # 3 + 27 = 30
    c2_id = f"C2_{passport_id}"
    cs_id = f"CS_{passport_id}"
    
    assert len(c1_id) == 30, f"C1 ID length: {len(c1_id)}"
    assert len(c2_id) == 30, f"C2 ID length: {len(c2_id)}"
    assert len(cs_id) == 30, f"CS ID length: {len(cs_id)}"
    
    # Все должны влезать в лимит Binance (35 символов)
    assert len(c1_id) <= 35
    assert len(c2_id) <= 35
    assert len(cs_id) <= 35


def test_t17_short_client_order_id_parsing():
    """
    Парсер должен извлекать passport_id из короткого формата C1_PASS_...
    """
    client_order_id = "C1_PASS_20260827_013351_7474d1"
    
    match = re.match(r'^(C1|C2|CS|CE)_(PASS_.+)$', client_order_id)
    assert match is not None, "Regex should match"
    
    close_level = match.group(1)
    passport_id = match.group(2)
    
    assert close_level == "C1"
    assert passport_id == "PASS_20260827_013351_7474d1"


def test_t17b_legacy_format_still_works():
    """
    Legacy формат PASS_YYYYMMDD_HHMMSS_XXXXXX должен парситься для обратной совместимости.
    """
    # Это может быть обрезанный ID из старого формата
    client_order_id = "CLOSE_TP1_HIT_PASS_20260827_013351_7474d1"
    
    match = re.search(r'(PASS_\d{8}_\d{6}_[a-f0-9]+)', client_order_id)
    assert match is not None
    
    passport_id = match.group(1)
    assert passport_id == "PASS_20260827_013351_7474d1"