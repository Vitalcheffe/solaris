"""
SOLARIS - Confluence Engine
===========================
Moteur de confluence : combine les signaux de toutes les stratégies
pour produire une décision de trading pondérée.
"""

import logging
from typing import List, Optional
from datetime import datetime

from config.settings import ConfluenceConfig
from core.models import (
    Signal, ConfluenceResult, SignalType, SignalSource
)

logger = logging.getLogger("solaris.confluence")


class ConfluenceEngine:
    """
    Moteur de confluence - le cerveau du système.
    
    Philosophie :
    Un signal seul peut être du bruit. Mais quand plusieurs sources
    indépendantes disent la même chose, la probabilité d'un bon trade
    augmente significativement.
    
    C'est comme un diagnostic médical : un symptôme seul ne suffit pas,
    mais quand 3 médecins différents arrivent à la même conclusion,
    la confiance est beaucoup plus élevée.
    
    Comment ça marche :
    1. Collecte tous les signaux récents pour un token
    2. Vérifie s'ils sont alignés dans la même direction
    3. Pondère chaque signal par sa source (whale > technique)
    4. Calcule un score de confluence global
    5. Ne recommande un trade que si le score dépasse le seuil
    
    Le scoring est conservateur : il vaut mieux rater un bon trade
    que d'entrer dans un mauvais.
    """
    
    def __init__(self, config: ConfluenceConfig):
        self.config = config
    
    def evaluate(self, token_address: str, signals: List[Signal]) -> ConfluenceResult:
        """
        Évalue la confluence des signaux pour un token.
        
        Returns: ConfluenceResult avec la recommandation
        """
        if not signals:
            return self._no_signal_result(token_address)
        
        # Séparer les signaux par direction
        buy_signals = [s for s in signals if s.signal_type == SignalType.BUY]
        sell_signals = [s for s in signals if s.signal_type == SignalType.SELL]
        
        # Calculer le score pondéré pour chaque direction
        buy_score = self._calculate_weighted_score(buy_signals)
        sell_score = self._calculate_weighted_score(sell_signals)
        
        # Déterminer la direction dominante
        if buy_score > sell_score and buy_score >= self.config.min_confluence_score:
            recommended_action = SignalType.BUY
            confluence_score = buy_score
            active_signals = buy_signals
        elif sell_score > buy_score and sell_score >= self.config.min_confluence_score:
            recommended_action = SignalType.SELL
            confluence_score = sell_score
            active_signals = sell_signals
        else:
            recommended_action = SignalType.HOLD
            confluence_score = max(buy_score, sell_score)
            active_signals = buy_signals if buy_score > sell_score else sell_signals
        
        # Vérifier le nombre minimum de signaux
        unique_sources = set(s.source for s in active_signals)
        if len(unique_sources) < self.config.min_signals_for_entry:
            if recommended_action != SignalType.HOLD:
                recommended_action = SignalType.HOLD
                confluence_score *= 0.5  # Réduire le score
        
        # Calculer les niveaux recommandés
        stop_loss_pct, take_profit_pct = self._calculate_levels(
            confluence_score, recommended_action
        )
        
        # Calculer la taille de position recommandée
        recommended_size = self._calculate_position_size(confluence_score)
        
        # Rassembler les raisons
        reasons = [s.reason for s in active_signals]
        
        # Déterminer le symbole
        token_symbol = active_signals[0].token_symbol if active_signals else "UNKNOWN"
        
        return ConfluenceResult(
            token_address=token_address,
            token_symbol=token_symbol,
            signals=active_signals,
            confluence_score=confluence_score,
            recommended_action=recommended_action,
            recommended_size_sol=recommended_size,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            reasons=reasons,
            timestamp=datetime.utcnow(),
        )
    
    def _calculate_weighted_score(self, signals: List[Signal]) -> float:
        """
        Calcule le score pondéré d'un ensemble de signaux.
        
        Chaque source a un poids différent (configurable) :
        - Whale tracking: 35% (le plus fiable)
        - Volume analysis: 25%
        - Technical: 20%
        - Smart money: 15%
        - New token: 5% (le moins fiable seul)
        """
        if not signals:
            return 0.0
        
        total_weighted_score = 0.0
        total_weight = 0.0
        
        for signal in signals:
            source_weight = self.config.signal_weights.get(signal.source.value, 0.1)
            
            # Le score du signal * le poids de sa source
            weighted = signal.score * source_weight
            total_weighted_score += weighted
            total_weight += source_weight
        
        # Normaliser par rapport au poids total possible
        # Score max possible = somme de tous les poids = 1.0
        # Donc le score est déjà normalisé entre 0 et 1
        
        # Bonus pour diversité des sources
        unique_sources = set(s.source for s in signals)
        if len(unique_sources) >= 3:
            total_weighted_score *= 1.15  # +15% pour 3+ sources
        elif len(unique_sources) >= 2:
            total_weighted_score *= 1.05  # +5% pour 2 sources
        
        return min(total_weighted_score, 1.0)
    
    def _calculate_levels(
        self, 
        confluence_score: float, 
        action: SignalType
    ) -> tuple:
        """
        Calcule les niveaux de stop-loss et take-profit.
        
        Logique :
        - Plus la confluence est forte, plus le take-profit peut être ambitieux
        - Le stop-loss est toujours calculé pour limiter la perte à un montant raisonnable
        - Ratio risque/récompense minimum de 1:2
        """
        if action == SignalType.BUY:
            # Stop loss basé sur la volatilité estimée et le score
            if confluence_score >= 0.8:
                stop_loss_pct = 3.0   # Signal très fort, SL serré
                take_profit_pct = 15.0
            elif confluence_score >= 0.6:
                stop_loss_pct = 5.0   # Signal moyen
                take_profit_pct = 10.0
            else:
                stop_loss_pct = 7.0   # Signal faible, SL large
                take_profit_pct = 10.0
            
            # Ajuster le ratio risque/récompense
            if take_profit_pct / stop_loss_pct < 2.0:
                take_profit_pct = stop_loss_pct * 2.0
        
        elif action == SignalType.SELL:
            stop_loss_pct = 5.0
            take_profit_pct = 10.0
        
        else:
            stop_loss_pct = 5.0
            take_profit_pct = 10.0
        
        return stop_loss_pct, take_profit_pct
    
    def _calculate_position_size(self, confluence_score: float) -> float:
        """
        Calcule la taille de position recommandée en SOL.
        
        Plus la confluence est forte, plus la position peut être grande.
        Mais toujours dans les limites du risk management.
        """
        # Taille de base proportionnelle au score
        base_size = 0.05  # 0.05 SOL minimum
        
        # Augmenter avec la confiance
        if confluence_score >= 0.8:
            recommended = 0.2   # 0.2 SOL pour les signaux très forts
        elif confluence_score >= 0.6:
            recommended = 0.1   # 0.1 SOL pour les signaux moyens
        else:
            recommended = 0.05  # 0.05 SOL pour les signaux faibles
        
        return recommended
    
    def _no_signal_result(self, token_address: str) -> ConfluenceResult:
        """Résultat quand il n'y a pas de signaux"""
        return ConfluenceResult(
            token_address=token_address,
            token_symbol="UNKNOWN",
            signals=[],
            confluence_score=0.0,
            recommended_action=SignalType.HOLD,
            recommended_size_sol=0.0,
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
            reasons=["Aucun signal détecté"],
            timestamp=datetime.utcnow(),
        )
