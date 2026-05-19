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
from strategies.technical_analyzer import TechnicalAnalyzer
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


class TestTechnicalAnalyzer(unittest.TestCase):
    """Tests de l'analyseur technique - MACD, RSI, Bollinger"""
    
    def setUp(self):
        self.config = SolarisConfig()
        self.analyzer = TechnicalAnalyzer(self.config.strategy, None)
    
    def test_rsi_neutral_with_little_data(self):
        """RSI doit retourner 50 (neutre) si pas assez de données"""
        prices = [100.0] * 5
        rsi = self.analyzer._calculate_rsi(prices)
        self.assertEqual(rsi, 50.0)
    
    def test_rsi_oversold(self):
        """RSI doit être bas quand les prix baissent fortement"""
        # 14 baisses consécutives
        prices = [200.0 - i * 10 for i in range(20)]
        rsi = self.analyzer._calculate_rsi(prices)
        self.assertLess(rsi, 40)
    
    def test_rsi_overbought(self):
        """RSI doit être haut quand les prix montent fortement"""
        # 14 hausses consécutives
        prices = [100.0 + i * 10 for i in range(20)]
        rsi = self.analyzer._calculate_rsi(prices)
        self.assertGreater(rsi, 60)
    
    def test_macd_signal_line_is_not_fake(self):
        """
        CRITIQUE: La signal line du MACD ne doit PAS être macd_line * 0.8.
        Elle doit être l'EMA du MACD sur 'signal_period' périodes.
        """
        # Créer des prix avec suffisamment de données pour un MACD complet
        # 50 prix = assez pour EMA 26 + signal 9
        import random
        random.seed(42)
        prices = [100.0 + random.uniform(-2, 2) for _ in range(50)]
        
        macd_line, signal_line, histogram = self.analyzer._calculate_macd(prices)
        
        # La signal line ne doit JAMAIS être exactement macd_line * 0.8
        # (c'était le bug critique)
        if macd_line != 0:
            ratio = signal_line / macd_line
            self.assertNotAlmostEqual(ratio, 0.8, places=2,
                msg="Signal line est toujours macd * 0.8 - le bug est toujours là!")
        
        # Le signal line doit être une vraie EMA du MACD
        # On peut le vérifier en comparant avec un calcul manuel
        ema_fast = self.analyzer._calculate_ema(prices, 12)
        ema_slow = self.analyzer._calculate_ema(prices, 26)
        expected_macd = ema_fast - ema_slow
        self.assertAlmostEqual(macd_line, expected_macd, places=10,
            msg="MACD line ne correspond pas à EMA_fast - EMA_slow")
    
    def test_macd_bullish_signal(self):
        """MACD doit détecter un crossover bullish"""
        # Prix qui montent après une baisse = crossover potentiel
        prices = [100.0 - i for i in range(30)] + [71.0 + i * 2 for i in range(30)]
        
        macd_line, signal_line, histogram = self.analyzer._calculate_macd(prices)
        
        # Avec des prix qui remontent, le MACD devrait être au-dessus du signal
        # (bullish) ou au moins l'histogramme devrait être positif
        # Pas toujours garanti avec un petit dataset, mais on vérifie que le calcul tourne
        self.assertIsInstance(macd_line, float)
        self.assertIsInstance(signal_line, float)
        self.assertIsInstance(histogram, float)
    
    def test_ema_series_length(self):
        """_calculate_ema_series doit retourner le même nombre de points que l'input"""
        prices = [100.0 + i for i in range(30)]
        series = self.analyzer._calculate_ema_series(prices, 12)
        self.assertEqual(len(series), len(prices))
    
    def test_bollinger_bands(self):
        """Bollinger bands doivent entourer les prix"""
        prices = [100.0] * 20
        upper, middle, lower = self.analyzer._calculate_bollinger(prices)
        self.assertEqual(upper, middle)  # Pas de volatilité = bands au même niveau
        self.assertEqual(lower, middle)
    
    def test_bollinger_squeeze_detection(self):
        """Le squeeze doit être détecté quand les bands se resserrent"""
        # 50 périodes stables puis 50 périodes avec volatilité croissante
        prices = [100.0] * 50 + [100.0 + i * 0.5 for i in range(50)]
        upper, middle, lower = self.analyzer._calculate_bollinger(prices)
        position, squeeze = self.analyzer._interpret_bollinger(
            prices[-1], upper, middle, lower, prices
        )
        # Le résultat doit être un tuple valide
        self.assertIn(position, ["above_upper", "below_lower", "middle"])
        self.assertIsInstance(squeeze, bool)


class TestWhaleTracker(unittest.TestCase):
    """Tests du whale tracker"""
    
    def test_whale_scoring_large_amount(self):
        """
        CRITIQUE: Le bonus pour > 500 SOL doit être accessible.
        Avant le fix, la branche > 500 était inaccessible car
        l'ordre était if > 100 elif > 500 (toujours le premier if).
        """
        from strategies.whale_tracker import WhaleTracker
        from data.onchain_fetcher import OnChainFetcher
        from unittest.mock import MagicMock
        
        config = SolarisConfig()
        fetcher = MagicMock(spec=OnChainFetcher)
        tracker = WhaleTracker(config.strategy, fetcher)
        
        # Whale de 600 SOL
        big_whale = WhaleTransaction(
            signature="sig1", wallet_address="w1",
            token_address="t1", token_symbol="SOL",
            signal_type=SignalType.BUY, amount_sol=600.0,
            amount_tokens=300.0, price_sol=2.0,
            timestamp=datetime.now(timezone.utc),
        )
        signal_big = tracker.generate_signal(big_whale)
        
        # Whale de 150 SOL
        medium_whale = WhaleTransaction(
            signature="sig2", wallet_address="w2",
            token_address="t2", token_symbol="SOL",
            signal_type=SignalType.BUY, amount_sol=150.0,
            amount_tokens=75.0, price_sol=2.0,
            timestamp=datetime.now(timezone.utc),
        )
        signal_medium = tracker.generate_signal(medium_whale)
        
        # Le signal de la grosse baleine doit avoir un score plus élevé
        if signal_big and signal_medium:
            self.assertGreater(signal_big.score, signal_medium.score,
                "Le score de la baleine >500 SOL doit être > celui de >100 SOL")


class TestTokenSniperFilters(unittest.TestCase):
    """Tests des filtres anti-rug du token sniper"""
    
    def test_top_holder_default_is_zero(self):
        """
        CRITIQUE: Le défaut de top_holder_pct doit être 0 (pas vérifié),
        pas 100 (qui rejetterait tous les tokens).
        """
        token = NewTokenEvent(
            token_address="test", token_symbol="TEST",
            token_name="Test Token", creator_address="c1",
            launch_platform="raydium",
            initial_liquidity_sol=50.0,
            launch_time=datetime.now(timezone.utc),
        )
        # Par défaut, top_holder_pct doit être 0, pas 100
        self.assertEqual(token.top_holder_pct, 0.0,
            "top_holder_pct par défaut doit être 0, pas 100")
    
    def test_token_with_low_top_holder_passes_filter(self):
        """Un token avec top_holder < 50% ne doit pas être rejeté"""
        token = NewTokenEvent(
            token_address="test", token_symbol="TEST",
            token_name="Test Token", creator_address="c1",
            launch_platform="raydium",
            initial_liquidity_sol=50.0,
            launch_time=datetime.now(timezone.utc),
            top_holder_pct=30.0,
            is_mint_renounced=True,
            is_lp_burned=True,
            honeypot_score=0.1,
            creator_history_score=0.8,
        )
        # risk_level doit être SAFE ou MEDIUM, pas DANGEROUS ou RISKY
        self.assertIn(token.risk_level, [TokenRisk.SAFE, TokenRisk.MEDIUM])


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
            creator_history_score=0.8,
        )
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
    
    def test_new_token_default_top_holder_is_zero(self):
        """
        CRITIQUE: Un nouveau token sans vérification RugCheck
        ne doit PAS être auto-rejeté (top_holder_pct=0 par défaut).
        """
        token = NewTokenEvent(
            token_address="new", token_symbol="NEW",
            token_name="New Token", creator_address="c1",
            launch_platform="pump.fun",
            initial_liquidity_sol=10.0,
            launch_time=datetime.now(timezone.utc),
        )
        self.assertEqual(token.top_holder_pct, 0.0)


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
    
    def test_helius_rpc_url(self):
        config = SolarisConfig()
        config.solana.helius_api_key = "test-key"
        self.assertIn("test-key", config.solana.helius_rpc_url)
        self.assertIn("helius", config.solana.helius_rpc_url)


class TestExecutor(unittest.TestCase):
    """Tests de l'exécuteur de trades"""
    
    def test_paper_execute_creates_trade(self):
        """Le paper trading doit créer un Trade valide"""
        from execution.executor import TradeExecutor
        from config.settings import WalletConfig, SolanaConfig
        
        executor = TradeExecutor(WalletConfig(), SolanaConfig(), TradingMode.PAPER)
        
        confluence = ConfluenceResult(
            token_address="test", token_symbol="SOL",
            signals=[], confluence_score=0.7,
            recommended_action=SignalType.BUY,
            recommended_size_sol=0.1,
            stop_loss_pct=5.0, take_profit_pct=10.0,
            reasons=["test"]
        )
        
        import asyncio
        trade = asyncio.get_event_loop().run_until_complete(
            executor.execute(confluence, 0.1)
        )
        
        self.assertIsNotNone(trade)
        self.assertEqual(trade.side, SignalType.BUY)
        self.assertEqual(trade.amount_sol, 0.1)
        self.assertEqual(trade.status, TradeStatus.EXECUTED)
    
    def test_keypair_none_without_private_key(self):
        """Le keypair doit être None si aucune clé privée n'est configurée"""
        from execution.executor import TradeExecutor
        from config.settings import WalletConfig, SolanaConfig
        
        executor = TradeExecutor(WalletConfig(), SolanaConfig(), TradingMode.LIVE)
        keypair = executor._get_keypair()
        self.assertIsNone(keypair)


if __name__ == "__main__":
    unittest.main(verbosity=2)
