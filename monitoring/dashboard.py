"""
SOLARIS - Dashboard & Monitoring
================================
Dashboard temps réel pour surveiller le système.
"""

import asyncio
import logging
from typing import Optional, List
from datetime import datetime

from config.settings import MonitoringConfig
from core.models import PortfolioState, Signal

logger = logging.getLogger("solaris.dashboard")


class Dashboard:
    """
    Dashboard de monitoring pour SOLARIS.
    
    Affiche en temps réel :
    - État du portefeuille
    - Positions ouvertes
    - Signaux récents
    - PnL
    - Statistiques de performance
    
    En mode CLI, affiche dans le terminal.
    En mode web, sert une page HTTP avec rafraîchissement auto.
    """
    
    def __init__(self, config: MonitoringConfig, portfolio: PortfolioState):
        self.config = config
        self.portfolio = portfolio
        self._web_server = None
    
    async def start(self):
        """Démarre le dashboard"""
        if self.config.dashboard_enabled:
            # Le dashboard web sera optionnel
            # Pour l'instant, on utilise le dashboard CLI
            logger.info(f"Dashboard CLI activé (port web: {self.config.dashboard_port})")
    
    async def stop(self):
        """Arrête le dashboard"""
        pass
    
    async def update(self, portfolio: PortfolioState, recent_signals: List[Signal]):
        """Met à jour l'affichage du dashboard"""
        self.portfolio = portfolio
        
        # Affichage CLI toutes les 30 secondes
        self._render_cli_dashboard(recent_signals)
    
    def _render_cli_dashboard(self, recent_signals: List[Signal]):
        """Affiche le dashboard dans le terminal"""
        now = datetime.utcnow().strftime("%H:%M:%S")
        
        # Portfolio
        total_sol = self.portfolio.total_sol
        available_sol = self.portfolio.available_sol
        invested = sum(t.amount_sol for t in self.portfolio.open_positions)
        daily_pnl = self.portfolio.daily_pnl_sol
        total_pnl = self.portfolio.total_pnl_sol
        
        # Win rate
        win_rate = self.portfolio.win_rate
        
        # Positions ouvertes
        open_count = len(self.portfolio.open_positions)
        
        # Signaux récents
        signal_count = len(recent_signals)
        
        # Rendu
        lines = [
            "",
            f"{'='*60}",
            f"  SOLARIS Dashboard - {now}",
            f"{'='*60}",
            f"  Portfolio: {total_sol:.4f} SOL | Disponible: {available_sol:.4f} SOL",
            f"  Investi: {invested:.4f} SOL | Positions: {open_count}",
            f"  PnL Jour: {daily_pnl:+.4f} SOL | PnL Total: {total_pnl:+.4f} SOL",
            f"  Win Rate: {win_rate:.1f}% | Trades: {self.portfolio.total_trades}",
            f"  Pertes consécutives: {self.portfolio.consecutive_losses}",
        ]
        
        # Positions ouvertes
        if self.portfolio.open_positions:
            lines.append(f"  {'─'*56}")
            lines.append("  Positions ouvertes:")
            for trade in self.portfolio.open_positions[:5]:
                pnl_str = f"{trade.pnl_pct:+.1f}%" if trade.pnl_pct else "N/A"
                lines.append(
                    f"    {trade.side.value} {trade.token_symbol} "
                    f"{trade.amount_sol:.4f} SOL "
                    f"| PnL: {pnl_str}"
                )
        
        # Derniers signaux
        if recent_signals:
            lines.append(f"  {'─'*56}")
            lines.append(f"  Derniers signaux ({signal_count} total):")
            for sig in recent_signals[-5:]:
                lines.append(
                    f"    [{sig.source.value}] {sig.signal_type.value} "
                    f"{sig.token_symbol} (score: {sig.score:.2f})"
                )
        
        lines.append(f"{'='*60}")
        
        logger.info("\n".join(lines))
