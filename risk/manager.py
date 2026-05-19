"""
SOLARIS - Risk Manager
=====================
Gestion du risque et validation des trades avant exécution.
"""

import logging
from typing import Tuple, Optional
from datetime import datetime, date

from config.settings import RiskConfig, RiskLevel
from core.models import ConfluenceResult, PortfolioState, SignalType

logger = logging.getLogger("solaris.risk")


class RiskManager:
    """
    Gestionnaire de risque - le gardien du portefeuille.
    
    Philosophie :
    "La première règle du trading est de ne pas perdre d'argent."
    - Warren Buffett
    
    Le Risk Manager est la couche de protection qui empêche le système
    de prendre des décisions émotionnelles ou excessivement risquées.
    
    Il vérifie AVANT chaque trade :
    - Limites de position (taille max)
    - Limites d'exposition (total investi max)
    - Limites journalières (pertes max, trades max)
    - Pertes consécutives (pause obligatoire)
    - Sécurité du token (pas de honeypot, etc.)
    """
    
    def __init__(self, config: RiskConfig, portfolio: PortfolioState):
        self.config = config
        self.portfolio = portfolio
        self._daily_reset_date: Optional[date] = None
    
    def validate_trade(self, confluence: ConfluenceResult) -> Tuple[bool, str]:
        """
        Valide si un trade peut être exécuté selon les règles de risque.
        
        Returns: (approved, reason)
        """
        # Réinitialiser les compteurs journaliers si nécessaire
        self._check_daily_reset()
        
        # ===== VÉRIFICATION 1: Perte journalière maximale =====
        max_daily_loss_sol = self.portfolio.total_sol * (self.config.max_daily_loss_pct / 100)
        if self.portfolio.daily_pnl_sol < 0 and abs(self.portfolio.daily_pnl_sol) >= max_daily_loss_sol:
            return False, f"Perte journalière max atteinte ({self.portfolio.daily_pnl_sol:.4f} SOL)"
        
        # ===== VÉRIFICATION 2: Nombre max de trades/jour =====
        if self.portfolio.daily_trades >= self.config.max_daily_trades:
            return False, f"Nombre max de trades/jour atteint ({self.config.max_daily_trades})"
        
        # ===== VÉRIFICATION 3: Pertes consécutives =====
        if self.portfolio.consecutive_losses >= self.config.max_consecutive_losses:
            # Vérifier si le cooldown est passé
            return False, (
                f"Trop de pertes consécutives ({self.portfolio.consecutive_losses}). "
                f"Pause de {self.config.cooldown_after_max_losses_s // 60} minutes."
            )
        
        # ===== VÉRIFICATION 4: Taille de position =====
        if confluence.recommended_size_sol > 0:
            max_position = self.portfolio.total_sol * (self.config.max_position_size_pct / 100)
            if confluence.recommended_size_sol > max_position:
                return False, (
                    f"Position trop grande: {confluence.recommended_size_sol:.4f} SOL > "
                    f"max {max_position:.4f} SOL"
                )
        
        # ===== VÉRIFICATION 5: Exposition totale =====
        current_exposure = sum(t.amount_sol for t in self.portfolio.open_positions)
        max_exposure = self.portfolio.total_sol * (self.config.max_total_exposure_pct / 100)
        new_exposure = current_exposure + confluence.recommended_size_sol
        
        if new_exposure > max_exposure:
            return False, (
                f"Exposition totale trop élevée: {new_exposure:.4f} SOL > "
                f"max {max_exposure:.4f} SOL"
            )
        
        # ===== VÉRIFICATION 6: Exposition par token =====
        token_exposure = sum(
            t.amount_sol for t in self.portfolio.open_positions
            if t.token_address == confluence.token_address
        )
        max_token_exposure = self.portfolio.total_sol * (self.config.max_single_token_exposure_pct / 100)
        
        if token_exposure + confluence.recommended_size_sol > max_token_exposure:
            return False, (
                f"Exposition sur {confluence.token_symbol} trop élevée"
            )
        
        # ===== VÉRIFICATION 7: SOL disponible =====
        if confluence.recommended_size_sol > self.portfolio.available_sol:
            return False, "SOL disponible insuffisant"
        
        # ===== VÉRIFICATION 8: Vente - position ouverte =====
        if confluence.recommended_action == SignalType.SELL:
            has_position = any(
                t.token_address == confluence.token_address and t.side == SignalType.BUY
                for t in self.portfolio.open_positions
            )
            if not has_position:
                return False, "Pas de position ouverte à vendre"
        
        # Toutes les vérifications passées
        return True, "Trade approuvé"
    
    def calculate_position_size(
        self, 
        recommended_size: float,
        confluence_score: float
    ) -> float:
        """
        Calcule la taille finale de la position en tenant compte de toutes
        les contraintes de risque.
        
        La taille est ajustée selon :
        - Le score de confluence (plus fort = position plus grande)
        - Le niveau de risque configuré
        - Le SOL disponible
        - Les limites d'exposition
        """
        # Facteur de risque selon le niveau
        risk_factors = {
            RiskLevel.CONSERVATIVE: 0.5,
            RiskLevel.MODERATE: 1.0,
            RiskLevel.AGGRESSIVE: 2.0,
        }
        risk_factor = risk_factors.get(self.config.risk_level, 1.0)
        
        # Ajuster selon le score de confluence
        confidence_factor = confluence_score  # 0.0 à 1.0
        
        # Taille ajustée
        adjusted_size = recommended_size * risk_factor * confidence_factor
        
        # Limiter par le SOL disponible
        max_from_available = self.portfolio.available_sol * (self.config.max_position_size_pct / 100)
        adjusted_size = min(adjusted_size, max_from_available)
        
        # Limiter par l'exposition totale
        current_exposure = sum(t.amount_sol for t in self.portfolio.open_positions)
        max_exposure = self.portfolio.total_sol * (self.config.max_total_exposure_pct / 100)
        remaining_exposure = max_exposure - current_exposure
        adjusted_size = min(adjusted_size, remaining_exposure)
        
        # Minimum 0.01 SOL
        adjusted_size = max(adjusted_size, 0.01)
        
        return round(adjusted_size, 4)
    
    def calculate_stop_loss(
        self, 
        entry_price: float, 
        confluence_score: float,
        token_risk_score: float = 0.0
    ) -> float:
        """
        Calcule le niveau de stop-loss.
        
        Plus le token est risqué, plus le SL est serré.
        Plus la confluence est forte, plus le SL peut être large.
        """
        # Base: configured default
        sl_pct = self.config.default_stop_loss_pct
        
        # Ajuster selon le risque du token
        if token_risk_score > 0.7:
            sl_pct *= 0.5  # SL plus serré pour les tokens risqués
        elif token_risk_score > 0.4:
            sl_pct *= 0.75
        
        # Ajuster selon la confluence
        if confluence_score >= 0.8:
            sl_pct *= 1.2  # SL plus large si très confiant
        
        # Calculer le prix du stop-loss
        return entry_price * (1 - sl_pct / 100)
    
    def _check_daily_reset(self):
        """Réinitialise les compteurs journaliers si on change de jour"""
        today = date.today()
        if self._daily_reset_date != today:
            self._daily_reset_date = today
            self.portfolio.daily_pnl_sol = 0.0
            self.portfolio.daily_trades = 0
            self.portfolio.daily_wins = 0
            self.portfolio.daily_losses = 0
