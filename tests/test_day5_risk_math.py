import pytest
from extensions.risk.grade_calculator import calculate_position_grade
from extensions.risk.stop_levels import (
    calculate_staircase_sl, calculate_break_even, calculate_emergency_sl,
    convert_spot_to_futures, build_risk_plan,
)


class TestGradeCalculator:
    def test_grade_a_boundary(self):
        assert calculate_position_grade(2.5, 50) == ("A", 1.0)

    def test_grade_a_high_rr(self):
        assert calculate_position_grade(3.2, 90) == ("A", 1.0)

    def test_grade_b_boundaries(self):
        assert calculate_position_grade(2.49, 60) == ("B", 0.75)
        assert calculate_position_grade(2.0, 60) == ("B", 0.75)

    def test_grade_c_boundaries(self):
        assert calculate_position_grade(1.99, 50) == ("C", 0.5)
        assert calculate_position_grade(1.5, 50) == ("C", 0.5)

    def test_grade_c_with_high_confidence(self):
        assert calculate_position_grade(1.99, 90) == ("C", 0.5)

    def test_reject_hard_block_even_max_confidence(self):
        assert calculate_position_grade(1.49, 100) == ("REJECT", 0.0)

    def test_reject_low_rr(self):
        assert calculate_position_grade(0.8, 95) == ("REJECT", 0.0)


class TestStaircaseSL:
    def test_long_normal(self):
        sl1, sl2 = calculate_staircase_sl(100.0, 2.0, "normal", "LONG")
        assert sl1 == pytest.approx(99.4)   # 100 - 2*0.3
        assert sl2 == pytest.approx(97.4)   # sl1 - 1 ATR

    def test_long_high_vol(self):
        sl1, sl2 = calculate_staircase_sl(100.0, 2.0, "high", "LONG")
        assert sl1 == pytest.approx(99.0)
        assert sl2 == pytest.approx(97.0)

    def test_short_low_vol_mirrored(self):
        sl1, sl2 = calculate_staircase_sl(100.0, 2.0, "low", "SHORT")
        assert sl1 == pytest.approx(100.4)
        assert sl2 == pytest.approx(102.4)

    def test_dynamic_sl2_buffer(self):
        _, sl2 = calculate_staircase_sl(100.0, 2.0, "normal", "LONG",
                                        sl2_atr_multiplier=1.5)
        assert sl2 == pytest.approx(96.4)   # расширенный буфер при экстрем. волатильности

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            calculate_staircase_sl(100.0, 2.0, "extreme", "LONG")

    def test_invalid_atr_raises(self):
        with pytest.raises(ValueError):
            calculate_staircase_sl(100.0, 0.0, "normal", "LONG")


class TestBreakEvenAndEmergency:
    def test_be_long_offset(self):
        assert calculate_break_even(100.0, 2.0, "LONG") == pytest.approx(99.5)

    def test_be_short_offset(self):
        assert calculate_break_even(100.0, 2.0, "SHORT") == pytest.approx(100.5)

    def test_emergency_long_2r(self):
        assert calculate_emergency_sl(100.0, 1.0, "LONG") == pytest.approx(98.0)

    def test_emergency_short_2r(self):
        assert calculate_emergency_sl(100.0, 1.0, "SHORT") == pytest.approx(102.0)


class TestConversionAndPlan:
    def test_spot_to_futures_with_basis(self):
        assert convert_spot_to_futures(100.0, 0.001, "LONG",
                                       slippage_buffer_pct=0.0) == pytest.approx(100.1)

    def test_risk_plan_long_ordering(self):
        plan = build_risk_plan(entry_price=140.0, edge_price=138.0, atr=2.0,
                               volatility_mode="normal", side="LONG", basis=0.0)
        assert plan["sl2"] < plan["sl1"] < plan["entry_price"] if "entry_price" in plan else plan["sl2"] < plan["sl1"] < 140.0
        assert plan["emergency_sl"] < plan["sl2"]      # 2R глубже обоих стопов
        assert plan["be"] < 140.0                      # BE со смещением вниз
        assert plan["r"] == pytest.approx(140.0 - plan["sl1"])

    def test_risk_plan_short_ordering(self):
        plan = build_risk_plan(entry_price=140.0, edge_price=142.0, atr=2.0,
                               volatility_mode="normal", side="SHORT", basis=0.0)
        assert plan["sl2"] > plan["sl1"] > 140.0
        assert plan["emergency_sl"] > plan["sl2"]
        assert plan["be"] > 140.0