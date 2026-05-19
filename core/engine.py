"""
SOLARIS - Core Engine
====================
Moteur principal du système de trading
"""

import asyncio
import logging
import time
from typing import Optional, List
from datetime import datetime, timezone

from config.settings import SolarisConfig, TradingMode
from core.models import (
    Signal, ConfluenceResult, Trade, PortfolioState,
    SignalType, TradeStatus
)
from data.onchain_fetcher import OnChainFetcher
from data.price_feed import PriceFeed
from strategies.whale_tracker import WhaleTracker
from strategies.volume_analyzer import VolumeAnalyzer
from strategies.technical_analyzer import TechnicalAnalyzer
from strategies.token_sniper import TokenSniper
from strategies.confluence_engine import ConfluenceEngine
from risk.manager import RiskManager
from execution.executor import TradeExecutor
from monitoring.dashboard import Dashboard


logger = logging.getLogger("solaris")


class SolarisEngine:
    """
    Moteur principal SOLARIS.
    
    Orchestre toutes les composantes :
    1. Collecte de données on-chain
    2. Analyse par chaque stratégie
    3. Confluence des signaux
    4. Validation des risques
    5. Exécution des trades
    6. Monitoring continu
    """
    
    def __init__(self, config: SolarisConfig):
        self.config = config
        self.running = False
        self.start_time: Optional[datetime] = None
        
        # État du portefeuille
        self.portfolio = PortfolioState()
        
        # Composants données
        self.onchain_fetcher = OnChainFetcher(config.solana)
        self.price_feed = PriceFeed(config.solana)
        
        # Stratégies
        self.whale_tracker = WhaleTracker(config.strategy, self.onchain_fetcher)
        self.volume_analyzer = VolumeAnalyzer(config.strategy, self.onchain_fetcher)
        self.technical_analyzer = TechnicalAnalyzer(config.strategy, self.price_feed)
        self.token_sniper = TokenSniper(config.strategy, self.onchain_fetcher)
        
        # Confluence
        self.confluence_engine = ConfluenceEngine(config.confluence)
        
        # Risque
        self.risk_manager = RiskManager(config.risk, self.portfolio)
        
        # Exécution
        self.executor = TradeExecutor(config.wallet, config.solana, config.mode)
        
        # Monitoring
        self.dashboard = Dashboard(config.monitoring, self.portfolio)
        
        # Historique
        self.signal_history: List[Signal] = []
        self.trade_history: List[Trade] = []
        self.confluence_history: List[ConfluenceResult] = []
    
    async def initialize(self):
        """Initialise toutes les composantes du système"""
        logger.info("=" * 60)
        logger.info("SOLARIS - Initialisation du système")
        logger.info(f"Mode: {self.config.mode.value.upper()}")
        logger.info(f"Risk Level: {self.config.risk.risk_level.value.upper()}")
        logger.info("=" * 60)
        
        try:
            # Connexion RPC
            await self.onchain_fetcher.connect()
            logger.info("[OK] Connexion RPC Solana établie")
            
            # Price feed
            await self.price_feed.start()
            logger.info("[OK] Price feed démarré")
            
            # Charger le portfolio
            initial_sol = getattr(self.config, '_portfolio_initial_sol', 10.0)
            if self.config.mode == TradingMode.LIVE:
                await self._load_portfolio_from_chain()
            else:
                self.portfolio.available_sol = initial_sol
                self.portfolio.total_sol = initial_sol
            logger.info(f"[OK] Portfolio: {self.portfolio.total_sol:.4f} SOL")
            
            # Initialiser les stratégies
            await self.whale_tracker.initialize()
            logger.info("[OK] Whale Tracker initialisé")
            
            await self.volume_analyzer.initialize()
            logger.info("[OK] Volume Analyzer initialisé")
            
            await self.technical_analyzer.initialize()
            logger.info("[OK] Technical Analyzer initialisé")
            
            await self.token_sniper.initialize()
            logger.info("[OK] Token Sniper initialisé")
            
            # Dashboard
            if self.config.monitoring.dashboard_enabled:
                await self.dashboard.start()
                logger.info(f"[OK] Dashboard démarré sur port {self.config.monitoring.dashboard_port}")
            
            logger.info("=" * 60)
            logger.info("SOLARIS - Système prêt !")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"Erreur d'initialisation: {e}")
            raise
    
    async def run(self):
        """Boucle principale du moteur"""
        self.running = True
        self.start_time = datetime.now(timezone.utc)
        
        logger.info("Boucle principale démarrée...")
        
        # Lancer les tâches en parallèle
        tasks = [
            asyncio.create_task(self._data_collection_loop(), name="data_collection"),
            asyncio.create_task(self._whale_tracking_loop(), name="whale_tracking"),
            asyncio.create_task(self._volume_analysis_loop(), name="volume_analysis"),
            asyncio.create_task(self._technical_analysis_loop(), name="technical_analysis"),
            asyncio.create_task(self._token_sniping_loop(), name="token_sniping"),
            asyncio.create_task(self._position_management_loop(), name="position_management"),
            asyncio.create_task(self._monitoring_loop(), name="monitoring"),
        ]
        
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Arrêt du moteur demandé")
        finally:
            await self.shutdown()
    
    async def _data_collection_loop(self):
        """Collecte continue des données on-chain"""
        while self.running:
            try:
                # Mettre à jour les prix de la watchlist
                for symbol in self.config.watchlist:
                    await self.price_feed.update_price(symbol)
                
                await asyncio.sleep(5)  # Toutes les 5 secondes
                
            except Exception as e:
                logger.error(f"Erreur collecte données: {e}")
                await asyncio.sleep(10)
    
    async def _whale_tracking_loop(self):
        """Surveillance continue des baleines"""
        if not self.config.strategy.whale_tracking_enabled:
            return
            
        while self.running:
            try:
                whale_txs = await self.whale_tracker.scan_for_whale_transactions()
                
                for tx in whale_txs:
                    signal = self.whale_tracker.generate_signal(tx)
                    if signal:
                        self.signal_history.append(signal)
                        logger.info(
                            f"[WHALE] {tx.signal_type.value} {tx.amount_sol:.2f} SOL "
                            f"de {tx.wallet_label or tx.wallet_address[:8]}... "
                            f"sur {tx.token_symbol}"
                        )
                        await self._process_signal(signal)
                
                await asyncio.sleep(2)  # Scan toutes les 2 secondes
                
            except Exception as e:
                logger.error(f"Erreur whale tracking: {e}")
                await asyncio.sleep(5)
    
    async def _volume_analysis_loop(self):
        """Analyse continue des volumes DEX"""
        if not self.config.strategy.volume_analysis_enabled:
            return
            
        while self.running:
            try:
                for symbol in self.config.watchlist:
                    volume_data = await self.volume_analyzer.analyze_token(symbol)
                    
                    if volume_data and volume_data.is_spike:
                        signal = self.volume_analyzer.generate_signal(volume_data)
                        if signal:
                            self.signal_history.append(signal)
                            logger.info(
                                f"[VOLUME] Spike {volume_data.volume_ratio:.1f}x "
                                f"sur {symbol} "
                                f"(buy pressure: {volume_data.buy_volume_pct:.0f}%)"
                            )
                            await self._process_signal(signal)
                
                await asyncio.sleep(10)  # Toutes les 10 secondes
                
            except Exception as e:
                logger.error(f"Erreur volume analysis: {e}")
                await asyncio.sleep(15)
    
    async def _technical_analysis_loop(self):
        """Analyse technique continue"""
        if not self.config.strategy.technical_enabled:
            return
            
        while self.running:
            try:
                for symbol in self.config.watchlist:
                    indicators = await self.technical_analyzer.analyze(symbol)
                    
                    if indicators and indicators.technical_signal != SignalType.HOLD:
                        signal = self.technical_analyzer.generate_signal(indicators)
                        if signal:
                            self.signal_history.append(signal)
                            logger.info(
                                f"[TECH] {indicators.technical_signal.value} {symbol} "
                                f"(RSI: {indicators.rsi:.1f}, "
                                f"MACD: {indicators.macd_crossover}, "
                                f"Score: {indicators.technical_score:.2f})"
                            )
                            await self._process_signal(signal)
                
                await asyncio.sleep(30)  # Toutes les 30 secondes
                
            except Exception as e:
                logger.error(f"Erreur technical analysis: {e}")
                await asyncio.sleep(30)
    
    async def _token_sniping_loop(self):
        """Surveillance des nouveaux tokens"""
        if not self.config.strategy.sniping_enabled:
            return
            
        while self.running:
            try:
                new_tokens = await self.token_sniper.scan_new_tokens()
                
                for token_event in new_tokens:
                    signal = self.token_sniper.evaluate_and_signal(token_event)
                    if signal:
                        self.signal_history.append(signal)
                        risk = token_event.risk_level.value
                        logger.info(
                            f"[SNIPE] Nouveau token {token_event.token_symbol} "
                            f"(risk: {risk}, "
                            f"liq: {token_event.initial_liquidity_sol:.1f} SOL, "
                            f"score: {signal.score:.2f})"
                        )
                        await self._process_signal(signal)
                
                await asyncio.sleep(3)  # Scan toutes les 3 secondes
                
            except Exception as e:
                logger.error(f"Erreur token sniping: {e}")
                await asyncio.sleep(5)
    
    async def _position_management_loop(self):
        """Gestion des positions ouvertes (stop-loss, take-profit, trailing)"""
        while self.running:
            try:
                for trade in self.portfolio.open_positions[:]:
                    await self._manage_position(trade)
                
                await asyncio.sleep(2)  # Vérifier toutes les 2 secondes
                
            except Exception as e:
                logger.error(f"Erreur position management: {e}")
                await asyncio.sleep(5)
    
    async def _monitoring_loop(self):
        """Mise à jour du dashboard et métriques"""
        while self.running:
            try:
                # Mettre à jour les stats du portfolio
                self._update_portfolio_stats()
                
                # Mettre à jour le dashboard
                if self.config.monitoring.dashboard_enabled:
                    await self.dashboard.update(self.portfolio, self.signal_history)
                
                await asyncio.sleep(5)
                
            except Exception as e:
                logger.error(f"Erreur monitoring: {e}")
                await asyncio.sleep(10)
    
    async def _process_signal(self, signal: Signal):
        """Traite un signal individuel - le passe au moteur de confluence"""
        # Collecter tous les signaux récents pour ce token
        recent_signals = [
            s for s in self.signal_history[-50:]
            if s.token_address == signal.token_address
            and (datetime.now(timezone.utc) - s.timestamp).seconds < 300  # 5 min window
        ]
        
        # Calculer la confluence
        confluence = self.confluence_engine.evaluate(signal.token_address, recent_signals)
        self.confluence_history.append(confluence)
        
        if confluence.is_actionable:
            logger.info(
                f"[CONFLUENCE] {confluence.recommended_action.value} "
                f"{confluence.token_symbol} "
                f"(score: {confluence.confluence_score:.2f}, "
                f"signaux: {len(confluence.signals)})"
            )
            await self._execute_if_approved(confluence)
    
    async def _execute_if_approved(self, confluence: ConfluenceResult):
        """Vérifie les risques et exécute si approuvé"""
        # Vérification des risques
        approved, reason = self.risk_manager.validate_trade(confluence)
        
        if not approved:
            logger.info(f"[RISK] Trade refusé: {reason}")
            return
        
        # Calculer la taille de position
        position_size = self.risk_manager.calculate_position_size(
            confluence.recommended_size_sol,
            confluence.confluence_score
        )
        
        # Exécuter le trade
        if self.config.mode == TradingMode.PAPER:
            trade = self._simulate_trade(confluence, position_size)
        else:
            trade = await self.executor.execute(confluence, position_size)
        
        if trade:
            self.trade_history.append(trade)
            self.portfolio.open_positions.append(trade)
            logger.info(
                f"[TRADE] {'PAPER ' if self.config.mode == TradingMode.PAPER else ''}"
                f"{trade.side.value} {trade.token_symbol} "
                f"{trade.amount_sol:.4f} SOL "
                f"@ {trade.entry_price_sol:.8f} SOL "
                f"(SL: {trade.stop_loss_pct:.1f}%, "
                f"TP: {trade.take_profit_pct:.1f}%)"
            )
    
    def _simulate_trade(self, confluence: ConfluenceResult, position_size: float) -> Trade:
        """Simule un trade en paper trading"""
        import uuid
        
        current_price = self.price_feed.get_price(confluence.token_symbol) or 0.0001
        
        return Trade(
            id=str(uuid.uuid4())[:8],
            token_address=confluence.token_address,
            token_symbol=confluence.token_symbol,
            side=confluence.recommended_action,
            entry_price_sol=current_price,
            amount_sol=position_size,
            amount_tokens=position_size / current_price if current_price > 0 else 0,
            stop_loss_sol=current_price * (1 - confluence.stop_loss_pct / 100),
            take_profit_sol=current_price * (1 + confluence.take_profit_pct / 100),
            status=TradeStatus.EXECUTED,
            confluence_score=confluence.confluence_score,
            signals=[s.source.value for s in confluence.signals],
        )
    
    async def _manage_position(self, trade: Trade):
        """Gère une position ouverte (stop-loss, take-profit, trailing stop)"""
        current_price = self.price_feed.get_price(trade.token_symbol)
        if not current_price:
            return
        
        # Mise à jour du prix le plus haut (pour trailing stop)
        if trade.highest_price_sol is None or current_price > trade.highest_price_sol:
            trade.highest_price_sol = current_price
        
        # Calculer le PnL actuel
        current_pnl_pct = ((current_price - trade.entry_price_sol) / trade.entry_price_sol) * 100
        
        # Vérifier stop-loss
        if current_price <= trade.stop_loss_sol:
            await self._close_position(trade, current_price, "STOP_LOSS")
            return
        
        # Vérifier take-profit
        if current_price >= trade.take_profit_sol:
            await self._close_position(trade, current_price, "TAKE_PROFIT")
            return
        
        # Trailing stop
        if self.config.risk.trailing_stop_enabled and trade.highest_price_sol:
            activation_price = trade.entry_price_sol * (1 + self.config.risk.trailing_stop_activation_pct / 100)
            
            if current_price >= activation_price:
                trailing_distance = trade.highest_price_sol * (self.config.risk.trailing_stop_distance_pct / 100)
                trailing_stop = trade.highest_price_sol - trailing_distance
                
                # Mettre à jour le trailing stop si plus élevé
                if trade.trailing_stop_sol is None or trailing_stop > trade.trailing_stop_sol:
                    trade.trailing_stop_sol = trailing_stop
                
                # Vérifier si le trailing stop est touché
                if trade.trailing_stop_sol and current_price <= trade.trailing_stop_sol:
                    await self._close_position(trade, current_price, "TRAILING_STOP")
                    return
    
    async def _close_position(self, trade: Trade, exit_price: float, reason: str):
        """Ferme une position"""
        trade.exit_price_sol = exit_price
        trade.exit_time = datetime.now(timezone.utc)
        trade.pnl_sol = (exit_price - trade.entry_price_sol) * trade.amount_tokens
        trade.pnl_pct = ((exit_price - trade.entry_price_sol) / trade.entry_price_sol) * 100
        
        # Mettre à jour le portfolio
        if trade.pnl_sol and trade.pnl_sol > 0:
            self.portfolio.daily_wins += 1
            self.portfolio.consecutive_losses = 0
        else:
            self.portfolio.daily_losses += 1
            self.portfolio.consecutive_losses += 1
        
        self.portfolio.daily_pnl_sol += trade.pnl_sol or 0
        self.portfolio.daily_trades += 1
        self.portfolio.available_sol += trade.amount_sol + (trade.pnl_sol or 0)
        
        # Retirer des positions ouvertes
        if trade in self.portfolio.open_positions:
            self.portfolio.open_positions.remove(trade)
        
        emoji = "+" if (trade.pnl_pct or 0) > 0 else ""
        logger.info(
            f"[CLOSE] {reason} {trade.token_symbol} "
            f"PnL: {emoji}{trade.pnl_pct:.1f}% "
            f"({emoji}{trade.pnl_sol:.4f} SOL)"
        )
    
    def _update_portfolio_stats(self):
        """Met à jour les statistiques globales du portfolio"""
        all_trades = self.trade_history
        closed_trades = [t for t in all_trades if t.exit_time is not None]
        
        if closed_trades:
            wins = [t for t in closed_trades if (t.pnl_pct or 0) > 0]
            losses = [t for t in closed_trades if (t.pnl_pct or 0) <= 0]
            
            self.portfolio.total_trades = len(closed_trades)
            self.portfolio.total_wins = len(wins)
            self.portfolio.win_rate = len(wins) / len(closed_trades) * 100 if closed_trades else 0
            self.portfolio.avg_win_pct = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
            self.portfolio.avg_loss_pct = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
            
            total_wins_sol = sum(t.pnl_sol for t in wins if t.pnl_sol) 
            total_losses_sol = abs(sum(t.pnl_sol for t in losses if t.pnl_sol))
            self.portfolio.profit_factor = total_wins_sol / total_losses_sol if total_losses_sol > 0 else float('inf')
            
            self.portfolio.total_pnl_sol = sum(t.pnl_sol for t in closed_trades if t.pnl_sol)
            self.portfolio.total_pnl_pct = (self.portfolio.total_pnl_sol / self.portfolio.total_sol * 100) if self.portfolio.total_sol > 0 else 0
    
    async def _load_portfolio_from_chain(self):
        """Charge le solde du wallet depuis la blockchain"""
        if self.config.wallet.public_key:
            balance = await self.onchain_fetcher.get_sol_balance(self.config.wallet.public_key)
            self.portfolio.total_sol = balance
            self.portfolio.available_sol = balance
    
    async def shutdown(self):
        """Arrêt propre du système"""
        self.running = False
        logger.info("Arrêt de SOLARIS...")
        
        # Fermer les connexions
        await self.onchain_fetcher.disconnect()
        await self.price_feed.stop()
        
        if self.config.monitoring.dashboard_enabled:
            await self.dashboard.stop()
        
        # Résumé final
        self._update_portfolio_stats()
        logger.info(f"Trades totaux: {self.portfolio.total_trades}")
        logger.info(f"Win rate: {self.portfolio.win_rate:.1f}%")
        logger.info(f"PnL total: {self.portfolio.total_pnl_sol:.4f} SOL ({self.portfolio.total_pnl_pct:.1f}%)")
        logger.info("SOLARIS arrêté.")
