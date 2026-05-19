"""
SOLARIS - Tests
===============
Tests unitaires et d'intégration pour valider le système.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from datetime import datetime, timezone

from config.settings import SolarisConfig, TradingMode, RiskLevel
from core.models import (
    Signal, SignalType, SignalSource, ConfluenceResult, 
    Trade, PortfolioState, TokenRisk, NewTokenEvent,
    WhaleTransaction, VolumeData, TradeStatus
)
from strategies.confluence_engine import ConfluenceEngine
from risk.manager import RiskManager
from utils.helpers import (
    lamports_to_sol, sol_to_lamports, shorten_address, format_sol,
    format_pct, calculate_pnl, validate_sol_address,
    CircuitBreaker, calculate_position_size_kelly
)


class TestConfluenceEngine(unittest.TestCase):
    """Tests du moteur de confluence"""
    
    def setUp(self):
        self.config = SolarisConfig()
        self.engine = ConfluenceEngine(self.config.confluence)
    
    def test_no_signals_returns_hold(self):
        result = self.engine.evaluate("test_token", [])
        self.assertEqual(result.recommended_action, SignalType.HOLD)
        self.assertEqual(result.confluence_score, 0.0)
    
    def test_single_weak_signal_returns_hold(self):
        signal = Signal(
            source=SignalSource.WHALE_TRACKING,
            signal_type=SignalType.BUY,
            token_address="test", token_symbol="TEST",
            score=0.3, reason="Weak signal"
        )
        result = self.engine.evaluate("test", [signal])
        self.assertEqual(result.recommended_action, SignalType.HOLD)
    
    def test_three_aligned_buy_signals_returns_buy(self):
        signals = [
            Signal(source=SignalSource.WHALE_TRACKING, signal_type=SignalType.BUY,
                   token_address="test", token_symbol="SOL", score=0.8, reason="Whale buy"),
            Signal(source=SignalSource.VOLUME_ANALYSIS, signal_type=SignalType.BUY,
                   token_address="test", token_symbol="SOL", score=0.7, reason="Volume spike"),
            Signal(source=SignalSource.TECHNICAL, signal_type=SignalType.BUY,
                   token_address="test", token_symbol="SOL", score=0.6, reason="RSI oversold"),
        ]
        result = self.engine.evaluate("test", signals)
        self.assertEqual(result.recommended_action, SignalType.BUY)
        self.assertTrue(result.is_actionable)
        self.assertGreater(result.confluence_score, 0.6)
    
    def test_conflicting_signals_returns_hold(self):
        signals = [
            Signal(source=SignalSource.WHALE_TRACKING, signal_type=SignalType.BUY,
                   token_address="test", token_symbol="SOL", score=0.8, reason="Whale buy"),
            Signal(source=SignalSource.WHALE_TRACKING, signal_type=SignalType.SELL,
                   token_address="test", token_symbol="SOL", score=0.9, reason="Whale sell"),
        ]
        result = self.engine.evaluate("test", signals)
        self.assertNotEqual(result.recommended_action, SignalType.BUY)


class TestRiskManager(unittest.TestCase):
    """Tests du gestionnaire de risque"""
    
    def setUp(self):
        self.config = SolarisConfig()
        self.portfolio = PortfolioState(total_sol=10.0, available_sol=10.0)
        self.risk_manager = RiskManager(self.config.risk, self.portfolio)
    
    def test_approve_valid_trade(self):
        confluence = ConfluenceResult(
            token_address="test", token_symbol="SOL",
            signals=[], confluence_score=0.7,
            recommended_action=SignalType.BUY,
            recommended_size_sol=0.1,
            stop_loss_pct=5.0, take_profit_pct=10.0,
            reasons=["test"]
        )
        approved, reason = self.risk_manager.validate_trade(confluence)
        self.assertTrue(approved)
    
    def test_reject_insufficient_sol(self):
        self.portfolio.available_sol = 0.001
        confluence = ConfluenceResult(
            token_address="test", token_symbol="SOL",
            signals=[], confluence_score=0.7,
            recommended_action=SignalType.BUY,
            recommended_size_sol=0.5,
            stop_loss_pct=5.0, take_profit_pct=10.0,
            reasons=["test"]
        )
        approved, reason = self.risk_manager.validate_trade(confluence)
        self.assertFalse(approved)
    
    def test_daily_loss_limit(self):
        # Max daily loss = 3% of 10 SOL = 0.3 SOL
        # We need to set daily_pnl AFTER _check_daily_reset has been called
        # So we bypass the reset by setting the date first
        from datetime import date
        self.risk_manager._daily_reset_date = date.today()  # Prevent reset
        self.portfolio.daily_pnl_sol = -0.31
        
        confluence = ConfluenceResult(
            token_address="test", token_symbol="SOL",
            signals=[], confluence_score=0.7,
            recommended_action=SignalType.BUY,
            recommended_size_sol=0.1,
            stop_loss_pct=5.0, take_profit_pct=10.0,
            reasons=["test"]
        )
        approved, reason = self.risk_manager.validate_trade(confluence)
        self.assertFalse(approved)
    
    def test_position_sizing(self):
        size = self.risk_manager.calculate_position_size(0.5, 0.8)
        self.assertGreater(size, 0)
        self.assertLessEqual(size, 10.0 * 0.05)


class TestHelpers(unittest.TestCase):
    """Tests des fonctions utilitaires"""
    
    def test_lamports_conversion(self):
        self.assertEqual(lamports_to_sol(1_000_000_000), 1.0)
        self.assertEqual(sol_to_lamports(1.0), 1_000_000_000)
    
    def test_shorten_address(self):
        addr = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
        short = shorten_address(addr)
        self.assertTrue("..." in short)
    
    def test_format_pct(self):
        self.assertEqual(format_pct(5.0), "+5.0%")
        self.assertEqual(format_pct(-3.0), "-3.0%")
    
    def test_calculate_pnl(self):
        pnl_abs, pnl_pct = calculate_pnl(100, 110, 1)
        self.assertAlmostEqual(pnl_abs, 10)
        self.assertAlmostEqual(pnl_pct, 10.0)
    
    def test_validate_sol_address(self):
        self.assertTrue(validate_sol_address("7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"))
        self.assertFalse(validate_sol_address("abc"))
        self.assertFalse(validate_sol_address(""))
    
    def test_circuit_breaker(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
        self.assertTrue(cb.can_execute())
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        self.assertFalse(cb.can_execute())
        cb.record_success()
        self.assertTrue(cb.can_execute())
    
    def test_kelly_criterion(self):
        size = calculate_position_size_kelly(0.6, 2.0, 1.0, fraction=0.5)
        self.assertGreater(size, 0)
        size_zero = calculate_position_size_kelly(0.0, 2.0, 1.0)
        self.assertEqual(size_zero, 0.0)


class TestDataModels(unittest.TestCase):
    """Tests des modèles de données"""
    
    def test_signal_types(self):
        self.assertEqual(SignalType.BUY.value, "BUY")
        self.assertEqual(SignalType.SELL.value, "SELL")
    
    def test_whale_transaction_smart_money(self):
        tx = WhaleTransaction(
            signature="sig123", wallet_address="w1",
            token_address="t1", token_symbol="SOL",
            signal_type=SignalType.BUY, amount_sol=100.0,
            amount_tokens=50.0, price_sol=2.0,
            timestamp=datetime.now(timezone.utc),
            wallet_win_rate=0.7, wallet_avg_roi=0.15,
        )
        self.assertTrue(tx.is_smart_money)
    
    def test_volume_data_spike(self):
        vol = VolumeData(
            token_address="test", token_symbol="SOL",
            current_volume_sol=300.0, average_volume_sol=80.0,
            volume_ratio=3.75, volume_change_1h_pct=50.0,
            volume_change_4h_pct=100.0, volume_change_24h_pct=200.0,
            buy_volume_pct=75.0, sell_volume_pct=25.0,
            unique_buyers_1h=30, unique_sellers_1h=10,
        )
        self.assertTrue(vol.is_spike)
        self.assertTrue(vol.is_buy_pressure)
    
    def test_new_token_risk_levels(self):
        safe = NewTokenEvent(
            token_address="safe", token_symbol="SAFE",
            token_name="Safe Token", creator_address="c1",
            launch_platform="raydium",
            initial_liquidity_sol=50.0,
            launch_time=datetime.now(timezone.utc),
            is_mint_renounced=True, is_lp_burned=True,
            honeypot_score=0.1, top_holder_pct=5.0,
            creator_history_score=0.8,  # High score = trusted creator
        )
        # With all safety checks passed, it should be MEDIUM or SAFE
        self.assertIn(safe.risk_level, [TokenRisk.SAFE, TokenRisk.MEDIUM])
    
    def test_trade_is_open(self):
        trade = Trade(
            id="t1", token_address="test", token_symbol="SOL",
            side=SignalType.BUY, entry_price_sol=0.001,
            amount_sol=0.1, amount_tokens=100.0,
            stop_loss_sol=0.00095, take_profit_sol=0.0015,
            status=TradeStatus.EXECUTED, confluence_score=0.7,
            signals=["whale_tracking"],
        )
        self.assertTrue(trade.is_open)


class TestConfig(unittest.TestCase):
    """Tests de la configuration"""
    
    def test_default_config(self):
        config = SolarisConfig()
        self.assertEqual(config.mode, TradingMode.PAPER)
        self.assertEqual(config.risk.risk_level, RiskLevel.MODERATE)
    
    def test_confluence_weights_sum(self):
        config = SolarisConfig()
        total = sum(config.confluence.signal_weights.values())
        self.assertAlmostEqual(total, 1.0, places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
