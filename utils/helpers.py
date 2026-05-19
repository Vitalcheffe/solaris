"""
SOLARIS - Utility Functions
===========================
Fonctions utilitaires utilisées partout dans le système.
"""

import hashlib
import time
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone


logger = logging.getLogger("solaris.utils")


def lamports_to_sol(lamports: int) -> float:
    """Convertit les lamports en SOL"""
    return lamports / 1_000_000_000


def sol_to_lamports(sol: float) -> int:
    """Convertit les SOL en lamports"""
    return int(sol * 1_000_000_000)


def shorten_address(address: str, chars: int = 4) -> str:
    """Raccourcit une adresse Solana pour l'affichage"""
    if not address or len(address) < chars * 2 + 3:
        return address
    return f"{address[:chars]}...{address[-chars:]}"


def shorten_signature(signature: str, chars: int = 8) -> str:
    """Raccourcit une signature de transaction"""
    if not signature or len(signature) < chars * 2 + 3:
        return signature
    return f"{signature[:chars]}...{signature[-chars:]}"


def format_sol(amount: float) -> str:
    """Formate un montant SOL pour l'affichage"""
    if amount >= 1000:
        return f"{amount:,.2f} SOL"
    elif amount >= 1:
        return f"{amount:.4f} SOL"
    elif amount >= 0.001:
        return f"{amount:.6f} SOL"
    else:
        return f"{amount:.9f} SOL"


def format_pct(value: float) -> str:
    """Formate un pourcentage avec signe"""
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def format_usd(amount: float) -> str:
    """Formate un montant USD"""
    if amount >= 1_000_000:
        return f"${amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"${amount/1_000:.2f}K"
    else:
        return f"${amount:.2f}"


def utc_now() -> datetime:
    """Retourne l'heure UTC actuelle"""
    return datetime.now(timezone.utc)


def timestamp_to_datetime(ts: int) -> datetime:
    """Convertit un timestamp Unix en datetime"""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def calculate_pnl(entry_price: float, exit_price: float, amount: float) -> tuple:
    """
    Calcule le PnL d'un trade.
    
    Returns: (pnl_absolu, pnl_pourcentage)
    """
    pnl_abs = (exit_price - entry_price) * amount
    pnl_pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
    return pnl_abs, pnl_pct


def calculate_sharpe_ratio(returns: list, risk_free_rate: float = 0.0) -> float:
    """
    Calcule le ratio de Sharpe.
    
    Sharpe = (R_p - R_f) / σ_p
    
    Un ratio > 1 est bon, > 2 est excellent.
    """
    if not returns or len(returns) < 2:
        return 0.0
    
    import numpy as np
    
    avg_return = np.mean(returns)
    std_return = np.std(returns)
    
    if std_return == 0:
        return 0.0
    
    return (avg_return - risk_free_rate) / std_return


def calculate_max_drawdown(equity_curve: list) -> float:
    """
    Calcule le maximum drawdown (chute maximale du portefeuille).
    
    Exprimé en pourcentage. Plus c'est bas, plus c'est risqué.
    """
    if not equity_curve or len(equity_curve) < 2:
        return 0.0
    
    peak = equity_curve[0]
    max_dd = 0.0
    
    for value in equity_curve:
        if value > peak:
            peak = value
        dd = (peak - value) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    
    return max_dd * 100


def hash_signature(signature: str) -> str:
    """Hash une signature de transaction pour le cache"""
    return hashlib.sha256(signature.encode()).hexdigest()[:16]


class RateLimiter:
    """Limiteur de taux d'appels API"""
    
    def __init__(self, max_calls: int, period_seconds: int):
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self.calls: list = []
    
    async def wait_if_needed(self):
        """Attend si le taux limite est atteint"""
        now = time.time()
        
        # Nettoyer les appels anciens
        self.calls = [c for c in self.calls if now - c < self.period_seconds]
        
        if len(self.calls) >= self.max_calls:
            oldest = self.calls[0]
            wait_time = self.period_seconds - (now - oldest)
            if wait_time > 0:
                logger.debug(f"Rate limit atteint, attente de {wait_time:.1f}s")
                import asyncio
                await asyncio.sleep(wait_time)
        
        self.calls.append(time.time())


class CircuitBreaker:
    """
    Circuit Breaker - arrête les trades si trop d'erreurs.
    
    États :
    - CLOSED: Fonctionnement normal
    - OPEN: Trop d'erreurs, les trades sont bloqués
    - HALF_OPEN: Test si le système est rétabli
    """
    
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 300):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = self.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
    
    def record_success(self):
        """Enregistre un succès"""
        self.failure_count = 0
        self.state = self.CLOSED
    
    def record_failure(self):
        """Enregistre un échec"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = self.OPEN
            logger.warning(
                f"Circuit Breaker OPEN - {self.failure_count} échecs consécutifs"
            )
    
    def can_execute(self) -> bool:
        """Vérifie si un trade peut être exécuté"""
        if self.state == self.CLOSED:
            return True
        
        if self.state == self.OPEN:
            if self.last_failure_time and \
               time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = self.HALF_OPEN
                logger.info("Circuit Breaker HALF_OPEN - test de récupération")
                return True
            return False
        
        if self.state == self.HALF_OPEN:
            return True
        
        return False


def validate_sol_address(address: str) -> bool:
    """Valide qu'une adresse Solana a le bon format (Base58, 32-44 chars)"""
    if not address:
        return False
    
    # Les adresses Solana sont en Base58, typiquement 32-44 caractères
    if len(address) < 32 or len(address) > 44:
        return False
    
    # Caractères Base58 valides (pas de 0, O, I, l)
    base58_chars = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
    return all(c in base58_chars for c in address)


def calculate_position_size_kelly(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    fraction: float = 0.5
) -> float:
    """
    Calcule la taille de position selon le critère de Kelly.
    
    Kelly% = (p * b - q) / b
    
    où:
    - p = probabilité de gain (win_rate)
    - q = 1 - p
    - b = ratio gain/perte moyen
    
    fraction = "fractional Kelly" pour réduire le risque
    (0.5 = half Kelly, plus conservateur)
    """
    if avg_loss == 0 or win_rate <= 0 or win_rate >= 1:
        return 0.0
    
    b = avg_win / avg_loss  # ratio gain/perte
    q = 1 - win_rate
    
    kelly = (win_rate * b - q) / b
    
    # Ne jamais parier plus que le Kelly
    if kelly <= 0:
        return 0.0
    
    # Fractional Kelly (plus sûr)
    return kelly * fraction
