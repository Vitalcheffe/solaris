"""
SOLARIS - Backtesting Engine
============================
Moteur de backtesting pour tester les stratégies sur données historiques.
Utilise les vraies données OHLCV de Birdeye pour des résultats réalistes.
"""

import asyncio
import logging
from typing import List, Dict, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, field

from core.models import (
    Signal, ConfluenceResult, Trade, PortfolioState,
    SignalType, SignalSource, TradeStatus
)
from strategies.confluence_engine import ConfluenceEngine
from risk.manager import RiskManager
from config.settings import SolarisConfig, ConfluenceConfig, RiskConfig
from data.onchain_fetcher import OnChainFetcher
from data.price_feed import PriceFeed, TOKEN_MINTS

logger = logging.getLogger("solaris.backtest")


@dataclass
class BacktestResult:
    """Résultat d'un backtest"""
    start_date: datetime
    end_date: datetime
    initial_capital_sol: float
    final_capital_sol: float
    
    # Métriques
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl_sol: float = 0.0
    total_pnl_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    best_trade_pct: float = 0.0
    worst_trade_pct: float = 0.0
    
    # Détails
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)


class Backtester:
    """
    Moteur de backtesting SOLARIS.
    
    Simule l'exécution des stratégies sur des données historiques réelles
    pour évaluer leur performance avant de risquer de l'argent réel.
    
    DEUX MODES :
    1. Avec signaux historiques (comme avant) — pour tester le moteur de confluence
    2. Avec données OHLCV réelles — pour backtester les indicateurs techniques
       sur de vraies bougies (RECOMMANDÉ)
    
    Usage:
        backtester = Backtester(config)
        
        # Mode OHLCV (recommandé) — télécharge les vraies données
        result = await backtester.run_on_ohlcv("BONK", "1H", days=30)
        
        # Mode signaux historiques
        result = await backtester.run(historical_signals)
    """
    
    def __init__(self, config: SolarisConfig):
        self.config = config
        self.confluence_engine = ConfluenceEngine(config.confluence)
        self.onchain_fetcher = OnChainFetcher(config.solana)
        self.price_feed = PriceFeed(config.solana)
    
    async def run_on_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1H",
        initial_capital: float = 10.0,
        days: int = 30,
    ) -> BacktestResult:
        """
        Backtest basé sur de vraies données OHLCV historiques.
        
        Télécharge les bougies depuis Birdeye, calcule les indicateurs
        techniques sur chaque bougie, et simule les entrées/sorties.
        
        C'est la méthode RECOMMANDÉE car elle utilise des données réelles.
        
        Args:
            symbol: Symbole du token (ex: "SOL", "BONK")
            timeframe: Timeframe des bougies ("1m", "5m", "15m", "1H", "4H", "1D")
            initial_capital: Capital initial en SOL
            days: Nombre de jours d'historique à tester
        """
        logger.info(f"Backtest OHLCV démarré — {symbol} {timeframe} — Capital: {initial_capital} SOL")
        
        # Connexion pour télécharger les données
        await self.onchain_fetcher.connect()
        await self.price_feed.start()
        
        try:
            # Télécharger les données OHLCV
            candles = await self.price_feed.get_ohlcv(
                symbol, timeframe, limit=min(days * 24 if timeframe == "1H" else days * 6 if timeframe == "4H" else days * 288 if timeframe == "5m" else days, 500)
            )
            
            if not candles or len(candles) < 50:
                logger.error(f"Pas assez de données OHLCV pour {symbol}: {len(candles) if candles else 0} bougies")
                return BacktestResult(
                    start_date=datetime.now(timezone.utc),
                    end_date=datetime.now(timezone.utc),
                    initial_capital_sol=initial_capital,
                    final_capital_sol=initial_capital,
                )
            
            logger.info(f"Données OHLCV chargées: {len(candles)} bougies pour {symbol}")
            
            # Lancer le backtest sur les bougies
            return await self._backtest_on_candles(symbol, candles, initial_capital)
            
        finally:
            await self.onchain_fetcher.disconnect()
            await self.price_feed.stop()
    
    async def _backtest_on_candles(
        self,
        symbol: str,
        candles: List[Dict],
        initial_capital: float,
    ) -> BacktestResult:
        """
        Exécute le backtest sur les bougies OHLCV.
        
        Pour chaque bougie :
        1. Calcule les indicateurs techniques (RSI, MACD, Bollinger)
        2. Génère un signal si les conditions sont remplies
        3. Simule l'entrée à l'ouverture de la bougie suivante
        4. Gère la position (SL/TP/trailing) sur les bougies suivantes
        """
        from strategies.technical_analyzer import TechnicalAnalyzer
        from config.settings import StrategyConfig
        
        # Préparer l'analyseur technique
        tech_analyzer = TechnicalAnalyzer(self.config.strategy, self.price_feed)
        
        # Initialiser le portfolio virtuel
        portfolio = PortfolioState(
            total_sol=initial_capital,
            available_sol=initial_capital,
        )
        risk_manager = RiskManager(self.config.risk, portfolio)
        
        all_trades: List[Trade] = []
        equity_curve = [initial_capital]
        open_trades: List[Trade] = []
        
        closes = [float(c.get("c", c.get("close", 0))) for c in candles]
        highs = [float(c.get("h", c.get("high", 0))) for c in candles]
        lows = [float(c.get("l", c.get("low", 0))) for c in candles]
        
        # Parcourir les bougies à partir d'un index minimum pour avoir assez de données
        min_index = 50  # Besoin d'au moins 50 bougies pour les indicateurs
        
        for i in range(min_index, len(closes)):
            current_close = closes[i]
            current_high = highs[i]
            current_low = lows[i]
            
            # --- Gérer les positions ouvertes ---
            for trade in open_trades[:]:
                # Vérifier si le stop-loss est touché (utiliser le low de la bougie)
                if current_low <= trade.stop_loss_sol:
                    trade.exit_price_sol = trade.stop_loss_sol
                    trade.exit_time = datetime.now(timezone.utc)
                    trade.pnl_sol = (trade.stop_loss_sol - trade.entry_price_sol) * trade.amount_tokens
                    trade.pnl_pct = ((trade.stop_loss_sol - trade.entry_price_sol) / trade.entry_price_sol) * 100
                    trade.status = TradeStatus.EXECUTED
                    self._update_portfolio_after_close(portfolio, trade)
                    open_trades.remove(trade)
                    all_trades.append(trade)
                    equity_curve.append(portfolio.total_sol)
                    continue
                
                # Vérifier si le take-profit est touché (utiliser le high de la bougie)
                if current_high >= trade.take_profit_sol:
                    trade.exit_price_sol = trade.take_profit_sol
                    trade.exit_time = datetime.now(timezone.utc)
                    trade.pnl_sol = (trade.take_profit_sol - trade.entry_price_sol) * trade.amount_tokens
                    trade.pnl_pct = ((trade.take_profit_sol - trade.entry_price_sol) / trade.entry_price_sol) * 100
                    trade.status = TradeStatus.EXECUTED
                    self._update_portfolio_after_close(portfolio, trade)
                    open_trades.remove(trade)
                    all_trades.append(trade)
                    equity_curve.append(portfolio.total_sol)
                    continue
                
                # Trailing stop
                if self.config.risk.trailing_stop_enabled:
                    if trade.highest_price_sol is None or current_high > trade.highest_price_sol:
                        trade.highest_price_sol = current_high
                    
                    activation_price = trade.entry_price_sol * (1 + self.config.risk.trailing_stop_activation_pct / 100)
                    if current_high >= activation_price:
                        trailing_distance = trade.highest_price_sol * (self.config.risk.trailing_stop_distance_pct / 100)
                        trailing_stop = trade.highest_price_sol - trailing_distance
                        if trade.trailing_stop_sol is None or trailing_stop > trade.trailing_stop_sol:
                            trade.trailing_stop_sol = trailing_stop
                        
                        if trade.trailing_stop_sol and current_low <= trade.trailing_stop_sol:
                            trade.exit_price_sol = trade.trailing_stop_sol
                            trade.exit_time = datetime.now(timezone.utc)
                            trade.pnl_sol = (trade.trailing_stop_sol - trade.entry_price_sol) * trade.amount_tokens
                            trade.pnl_pct = ((trade.trailing_stop_sol - trade.entry_price_sol) / trade.entry_price_sol) * 100
                            trade.status = TradeStatus.EXECUTED
                            self._update_portfolio_after_close(portfolio, trade)
                            open_trades.remove(trade)
                            all_trades.append(trade)
                            equity_curve.append(portfolio.total_sol)
                            continue
            
            # --- Analyser les indicateurs techniques sur les données disponibles ---
            subset_closes = closes[:i+1]
            
            rsi = tech_analyzer._calculate_rsi(subset_closes)
            rsi_signal = tech_analyzer._interpret_rsi(rsi)
            macd_line, signal_line, histogram = tech_analyzer._calculate_macd(subset_closes)
            macd_crossover = tech_analyzer._interpret_macd(macd_line, signal_line, histogram)
            bb_upper, bb_middle, bb_lower = tech_analyzer._calculate_bollinger(subset_closes)
            bb_position, bb_squeeze = tech_analyzer._interpret_bollinger(
                current_close, bb_upper, bb_middle, bb_lower, subset_closes
            )
            sma_7 = tech_analyzer._calculate_sma(subset_closes, 7)
            sma_25 = tech_analyzer._calculate_sma(subset_closes, 25)
            ema_12 = tech_analyzer._calculate_ema(subset_closes, 12)
            ema_26 = tech_analyzer._calculate_ema(subset_closes, 26)
            
            technical_score = tech_analyzer._calculate_composite_score(
                rsi, rsi_signal, macd_crossover, bb_position, bb_squeeze,
                current_close, sma_7, sma_25, ema_12, ema_26
            )
            
            # Générer un signal technique
            if abs(technical_score) > 0.3:
                signal_type = SignalType.BUY if technical_score > 0 else SignalType.SELL
                confidence = min(abs(technical_score), 1.0) * 0.6
                
                signal = Signal(
                    source=SignalSource.TECHNICAL,
                    signal_type=signal_type,
                    token_address=symbol,
                    token_symbol=symbol,
                    score=confidence,
                    reason=f"Backtest signal: RSI={rsi:.1f}, MACD={macd_crossover}, BB={bb_position}",
                    data={"price": current_close, "rsi": rsi, "macd_crossover": macd_crossover},
                    timestamp=datetime.now(timezone.utc),
                )
                
                # Confluence (même avec un seul signal technique)
                confluence = self.confluence_engine.evaluate(symbol, [signal])
                
                if confluence.is_actionable:
                    # Valider avec le risk manager
                    approved, reason = risk_manager.validate_trade(confluence)
                    if not approved:
                        continue
                    
                    # Calculer la taille de position
                    position_size = risk_manager.calculate_position_size(
                        confluence.recommended_size_sol,
                        confluence.confluence_score
                    )
                    
                    if position_size <= 0 or position_size > portfolio.available_sol:
                        continue
                    
                    # Créer le trade (entrée à l'ouverture de la bougie suivante)
                    import uuid
                    next_open = float(candles[min(i+1, len(candles)-1)].get("o", candles[min(i+1, len(candles)-1)].get("open", current_close)))
                    
                    trade = Trade(
                        id=str(uuid.uuid4())[:8],
                        token_address=symbol,
                        token_symbol=symbol,
                        side=confluence.recommended_action,
                        entry_price_sol=next_open,
                        amount_sol=position_size,
                        amount_tokens=position_size / next_open if next_open > 0 else 0,
                        stop_loss_sol=next_open * (1 - confluence.stop_loss_pct / 100),
                        take_profit_sol=next_open * (1 + confluence.take_profit_pct / 100),
                        status=TradeStatus.EXECUTED,
                        confluence_score=confluence.confluence_score,
                        signals=[s.source.value for s in confluence.signals],
                        entry_time=datetime.now(timezone.utc),
                        highest_price_sol=next_open,
                    )
                    
                    open_trades.append(trade)
                    portfolio.available_sol -= position_size
        
        # Fermer les positions restantes au dernier prix
        for trade in open_trades:
            trade.exit_price_sol = closes[-1]
            trade.exit_time = datetime.now(timezone.utc)
            trade.pnl_sol = (closes[-1] - trade.entry_price_sol) * trade.amount_tokens
            trade.pnl_pct = ((closes[-1] - trade.entry_price_sol) / trade.entry_price_sol) * 100
            trade.status = TradeStatus.EXECUTED
            self._update_portfolio_after_close(portfolio, trade)
            all_trades.append(trade)
            equity_curve.append(portfolio.total_sol)
        
        return self._calculate_metrics(all_trades, equity_curve, initial_capital)
    
    def _update_portfolio_after_close(self, portfolio: PortfolioState, trade: Trade):
        """Met à jour le portfolio après la fermeture d'un trade"""
        if trade.pnl_sol and trade.pnl_sol > 0:
            portfolio.daily_wins += 1
            portfolio.consecutive_losses = 0
        else:
            portfolio.daily_losses += 1
            portfolio.consecutive_losses += 1
        
        portfolio.daily_pnl_sol += trade.pnl_sol or 0
        portfolio.daily_trades += 1
        portfolio.available_sol += trade.amount_sol + (trade.pnl_sol or 0)
        portfolio.total_sol = portfolio.available_sol
    
    async def run(
        self,
        historical_signals: Dict[str, List[Signal]],
        initial_capital: float = 10.0
    ) -> BacktestResult:
        """
        Lance un backtest basé sur des signaux historiques (mode legacy).
        
        Pour un backtest plus fiable, utilisez run_on_ohlcv() qui utilise
        de vraies données OHLCV au lieu de prix de sortie simulés.
        
        Args:
            historical_signals: Dictionnaire {token: [signaux historiques]}
            initial_capital: Capital initial en SOL
        """
        logger.info(f"Backtest signaux démarré - Capital: {initial_capital} SOL")
        
        # Initialiser le portfolio virtuel
        portfolio = PortfolioState(
            total_sol=initial_capital,
            available_sol=initial_capital,
        )
        risk_manager = RiskManager(self.config.risk, portfolio)
        
        all_trades: List[Trade] = []
        equity_curve = [initial_capital]
        open_trades: List[Trade] = []
        
        # Traiter chaque token
        for token_address, signals in historical_signals.items():
            # Trier les signaux par timestamp
            sorted_signals = sorted(signals, key=lambda s: s.timestamp)
            
            # Évaluer la confluence pour chaque signal
            for signal in sorted_signals:
                # Collecter les signaux récents pour ce token
                recent = [
                    s for s in sorted_signals
                    if s.token_address == token_address
                    and s.timestamp <= signal.timestamp
                    and (signal.timestamp - s.timestamp).seconds < 300
                ]
                
                # Confluence
                confluence = self.confluence_engine.evaluate(token_address, recent)
                
                if not confluence.is_actionable:
                    continue
                
                # Valider avec le risk manager
                approved, reason = risk_manager.validate_trade(confluence)
                if not approved:
                    continue
                
                # Calculer la taille de position
                position_size = risk_manager.calculate_position_size(
                    confluence.recommended_size_sol,
                    confluence.confluence_score
                )
                
                # Simuler l'entrée
                import uuid
                entry_price = signal.data.get("price", 0)
                
                if entry_price <= 0:
                    continue
                
                trade = Trade(
                    id=str(uuid.uuid4())[:8],
                    token_address=token_address,
                    token_symbol=signal.token_symbol,
                    side=confluence.recommended_action,
                    entry_price_sol=entry_price,
                    amount_sol=position_size,
                    amount_tokens=position_size / entry_price,
                    stop_loss_sol=entry_price * (1 - confluence.stop_loss_pct / 100),
                    take_profit_sol=entry_price * (1 + confluence.take_profit_pct / 100),
                    status=TradeStatus.EXECUTED,
                    confluence_score=confluence.confluence_score,
                    signals=[s.source.value for s in confluence.signals],
                    entry_time=signal.timestamp,
                )
                
                # Simuler la sortie en utilisant le SL/TP (méthode déterministe)
                # On ne peut pas utiliser les bougies ici, on simule donc un scénario
                # où le SL ou TP est touché selon le score de confluence
                if confluence.confluence_score >= 0.7:
                    # Signal fort : TP touché dans 60% des cas
                    import random
                    if random.random() < 0.6:
                        exit_price = trade.take_profit_sol
                    else:
                        exit_price = trade.stop_loss_sol
                else:
                    # Signal moyen : TP touché dans 45% des cas
                    import random
                    if random.random() < 0.45:
                        exit_price = trade.take_profit_sol
                    else:
                        exit_price = trade.stop_loss_sol
                
                trade.exit_price_sol = exit_price
                trade.exit_time = signal.timestamp
                trade.pnl_sol = (exit_price - entry_price) * trade.amount_tokens
                trade.pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                trade.status = TradeStatus.EXECUTED
                
                # Mettre à jour le portfolio
                self._update_portfolio_after_close(portfolio, trade)
                
                all_trades.append(trade)
                equity_curve.append(portfolio.total_sol)
        
        # Calculer les métriques finales
        return self._calculate_metrics(
            all_trades, equity_curve, initial_capital
        )
    
    def _calculate_metrics(
        self,
        trades: List[Trade],
        equity_curve: List[float],
        initial_capital: float
    ) -> BacktestResult:
        """Calcule toutes les métriques de performance"""
        
        if not trades:
            return BacktestResult(
                start_date=datetime.now(timezone.utc),
                end_date=datetime.now(timezone.utc),
                initial_capital_sol=initial_capital,
                final_capital_sol=initial_capital,
                equity_curve=equity_curve,
            )
        
        wins = [t for t in trades if (t.pnl_pct or 0) > 0]
        losses = [t for t in trades if (t.pnl_pct or 0) <= 0]
        
        total_pnl = sum(t.pnl_sol or 0 for t in trades)
        total_pnl_pct = (total_pnl / initial_capital) * 100 if initial_capital > 0 else 0
        
        win_rate = (len(wins) / len(trades) * 100) if trades else 0
        
        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
        
        total_wins_sol = sum(t.pnl_sol for t in wins if t.pnl_sol)
        total_losses_sol = abs(sum(t.pnl_sol for t in losses if t.pnl_sol))
        profit_factor = total_wins_sol / total_losses_sol if total_losses_sol > 0 else float('inf')
        
        best_trade = max((t.pnl_pct or 0 for t in trades), default=0)
        worst_trade = min((t.pnl_pct or 0 for t in trades), default=0)
        
        # Max drawdown
        max_dd = 0.0
        if equity_curve:
            peak = equity_curve[0]
            for value in equity_curve:
                if value > peak:
                    peak = value
                dd = ((peak - value) / peak * 100) if peak > 0 else 0
                max_dd = max(max_dd, dd)
        
        # Sharpe ratio
        try:
            import numpy as np
            if len(trades) > 1:
                returns = [t.pnl_pct / 100 for t in trades if t.pnl_pct is not None]
                if returns:
                    avg_r = np.mean(returns)
                    std_r = np.std(returns)
                    sharpe = (avg_r / std_r * (252 ** 0.5)) if std_r > 0 else 0
                else:
                    sharpe = 0
            else:
                sharpe = 0
        except ImportError:
            sharpe = 0
        
        final_capital = equity_curve[-1] if equity_curve else initial_capital
        
        return BacktestResult(
            start_date=trades[0].entry_time if trades else datetime.now(timezone.utc),
            end_date=trades[-1].exit_time or trades[-1].entry_time if trades else datetime.now(timezone.utc),
            initial_capital_sol=initial_capital,
            final_capital_sol=final_capital,
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=win_rate,
            total_pnl_sol=total_pnl,
            total_pnl_pct=total_pnl_pct,
            max_drawdown_pct=max_dd,
            sharpe_ratio=sharpe,
            profit_factor=profit_factor,
            avg_win_pct=avg_win,
            avg_loss_pct=avg_loss,
            best_trade_pct=best_trade,
            worst_trade_pct=worst_trade,
            trades=trades,
            equity_curve=equity_curve,
        )


def print_backtest_result(result: BacktestResult):
    """Affiche les résultats du backtest de manière lisible"""
    
    print("\n" + "=" * 60)
    print("  SOLARIS - Resultats du Backtest")
    print("=" * 60)
    
    print(f"\n  Capital initial:  {result.initial_capital_sol:.4f} SOL")
    print(f"  Capital final:    {result.final_capital_sol:.4f} SOL")
    print(f"  PnL total:        {result.total_pnl_sol:+.4f} SOL ({result.total_pnl_pct:+.1f}%)")
    
    print(f"\n  Trades totaux:    {result.total_trades}")
    print(f"  Gagnants:         {result.winning_trades}")
    print(f"  Perdants:         {result.losing_trades}")
    print(f"  Win rate:         {result.win_rate:.1f}%")
    
    print(f"\n  Gain moyen:       +{result.avg_win_pct:.1f}%")
    print(f"  Perte moyenne:    {result.avg_loss_pct:.1f}%")
    print(f"  Meilleur trade:   +{result.best_trade_pct:.1f}%")
    print(f"  Pire trade:       {result.worst_trade_pct:.1f}%")
    
    print(f"\n  Profit Factor:    {result.profit_factor:.2f}")
    print(f"  Max Drawdown:     -{result.max_drawdown_pct:.1f}%")
    print(f"  Sharpe Ratio:     {result.sharpe_ratio:.2f}")
    
    # Evaluation
    print(f"\n  {'---'*18}")
    if result.win_rate > 55 and result.profit_factor > 1.5:
        print("  Stratategie POTENTIELLEMENT RENTABLE")
    elif result.win_rate > 45 and result.profit_factor > 1.0:
        print("  Stratategie MARGINALEMENT RENTABLE - A optimiser")
    else:
        print("  Stratategie NON RENTABLE - A revoir")
    
    print("=" * 60)
