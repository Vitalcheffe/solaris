"""
SOLARIS - Token Sniper Strategy
===============================
Détection et évaluation des nouveaux tokens sur Solana.
Filtres anti-rug pull intégrés.
"""

import asyncio
import logging
from typing import List, Optional
from datetime import datetime, timezone

from config.settings import StrategyConfig
from core.models import (
    NewTokenEvent, Signal, SignalType, SignalSource, TokenRisk
)
from data.onchain_fetcher import OnChainFetcher

logger = logging.getLogger("solaris.sniper")


class TokenSniper:
    """
    Stratégie de sniping de nouveaux tokens.
    
    Comment ça marche :
    1. Détecte les nouveaux tokens dès leur lancement (Pump.fun, Raydium)
    2. Applique des filtres anti-rug pull
    3. Évalue le potentiel du token
    4. Génère un signal si le token passe tous les filtres
    
    AVERTISSEMENT : Le sniping est la stratégie la plus risquée.
    90% des nouveaux tokens sont des rug pulls ou meurent en quelques heures.
    Les filtres sont essentiels pour éviter de perdre de l'argent.
    """
    
    def __init__(self, config: StrategyConfig, fetcher: OnChainFetcher):
        self.config = config
        self.fetcher = fetcher
        
        # Tokens vus récemment (pour éviter les doublons)
        self._seen_tokens: set = set()
        
        # Historique des créateurs (pour détecter les serial rug pullers)
        self._creator_history: dict = {}
    
    async def initialize(self):
        """Initialise le sniper"""
        logger.info(
            f"Token Sniper: max buy = {self.config.sniping_max_buy_sol} SOL, "
            f"TP = +{self.config.sniping_take_profit_pct}%, "
            f"SL = -{self.config.sniping_stop_loss_pct}%, "
            f"anti-rug = {self.config.sniping_anti_rug_checks}"
        )
    
    async def scan_new_tokens(self) -> List[NewTokenEvent]:
        """
        Scanne les nouveaux tokens récemment lancés.
        
        Sources :
        - Pump.fun (principal launchpad memecoin Solana)
        - Raydium nouveaux pools
        - Birdeye new listings API
        """
        new_tokens = []
        
        try:
            # Récupérer les nouveaux listings
            listings = await self.fetcher.get_new_listings(limit=20)
            
            for listing in listings:
                address = listing.get("address", listing.get("mint", ""))
                
                # Éviter les doublons
                if address in self._seen_tokens:
                    continue
                
                self._seen_tokens.add(address)
                
                # Créer l'événement
                token_event = NewTokenEvent(
                    token_address=address,
                    token_symbol=listing.get("symbol", "???"),
                    token_name=listing.get("name", "Unknown"),
                    creator_address=listing.get("creator", listing.get("authority", "")),
                    launch_platform=listing.get("platform", "unknown"),
                    initial_liquidity_sol=float(listing.get("liquidity", 0)),
                    launch_time=datetime.now(timezone.utc),
                    price_at_launch_sol=float(listing.get("price", 0)),
                    current_price_sol=float(listing.get("price", 0)),
                )
                
                # Vérification de sécurité si activée
                if self.config.sniping_anti_rug_checks:
                    security = await self.fetcher.get_token_security(address)
                    if security:
                        token_event.is_mint_renounced = security.get("mint_authority") is None
                        token_event.is_lp_burned = security.get("is_lp_burned", False)
                        token_event.honeypot_score = float(security.get("score", 0.5))
                        token_event.top_holder_pct = float(security.get("top_holder_pct", 0))
                
                new_tokens.append(token_event)
            
            # Nettoyer le cache des tokens vus
            if len(self._seen_tokens) > 500:
                self._seen_tokens = set(list(self._seen_tokens)[-200:])
        
        except Exception as e:
            logger.debug(f"Erreur scan nouveaux tokens: {e}")
        
        return new_tokens
    
    def evaluate_and_signal(self, token_event: NewTokenEvent) -> Optional[Signal]:
        """
        Évalue un nouveau token et génère un signal si les critères sont remplis.
        
        Filtres successifs (tous doivent passer) :
        1. Liquidité minimum
        2. Pas un honeypot probable
        3. Pas de concentration excessive (top holder)
        4. Mint renoncé (idéal mais pas obligatoire)
        5. Créateur fiable
        
        Score basé sur :
        - Liquidité (plus = mieux)
        - Score de sécurité
        - Activité initiale (acheteurs/vendeurs)
        """
        
        # ===== FILTRE 1: Liquidité minimum =====
        if token_event.initial_liquidity_sol < self.config.sniping_min_liquidity_sol:
            return None
        
        # ===== FILTRE 2: Honeypot =====
        if token_event.honeypot_score > self.config.sniping_max_honeypot_score:
            logger.debug(
                f"[SNIPE-REJECT] {token_event.token_symbol}: "
                f"honeypot score {token_event.honeypot_score:.2f} > "
                f"max {self.config.sniping_max_honeypot_score}"
            )
            return None
        
        # ===== FILTRE 3: Concentration =====
        if token_event.top_holder_pct > 50:
            logger.debug(
                f"[SNIPE-REJECT] {token_event.token_symbol}: "
                f"top holder détient {token_event.top_holder_pct:.0f}%"
            )
            return None
        
        # ===== FILTRE 4: Niveau de risque =====
        risk = token_event.risk_level
        if risk == TokenRisk.DANGEROUS:
            return None
        
        # ===== CALCUL DU SCORE =====
        score = 0.1  # Base très basse pour les nouveaux tokens
        
        # Liquidité
        if token_event.initial_liquidity_sol > 50:
            score += 0.2
        elif token_event.initial_liquidity_sol > 20:
            score += 0.15
        elif token_event.initial_liquidity_sol > 10:
            score += 0.1
        
        # Sécurité
        if token_event.is_mint_renounced:
            score += 0.15
        if token_event.is_lp_burned:
            score += 0.15
        
        # Honeypot score (plus c'est bas, mieux c'est)
        score += (1 - token_event.honeypot_score) * 0.1
        
        # Créateur fiable
        if token_event.creator_history_score > 0.5:
            score += 0.1
        
        # Activité initiale
        if token_event.buyers_5m > 10:
            score += 0.1
        elif token_event.buyers_5m > 5:
            score += 0.05
        
        # Pénalité si risque medium
        if risk == TokenRisk.RISKY:
            score *= 0.6
        elif risk == TokenRisk.MEDIUM:
            score *= 0.8
        
        # Seuil minimum
        if score < 0.3:
            return None
        
        risk_label = risk.value
        reason = (
            f"Nouveau token {token_event.token_symbol} "
            f"(risque: {risk_label}, "
            f"liq: {token_event.initial_liquidity_sol:.1f} SOL, "
            f"mint: {'renoncé' if token_event.is_mint_renounced else 'actif'}, "
            f"LP: {'brûlé' if token_event.is_lp_burned else 'non brûlé'})"
        )
        
        return Signal(
            source=SignalSource.NEW_TOKEN,
            signal_type=SignalType.BUY,
            token_address=token_event.token_address,
            token_symbol=token_event.token_symbol,
            score=min(score, 0.7),  # Plafond bas pour les nouveaux tokens
            reason=reason,
            data={
                "risk_level": risk_label,
                "liquidity_sol": token_event.initial_liquidity_sol,
                "is_mint_renounced": token_event.is_mint_renounced,
                "is_lp_burned": token_event.is_lp_burned,
                "honeypot_score": token_event.honeypot_score,
                "platform": token_event.launch_platform,
            }
        )
