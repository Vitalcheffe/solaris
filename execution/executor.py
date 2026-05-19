"""
SOLARIS - Trade Executor
=======================
Exécution des trades sur Solana (ou simulation en paper trading).
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime

from config.settings import WalletConfig, SolanaConfig, TradingMode
from core.models import ConfluenceResult, Trade, SignalType, TradeStatus

logger = logging.getLogger("solaris.executor")


class TradeExecutor:
    """
    Exécuteur de trades sur Solana.
    
    Modes :
    - PAPER: Simulation complète, pas de transaction réelle
    - LIVE: Exécution réelle via RPC Solana et Jupiter Aggregator
    
    En mode LIVE, l'exécuteur utilise :
    - Jupiter Aggregator pour les swaps (meilleur prix)
    - Jito bundles pour la priorité d'exécution
    - Priority fees pour accélérer la confirmation
    """
    
    def __init__(self, wallet_config: WalletConfig, solana_config: SolanaConfig, mode: TradingMode):
        self.wallet_config = wallet_config
        self.solana_config = solana_config
        self.mode = mode
    
    async def execute(
        self, 
        confluence: ConfluenceResult, 
        position_size: float
    ) -> Optional[Trade]:
        """
        Exécute un trade basé sur la confluence.
        
        En mode PAPER: simule l'exécution
        En mode LIVE: exécute réellement via Jupiter + RPC
        """
        if self.mode == TradingMode.PAPER:
            return self._paper_execute(confluence, position_size)
        elif self.mode == TradingMode.LIVE:
            return await self._live_execute(confluence, position_size)
        else:
            logger.warning(f"Mode non supporté pour l'exécution: {self.mode}")
            return None
    
    def _paper_execute(
        self, 
        confluence: ConfluenceResult, 
        position_size: float
    ) -> Trade:
        """Exécution simulée (paper trading)"""
        import uuid
        
        # Prix simulé (dans un vrai système, on prendrait le prix actuel)
        entry_price = 0.001  # Sera remplacé par le vrai prix dans l'engine
        
        trade = Trade(
            id=str(uuid.uuid4())[:8],
            token_address=confluence.token_address,
            token_symbol=confluence.token_symbol,
            side=confluence.recommended_action,
            entry_price_sol=entry_price,
            amount_sol=position_size,
            amount_tokens=position_size / entry_price if entry_price > 0 else 0,
            stop_loss_sol=entry_price * (1 - confluence.stop_loss_pct / 100),
            take_profit_sol=entry_price * (1 + confluence.take_profit_pct / 100),
            status=TradeStatus.EXECUTED,
            confluence_score=confluence.confluence_score,
            signals=[s.source.value for s in confluence.signals],
        )
        
        logger.info(
            f"[PAPER TRADE] {trade.side.value} {trade.token_symbol} "
            f"{trade.amount_sol:.4f} SOL"
        )
        
        return trade
    
    async def _live_execute(
        self, 
        confluence: ConfluenceResult, 
        position_size: float
    ) -> Optional[Trade]:
        """
        Exécution réelle sur Solana.
        
        Étapes :
        1. Construire la transaction via Jupiter Aggregator
        2. Signer avec le wallet
        3. Envoyer via Jito bundle (priorité)
        4. Confirmer l'exécution
        """
        import uuid
        
        if not self.wallet_config.private_key:
            logger.error("Clé privée non configurée - impossible d'exécuter en LIVE")
            return None
        
        try:
            # Étape 1: Obtenir le quote Jupiter
            quote = await self._get_jupiter_quote(
                confluence.token_address,
                position_size,
                confluence.recommended_action
            )
            
            if not quote:
                logger.error("Impossible d'obtenir un quote Jupiter")
                return None
            
            # Étape 2: Construire la transaction
            tx_data = await self._build_jupiter_swap(quote)
            
            if not tx_data:
                logger.error("Impossible de construire la transaction")
                return None
            
            # Étape 3: Signer et envoyer
            signature = await self._sign_and_send(tx_data)
            
            if not signature:
                logger.error("Transaction échouée")
                return None
            
            # Étape 4: Créer le Trade object
            entry_price = float(quote.get("price", 0))
            
            trade = Trade(
                id=str(uuid.uuid4())[:8],
                token_address=confluence.token_address,
                token_symbol=confluence.token_symbol,
                side=confluence.recommended_action,
                entry_price_sol=entry_price,
                amount_sol=position_size,
                amount_tokens=position_size / entry_price if entry_price > 0 else 0,
                stop_loss_sol=entry_price * (1 - confluence.stop_loss_pct / 100),
                take_profit_sol=entry_price * (1 + confluence.take_profit_pct / 100),
                status=TradeStatus.EXECUTED,
                confluence_score=confluence.confluence_score,
                signals=[s.source.value for s in confluence.signals],
                tx_signature=signature,
            )
            
            logger.info(
                f"[LIVE TRADE] {trade.side.value} {trade.token_symbol} "
                f"{trade.amount_sol:.4f} SOL - TX: {signature[:16]}..."
            )
            
            return trade
            
        except Exception as e:
            logger.error(f"Erreur exécution LIVE: {e}")
            return None
    
    async def _get_jupiter_quote(
        self, 
        token_address: str, 
        amount_sol: float,
        side: SignalType
    ) -> Optional[dict]:
        """Obtient un quote de swap via Jupiter Aggregator V6"""
        import aiohttp
        
        SOL_MINT = "So11111111111111111111111111111111111111112"
        
        # Déterminer input/output
        if side == SignalType.BUY:
            input_mint = SOL_MINT
            output_mint = token_address
        else:
            input_mint = token_address
            output_mint = SOL_MINT
        
        amount_lamports = int(amount_sol * 1_000_000_000)
        slippage_bps = self.wallet_config.default_slippage_bps
        
        url = "https://quote-api.jup.ag/v6/quote"
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount_lamports,
            "slippageBps": slippage_bps,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            logger.debug(f"Jupiter quote error: {e}")
        
        return None
    
    async def _build_jupiter_swap(self, quote: dict) -> Optional[dict]:
        """Construit la transaction de swap via Jupiter"""
        # Nécessite la clé publique du wallet
        if not self.wallet_config.public_key:
            return None
        
        import aiohttp
        
        url = "https://quote-api.jup.ag/v6/swap"
        payload = {
            "quoteResponse": quote,
            "userPublicKey": self.wallet_config.public_key,
            "priorityFeeLamports": self.wallet_config.priority_fee_lamports,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            logger.debug(f"Jupiter swap build error: {e}")
        
        return None
    
    async def _sign_and_send(self, tx_data: dict) -> Optional[str]:
        """Signe et envoie la transaction"""
        # Cette partie nécessite solders ou nacl pour signer
        # Pour le moment, on log et on retourne None
        logger.warning("Signature de transaction non implémentée - utilisez le mode PAPER")
        return None
