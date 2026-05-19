"""
SOLARIS - On-Chain Data Fetcher
==============================
Récupération des données on-chain Solana via RPC et APIs publiques
"""

import asyncio
import aiohttp
import logging
import json
import base64
from typing import Optional, List, Dict, Any
from datetime import datetime

from config.settings import SolanaConfig
from core.models import WhaleTransaction, VolumeData, NewTokenEvent

logger = logging.getLogger("solaris.onchain")


class OnChainFetcher:
    """
    Récupérateur de données on-chain Solana.
    
    Utilise :
    - RPC Solana (ou Helius) pour les données blockchain
    - Birdeye API pour les données de marché
    - WebSocket pour le streaming en temps réel
    """
    
    def __init__(self, config: SolanaConfig):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws_session: Optional[aiohttp.ClientSession] = None
        self._connected = False
        
        # Cache
        self._token_cache: Dict[str, Dict] = {}
        self._price_cache: Dict[str, float] = {}
        self._whale_wallets: Dict[str, Dict] = {}
    
    async def connect(self):
        """Établit la connexion RPC"""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.rpc_timeout)
        )
        self._connected = True
        
        # Vérifier la connexion
        try:
            result = await self._rpc_call("getHealth")
            if result == "ok":
                logger.info("RPC health check: OK")
            else:
                logger.warning(f"RPC health check: {result}")
        except Exception as e:
            logger.warning(f"RPC health check failed: {e}")
    
    async def disconnect(self):
        """Ferme les connexions"""
        if self.session:
            await self.session.close()
        self._connected = False
    
    @property
    def helius_api_base(self) -> str:
        """URL de base pour les APIs Helius Enhanced"""
        if self.config.helius_api_key:
            return f"https://api-mainnet.helius-rpc.com/v0"
        return ""
    
    async def _rpc_call(self, method: str, params: List = None) -> Any:
        """Appel RPC Solana"""
        if not self.session:
            raise RuntimeError("Session RPC non connectée")
        
        url = self.config.helius_rpc_url if self.config.helius_api_key else self.config.rpc_url
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or []
        }
        
        for attempt in range(self.config.max_retries):
            try:
                async with self.session.post(url, json=payload) as resp:
                    data = await resp.json()
                    if "error" in data:
                        raise Exception(f"RPC Error: {data['error']}")
                    return data.get("result")
            except Exception as e:
                logger.debug(f"RPC retry {attempt + 1}/{self.config.max_retries}: {e}")
                if attempt < self.config.max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                else:
                    raise
    
    async def _birdeye_get(self, endpoint: str, params: Dict = None) -> Any:
        """Appel API Birdeye"""
        if not self.session:
            raise RuntimeError("Session non connectée")
        
        url = f"https://public-api.birdeye.so{endpoint}"
        headers = {}
        if self.config.birdeye_api_key:
            headers["X-API-KEY"] = self.config.birdeye_api_key
        
        try:
            async with self.session.get(url, params=params, headers=headers) as resp:
                data = await resp.json()
                if data.get("success"):
                    return data.get("data")
                return None
        except Exception as e:
            logger.debug(f"Birdeye API error: {e}")
            return None
    
    # ========================
    # SOL Balance
    # ========================
    
    async def get_sol_balance(self, wallet_address: str) -> float:
        """Récupère le solde SOL d'un wallet"""
        result = await self._rpc_call("getBalance", [wallet_address])
        if result:
            return result.get("value", 0) / 1_000_000_000  # lamports to SOL
        return 0.0
    
    # ========================
    # Whale Tracking
    # ========================
    
    async def get_recent_transactions(self, wallet_address: str, limit: int = 10) -> List[Dict]:
        """Récupère les transactions récentes d'un wallet"""
        result = await self._rpc_call("getSignaturesForAddress", [
            wallet_address,
            {"limit": limit}
        ])
        return result or []
    
    async def get_transaction_details(self, signature: str) -> Optional[Dict]:
        """Récupère les détails d'une transaction"""
        result = await self._rpc_call("getTransaction", [
            signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
        ])
        return result
    
    async def _helius_api_call(self, endpoint: str, params: Dict = None) -> Any:
        """Appel API Helius Enhanced (DAS, Parse Transactions, etc.)"""
        if not self.config.helius_api_key or not self.session:
            return None
        
        base = self.helius_api_base
        url = f"{base}{endpoint}?api-key={self.config.helius_api_key}"
        
        try:
            if params:
                async with self.session.post(url, json=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
            else:
                async with self.session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            logger.debug(f"Helius API error ({endpoint}): {e}")
        
        return None
    
    async def scan_large_transactions(
        self, 
        token_address: str, 
        min_sol: float = 50.0
    ) -> List[WhaleTransaction]:
        """
        Scanne les grandes transactions pour un token.
        Utilise Helius Enhanced API (Parse Transactions) + Birdeye.
        """
        whale_txs = []
        
        # Méthode 1: Helius Parse Transaction History (si disponible)
        if self.config.helius_api_key:
            helius_txs = await self._helius_api_call(
                f"/addresses/{token_address}/transactions"
            )
            if helius_txs and isinstance(helius_txs, list):
                for tx in helius_txs:
                    # Helius parsed transactions
                    native_transfers = tx.get("nativeTransfers", [])
                    events = tx.get("events", {})
                    swap = events.get("swap", {})
                    
                    if swap:
                        sol_amount = 0
                        for transfer in native_transfers:
                            amount = transfer.get("amount", 0) / 1_000_000_000
                            if amount >= min_sol * 0.1:  # Even partial matches
                                sol_amount += amount
                        
                        if sol_amount >= min_sol:
                            from core.models import SignalType
                            is_buy = any(
                                t.get("toUserAccount") == token_address 
                                for t in native_transfers
                            )
                            whale_tx = WhaleTransaction(
                                signature=tx.get("signature", ""),
                                wallet_address=tx.get("description", "")[:44],
                                token_address=token_address,
                                token_symbol=tx.get("tokenTransfers", [{}])[0].get("mint", "UNKNOWN") if tx.get("tokenTransfers") else "UNKNOWN",
                                signal_type=SignalType.BUY if is_buy else SignalType.SELL,
                                amount_sol=sol_amount,
                                amount_tokens=0,
                                price_sol=0,
                                timestamp=datetime.utcnow(),
                                wallet_label=tx.get("type", ""),
                            )
                            whale_txs.append(whale_tx)
        
        # Méthode 2: Birdeye API (fallback/complément)
        transactions = await self._birdeye_get(
            "/defi/txs",
            {"address": token_address, "limit": 50}
        )
        
        if transactions:
            from core.models import SignalType
            for tx in transactions:
                sol_amount = float(tx.get("amount", 0))
                if sol_amount >= min_sol:
                    side_str = tx.get("side", "BUY").upper()
                    whale_tx = WhaleTransaction(
                        signature=tx.get("txHash", ""),
                        wallet_address=tx.get("owner", ""),
                        token_address=token_address,
                        token_symbol=tx.get("symbol", "UNKNOWN"),
                        signal_type=SignalType.BUY if side_str == "BUY" else SignalType.SELL,
                        amount_sol=sol_amount,
                        amount_tokens=float(tx.get("uiAmount", 0)),
                        price_sol=float(tx.get("price", 0)),
                        timestamp=datetime.utcnow(),
                        wallet_label=tx.get("label"),
                    )
                    whale_txs.append(whale_tx)
        
        return whale_txs
    
    async def get_top_holders(self, token_address: str, limit: int = 20) -> List[Dict]:
        """Récupère les plus grands détenteurs d'un token"""
        result = await self._birdeye_get(
            "/defi/token_holder",
            {"address": token_address, "limit": limit}
        )
        return result or []
    
    # ========================
    # Volume & Price Data
    # ========================
    
    async def get_token_market_data(self, token_address: str) -> Optional[Dict]:
        """Récupère les données de marché d'un token (prix, volume, market cap)"""
        result = await self._birdeye_get(
            "/defi/price",
            {"address": token_address}
        )
        return result
    
    async def get_token_volume(self, token_address: str, timeframe: str = "24h") -> Optional[Dict]:
        """Récupère les données de volume d'un token"""
        result = await self._birdeye_get(
            "/defi/volume",
            {"address": token_address, "type": timeframe}
        )
        return result
    
    async def get_dex_trades(
        self, 
        token_address: str, 
        limit: int = 50
    ) -> List[Dict]:
        """Récupère les trades DEX récents pour un token"""
        result = await self._birdeye_get(
            "/defi/txs",
            {"address": token_address, "limit": limit}
        )
        return result or []
    
    # ========================
    # New Token Detection
    # ========================
    
    async def get_new_listings(self, limit: int = 20) -> List[Dict]:
        """Récupère les nouveaux tokens listés récemment"""
        result = await self._birdeye_get(
            "/defi/token_new_listing",
            {"limit": limit, "sort_by": "creation_time", "sort_type": "desc"}
        )
        return result or []
    
    async def get_token_security(self, token_address: str) -> Optional[Dict]:
        """
        Vérifie la sécurité d'un token (honeypot, mint authority, etc.)
        Utilise des APIs publiques comme RugCheck ou GoPlus.
        """
        # RugCheck API (gratuit)
        try:
            url = f"https://api.rugcheck.xyz/v1/tokens/{token_address}/report/summary"
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception:
            pass
        
        # Fallback: vérification basique on-chain
        return await self._basic_token_check(token_address)
    
    async def _basic_token_check(self, token_address: str) -> Dict:
        """Vérification basique d'un token directement on-chain"""
        result = {
            "mint_authority": None,
            "freeze_authority": None,
            "is_honeypot": False,
            "top_holder_pct": 0,
            "score": 0.5,
        }
        
        try:
            # Vérifier le mint authority
            account_info = await self._rpc_call("getAccountInfo", [
                token_address,
                {"encoding": "jsonParsed"}
            ])
            
            if account_info and account_info.get("value"):
                data = account_info["value"].get("data", {})
                if isinstance(data, dict):
                    parsed = data.get("parsed", {})
                    info = parsed.get("info", {})
                    result["mint_authority"] = info.get("mintAuthority")
                    result["freeze_authority"] = info.get("freezeAuthority")
                    
                    # Si mint authority est null, le token est plus sûr
                    if info.get("mintAuthority") is None:
                        result["score"] += 0.2
                    if info.get("freezeAuthority") is None:
                        result["score"] += 0.1
        
        except Exception as e:
            logger.debug(f"Token check error: {e}")
        
        return result
    
    # ========================
    # Smart Money Detection
    # ========================
    
    async def identify_smart_money_wallets(self, token_address: str) -> List[str]:
        """
        Identifie les wallets 'smart money' pour un token donné.
        Smart money = wallets qui ont réalisé des profits significatifs.
        """
        smart_wallets = []
        
        holders = await self.get_top_holders(token_address, limit=50)
        
        for holder in holders:
            address = holder.get("address", "")
            pnl = float(holder.get("pnl", 0))
            
            # Critères smart money
            if pnl > 10:  # Plus de 10 SOL de profit
                smart_wallets.append(address)
                self._whale_wallets[address] = {
                    "pnl": pnl,
                    "win_rate": float(holder.get("winRate", 0)),
                    "avg_roi": float(holder.get("roi", 0)),
                    "label": "Smart Money",
                }
        
        return smart_wallets
    
    # ========================
    # Utility
    # ========================
    
    async def get_slot(self) -> int:
        """Récupère le slot actuel"""
        return await self._rpc_call("getSlot")
    
    async def get_block_time(self, slot: int) -> int:
        """Récupère le timestamp d'un block"""
        return await self._rpc_call("getBlockTime", [slot])
