"""
SOLARIS - Volume Analyzer Strategy
==================================
Analyse des volumes DEX pour détecter les pics et la pression acheteuse/vendeuse.
"""

import asyncio
import logging
from typing import Optional, List
from datetime import datetime

from config.settings import StrategyConfig
from core.models import VolumeData, Signal, SignalType, SignalSource
from data.onchain_fetcher import OnChainFetcher

logger = logging.getLogger("solaris.volume")


class VolumeAnalyzer:
    """
    Stratégie d'analyse de volume.
    
    Principes clés :
    1. Un pic de volume = quelque chose se passe (news, whale, manipulation)
    2. Volume + pression acheteuse = signal haussier fort
    3. Volume + pression vendeuse = signal baissier
    4. Volume sans direction = indécision (pas de signal)
    
    Le volume est le carburant du marché. Sans volume, un mouvement de prix
    n'est pas credible. Avec volume, il est beaucoup plus probable de se prolonger.
    """
    
    def __init__(self, config: StrategyConfig, fetcher: OnChainFetcher):
        self.config = config
        self.fetcher = fetcher
        
        # Cache des volumes moyens
        self._volume_averages: dict = {}  # symbol -> average_volume_sol
        self._volume_history: dict = {}   # symbol -> list of recent volumes
    
    async def initialize(self):
        """Initialise l'analyseur de volume"""
        logger.info(
            f"Volume Analyzer: spike threshold = {self.config.volume_spike_threshold}x, "
            f"lookback = {self.config.volume_lookback_hours}h"
        )
    
    async def analyze_token(self, symbol: str) -> Optional[VolumeData]:
        """
        Analyse le volume d'un token et retourne les métriques.
        
        Calcule :
        - Volume actuel vs volume moyen (ratio)
        - Changement de volume sur 1h, 4h, 24h
        - Pression acheteuse vs vendeuse
        - Nombre d'acheteurs/vendeurs uniques
        """
        try:
            # Récupérer les données de volume via l'API
            volume_data = await self.fetcher.get_token_volume(symbol, "24h")
            
            if not volume_data:
                # Fallback: estimer à partir des trades DEX
                return await self._estimate_volume_from_trades(symbol)
            
            # Calculer le volume moyen
            current_volume = float(volume_data.get("volume", 0))
            avg_volume = self._volume_averages.get(symbol, current_volume)
            
            # Mettre à jour la moyenne mobile
            self._update_volume_average(symbol, current_volume)
            
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
            
            # Récupérer les trades récents pour la pression acheteur/vendeur
            trades = await self.fetcher.get_dex_trades(symbol, limit=50)
            buy_volume_pct, sell_volume_pct, unique_buyers, unique_sellers = \
                self._analyze_trade_pressure(trades)
            
            return VolumeData(
                token_address=symbol,
                token_symbol=symbol,
                current_volume_sol=current_volume,
                average_volume_sol=avg_volume,
                volume_ratio=volume_ratio,
                volume_change_1h_pct=float(volume_data.get("change1h", 0)),
                volume_change_4h_pct=float(volume_data.get("change4h", 0)),
                volume_change_24h_pct=float(volume_data.get("change24h", 0)),
                buy_volume_pct=buy_volume_pct,
                sell_volume_pct=sell_volume_pct,
                unique_buyers_1h=unique_buyers,
                unique_sellers_1h=unique_sellers,
                timestamp=datetime.utcnow(),
            )
            
        except Exception as e:
            logger.debug(f"Erreur analyse volume {symbol}: {e}")
            return None
    
    async def _estimate_volume_from_trades(self, symbol: str) -> Optional[VolumeData]:
        """Estime le volume à partir des trades DEX quand l'API volume n'est pas dispo"""
        trades = await self.fetcher.get_dex_trades(symbol, limit=100)
        
        if not trades:
            return None
        
        total_volume = sum(float(t.get("amount", 0)) for t in trades)
        buy_volume_pct, sell_volume_pct, unique_buyers, unique_sellers = \
            self._analyze_trade_pressure(trades)
        
        avg_volume = self._volume_averages.get(symbol, total_volume)
        self._update_volume_average(symbol, total_volume)
        
        return VolumeData(
            token_address=symbol,
            token_symbol=symbol,
            current_volume_sol=total_volume,
            average_volume_sol=avg_volume,
            volume_ratio=total_volume / avg_volume if avg_volume > 0 else 1.0,
            volume_change_1h_pct=0,
            volume_change_4h_pct=0,
            volume_change_24h_pct=0,
            buy_volume_pct=buy_volume_pct,
            sell_volume_pct=sell_volume_pct,
            unique_buyers_1h=unique_buyers,
            unique_sellers_1h=unique_sellers,
            timestamp=datetime.utcnow(),
        )
    
    def _analyze_trade_pressure(self, trades: List[dict]) -> tuple:
        """
        Analyse la pression acheteuse/vendeuse dans une liste de trades.
        
        Returns: (buy_pct, sell_pct, unique_buyers, unique_sellers)
        """
        if not trades:
            return 50.0, 50.0, 0, 0
        
        buy_volume = 0.0
        sell_volume = 0.0
        buyers = set()
        sellers = set()
        
        for trade in trades:
            side = trade.get("side", "").upper()
            amount = float(trade.get("amount", 0))
            wallet = trade.get("owner", "")
            
            if side == "BUY":
                buy_volume += amount
                if wallet:
                    buyers.add(wallet)
            elif side == "SELL":
                sell_volume += amount
                if wallet:
                    sellers.add(wallet)
        
        total = buy_volume + sell_volume
        buy_pct = (buy_volume / total * 100) if total > 0 else 50.0
        sell_pct = 100 - buy_pct
        
        return buy_pct, sell_pct, len(buyers), len(sellers)
    
    def _update_volume_average(self, symbol: str, current_volume: float):
        """Met à jour la moyenne mobile du volume"""
        if symbol not in self._volume_history:
            self._volume_history[symbol] = []
        
        self._volume_history[symbol].append(current_volume)
        
        # Garder les 24 dernières entrées (1 par heure)
        if len(self._volume_history[symbol]) > 24:
            self._volume_history[symbol] = self._volume_history[symbol][-24:]
        
        # Calculer la moyenne
        self._volume_averages[symbol] = sum(self._volume_history[symbol]) / len(self._volume_history[symbol])
    
    def generate_signal(self, volume_data: VolumeData) -> Optional[Signal]:
        """
        Génère un signal basé sur l'analyse du volume.
        
        Scoring :
        - Volume spike (> 3x moyenne): base 0.4
        - Volume spike (> 5x moyenne): base 0.5
        - Pression acheteuse forte (> 70%): +0.2
        - Pression acheteuse (> 80%): +0.15
        - Acheteurs uniques élevés: +0.1
        - Volume spike + achat = signal fort
        """
        if not volume_data.is_spike:
            return None
        
        # Score de base selon l'intensité du spike
        if volume_data.volume_ratio >= 5.0:
            base_score = 0.5
        elif volume_data.volume_ratio >= 3.0:
            base_score = 0.4
        else:
            return None
        
        # Déterminer la direction
        if volume_data.is_buy_pressure:
            signal_type = SignalType.BUY
            
            # Bonus pression acheteuse
            if volume_data.buy_volume_pct > 80:
                base_score += 0.15
            elif volume_data.buy_volume_pct > 70:
                base_score += 0.1
            
            # Bonus acheteurs uniques
            if volume_data.unique_buyers_1h > 50:
                base_score += 0.1
            elif volume_data.unique_buyers_1h > 20:
                base_score += 0.05
        else:
            signal_type = SignalType.SELL
            
            if volume_data.sell_volume_pct > 80:
                base_score += 0.15
            elif volume_data.sell_volume_pct > 70:
                base_score += 0.1
        
        # Cap at 1.0
        score = min(base_score, 1.0)
        
        if score < 0.4:
            return None
        
        direction = "achat" if signal_type == SignalType.BUY else "vente"
        reason = (
            f"Volume spike {volume_data.volume_ratio:.1f}x sur {volume_data.token_symbol} "
            f"- Pression {direction}: "
            f"{volume_data.buy_volume_pct:.0f}% buy / {volume_data.sell_volume_pct:.0f}% sell "
            f"({volume_data.unique_buyers_1h} acheteurs uniques)"
        )
        
        return Signal(
            source=SignalSource.VOLUME_ANALYSIS,
            signal_type=signal_type,
            token_address=volume_data.token_address,
            token_symbol=volume_data.token_symbol,
            score=score,
            reason=reason,
            data={
                "volume_ratio": volume_data.volume_ratio,
                "buy_pct": volume_data.buy_volume_pct,
                "unique_buyers": volume_data.unique_buyers_1h,
            }
        )
