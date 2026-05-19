"""
SOLARIS - Backtesting Engine
============================
Moteur de backtesting pour tester les stratégies sur données historiques.
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
    
    Simule l'exécution des stratégies sur des données historiques
    pour évaluer leur performance avant de risquer de l'argent réel.
    
    Usage:
        backtester = Backtester(config)
        result = await backtester.run(historical_data)
        print(f"Win rate: {result.win_rate:.1f}%")
        print(f"Total PnL: {result.total_pnl_pct:.1f}%")
    """
    
    def __init__(self, config: SolarisConfig):
        self.config = config
        self.confluence_engine = ConfluenceEngine(config.confluence)
    
    async def run(
        self,
        historical_signals: Dict[str, List[Signal]],
        initial_capital: float = 10.0
    ) -> BacktestResult:
        """
        Lance un backtest complet.
        
        Args:
            historical_signals: Dictionnaire {token: [signaux historiques]}
            initial_capital: Capital initial en SOL
        
        Returns:
            BacktestResult avec toutes les métriques
        """
        logger.info(f"Backtest démarré - Capital: {initial_capital} SOL")
        
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
                entry_price = signal.data.get("price", 0.001)
                
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
                
                open_trades.append(trade)
                portfolio.available_sol -= position_size
                
                # Simuler la sortie (simplifié: prix de sortie = prix du prochain signal)
                # Dans un vrai backtest, on utiliserait les bougies OHLCV
                exit_price = self._simulate_exit_price(
                    entry_price, confluence
                )
                
                # Calculer le PnL
                trade.exit_price_sol = exit_price
                trade.exit_time = signal.timestamp
                trade.pnl_sol = (exit_price - entry_price) * trade.amount_tokens
                trade.pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                trade.status = TradeStatus.EXECUTED
                
                # Mettre à jour le portfolio
                if trade.pnl_sol and trade.pnl_sol > 0:
                    portfolio.daily_wins += 1
                    portfolio.consecutive_losses = 0
                else:
                    portfolio.daily_losses += 1
                    portfolio.consecutive_losses += 1
                
                portfolio.available_sol += position_size + (trade.pnl_sol or 0)
                portfolio.total_sol = portfolio.available_sol
                
                all_trades.append(trade)
                equity_curve.append(portfolio.total_sol)
                
                open_trades.remove(trade)
        
        # Calculer les métriques finales
        return self._calculate_metrics(
            all_trades, equity_curve, initial_capital
        )
    
    def _simulate_exit_price(
        self, 
        entry_price: float, 
        confluence: ConfluenceResult
    ) -> float:
        """
        Simule un prix de sortie réaliste.
        
        En l'absence de données OHLCV détaillées, on simule
        en fonction du score de confluence et du hasard.
        """
        import random
        
        # Probabilité de gain basée sur la confluence
        win_probability = min(0.3 + confluence.confluence_score * 0.4, 0.7)
        
        if random.random() < win_probability:
            # Gain
            avg_win = confluence.take_profit_pct / 100
            multiplier = 1 + random.uniform(0.01, avg_win)
        else:
            # Perte
            avg_loss = confluence.stop_loss_pct / 100
            multiplier = 1 - random.uniform(0.01, avg_loss)
        
        return entry_price * multiplier
    
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
    print("  SOLARIS - Résultats du Backtest")
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
    
    # Évaluation
    print(f"\n  {'─'*56}")
    if result.win_rate > 55 and result.profit_factor > 1.5:
        print("  ✅ Stratatégie POTENTIELLEMENT RENTABLE")
    elif result.win_rate > 45 and result.profit_factor > 1.0:
        print("  ⚠️  Stratatégie MARGINALEMENT RENTABLE - À optimiser")
    else:
        print("  ❌ Stratatégie NON RENTABLE - À revoir")
    
    print("=" * 60)
