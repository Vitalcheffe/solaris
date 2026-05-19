"""
SOLARIS - Trade Executor
=======================
Exécution des trades sur Solana (ou simulation en paper trading).
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone

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
    - solders pour la signature des transactions
    - Priority fees pour accélérer la confirmation
    """
    
    def __init__(self, wallet_config: WalletConfig, solana_config: SolanaConfig, mode: TradingMode):
        self.wallet_config = wallet_config
        self.solana_config = solana_config
        self.mode = mode
        
        # Keypair pour le mode LIVE (lazy loading)
        self._keypair = None
    
    def _get_keypair(self):
        """
        Charge le keypair Solana à partir de la clé privée base58.
        
        La clé privée doit être au format base58 (44-88 caractères).
        solders est utilisé pour la désérialisation et la signature.
        """
        if self._keypair is not None:
            return self._keypair
        
        if not self.wallet_config.private_key:
            return None
        
        try:
            import base58
            from solders.keypair import Keypair
            
            # Décoder la clé base58
            private_key_bytes = base58.b58decode(self.wallet_config.private_key)
            
            # Solana keypair format: first 32 bytes = ed25519 seed, full 64 bytes = keypair
            if len(private_key_bytes) == 64:
                self._keypair = Keypair.from_bytes(private_key_bytes)
            elif len(private_key_bytes) == 32:
                self._keypair = Keypair.from_seed(private_key_bytes)
            else:
                logger.error(f"Format de clé privée invalide: {len(private_key_bytes)} bytes (attendu 32 ou 64)")
                return None
            
            # Vérifier que la clé publique correspond
            derived_pubkey = str(self._keypair.pubkey())
            if self.wallet_config.public_key and derived_pubkey != self.wallet_config.public_key:
                logger.warning(
                    f"Clé publique dérivée ({derived_pubkey}) ne correspond pas "
                    f"à la clé configurée ({self.wallet_config.public_key})"
                )
            
            logger.info(f"Keypair chargé: {derived_pubkey}")
            return self._keypair
            
        except ImportError:
            logger.error(
                "solders n'est pas installé. Installez-le avec: pip install solders"
            )
            return None
        except Exception as e:
            logger.error(f"Erreur chargement keypair: {e}")
            return None
    
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
        1. Charger le keypair
        2. Obtenir le quote Jupiter
        3. Construire la transaction de swap
        4. Désérialiser, signer et envoyer
        5. Confirmer l'exécution
        """
        import uuid
        
        # Étape 1: Charger le keypair
        keypair = self._get_keypair()
        if not keypair:
            logger.error("Keypair non disponible - impossible d'exécuter en LIVE")
            return None
        
        try:
            # Étape 2: Obtenir le quote Jupiter
            quote = await self._get_jupiter_quote(
                confluence.token_address,
                position_size,
                confluence.recommended_action
            )
            
            if not quote:
                logger.error("Impossible d'obtenir un quote Jupiter")
                return None
            
            # Étape 3: Construire la transaction de swap
            tx_data = await self._build_jupiter_swap(quote)
            
            if not tx_data:
                logger.error("Impossible de construire la transaction")
                return None
            
            # Étape 4: Signer et envoyer
            signature = await self._sign_and_send(tx_data)
            
            if not signature:
                logger.error("Transaction échouée - signature/envoi échoué")
                return None
            
            # Étape 5: Confirmer l'exécution
            confirmed = await self._confirm_transaction(signature)
            
            # Étape 6: Créer le Trade object
            entry_price = float(quote.get("price", 0))
            if entry_price == 0:
                # Estimer le prix à partir du quote
                out_amount = int(quote.get("outAmount", 0))
                in_amount = int(quote.get("inAmount", 0))
                if in_amount > 0:
                    entry_price = (in_amount / 1_000_000_000) / (out_amount / 1_000_000) if out_amount > 0 else 0
            
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
                status=TradeStatus.EXECUTED if confirmed else TradeStatus.PENDING,
                confluence_score=confluence.confluence_score,
                signals=[s.source.value for s in confluence.signals],
                tx_signature=signature,
            )
            
            status_label = "CONFIRMED" if confirmed else "PENDING"
            logger.info(
                f"[LIVE TRADE] [{status_label}] {trade.side.value} {trade.token_symbol} "
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
                    else:
                        error_text = await resp.text()
                        logger.error(f"Jupiter quote error {resp.status}: {error_text[:200]}")
        except Exception as e:
            logger.error(f"Jupiter quote exception: {e}")
        
        return None
    
    async def _build_jupiter_swap(self, quote: dict) -> Optional[dict]:
        """Construit la transaction de swap via Jupiter"""
        keypair = self._get_keypair()
        if not keypair:
            return None
        
        import aiohttp
        
        user_pubkey = str(keypair.pubkey())
        
        url = "https://quote-api.jup.ag/v6/swap"
        payload = {
            "quoteResponse": quote,
            "userPublicKey": user_pubkey,
            "priorityFeeLamports": self.wallet_config.priority_fee_lamports,
            "dynamicComputeUnitLimit": True,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        error_text = await resp.text()
                        logger.error(f"Jupiter swap build error {resp.status}: {error_text[:200]}")
        except Exception as e:
            logger.error(f"Jupiter swap build exception: {e}")
        
        return None
    
    async def _sign_and_send(self, tx_data: dict) -> Optional[str]:
        """
        Signe et envoie la transaction Solana.
        
        Processus :
        1. Désérialiser la transaction depuis base58 ou base64
        2. Signer avec le keypair
        3. Envoyer via RPC
        """
        keypair = self._get_keypair()
        if not keypair:
            logger.error("Keypair non disponible pour la signature")
            return None
        
        try:
            from solders.transaction import VersionedTransaction
            import aiohttp
            
            # Jupiter renvoie la transaction en base64 dans "swapTransaction"
            swap_transaction_b64 = tx_data.get("swapTransaction")
            if not swap_transaction_b64:
                logger.error("Pas de swapTransaction dans la réponse Jupiter")
                return None
            
            # Désérialiser la transaction
            import base64 as b64
            tx_bytes = b64.b64decode(swap_transaction_b64)
            
            # Parser la transaction Versioned
            vtx = VersionedTransaction.from_bytes(tx_bytes)
            
            # Signer la transaction
            signed_vtx = VersionedTransaction(vtx.message, [keypair])
            
            # Envoyer via RPC
            rpc_url = self.solana_config.helius_rpc_url if self.solana_config.helius_api_key else self.solana_config.rpc_url
            
            serialized = bytes(signed_vtx)
            encoded = b64.b64encode(serialized).decode('utf-8')
            
            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [
                        encoded,
                        {
                            "encoding": "base64",
                            "skipPreflight": False,
                            "maxRetries": 3,
                        }
                    ]
                }
                
                async with session.post(rpc_url, json=payload) as resp:
                    data = await resp.json()
                    if "error" in data:
                        logger.error(f"RPC sendTransaction error: {data['error']}")
                        return None
                    
                    signature = data.get("result")
                    if signature:
                        logger.info(f"Transaction envoyée: {signature}")
                        return signature
            
            return None
            
        except ImportError as e:
            logger.error(f"Dépendance manquante pour la signature: {e}. Installez solders: pip install solders")
            return None
        except Exception as e:
            logger.error(f"Erreur signature/envoi transaction: {e}")
            return None
    
    async def _confirm_transaction(self, signature: str, timeout: int = 30) -> bool:
        """
        Confirme qu'une transaction a été validée sur la blockchain.
        
        Attend la confirmation avec un timeout de 30 secondes.
        """
        import aiohttp
        
        rpc_url = self.solana_config.helius_rpc_url if self.solana_config.helius_api_key else self.solana_config.rpc_url
        
        start_time = asyncio.get_event_loop().time()
        
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            try:
                async with aiohttp.ClientSession() as session:
                    payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignatureStatuses",
                        "params": [[signature]]
                    }
                    
                    async with session.post(rpc_url, json=payload) as resp:
                        data = await resp.json()
                        result = data.get("result", {})
                        statuses = result.get("value", [])
                        
                        if statuses and statuses[0]:
                            status = statuses[0]
                            if status.get("confirmationStatus") in ("confirmed", "finalized"):
                                logger.info(f"Transaction confirmée: {signature[:16]}...")
                                return True
                            elif status.get("err"):
                                logger.error(f"Transaction échouée: {status['err']}")
                                return False
                
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.debug(f"Erreur confirmation check: {e}")
                await asyncio.sleep(1)
        
        logger.warning(f"Timeout confirmation pour {signature[:16]}...")
        return False
