"""
SOLARIS - Price Feed
===================
Flux de prix en temps réel pour les tokens Solana
"""

import asyncio
import aiohttp
import logging
from typing import Optional, Dict, List
from datetime import datetime
from collections import deque

from config.settings import SolanaConfig

logger = logging.getLogger("solaris.pricefeed")

# Adresses Solana connues
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

TOKEN_MINTS = {
    "SOL": SOL_MINT,
    "USDC": USDC_MINT,
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "JTO": "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
    "RNDR": "rndrizKT3MK1iimdxRdWabcF7Zg7QR5ANgFmFy68q2S",
    "PYTH": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "MNGO": "MangoCz7363OZX9jix1CDYPYgbe4nuhsY52Gn5aQr1Gk",
}


class PriceFeed:
    """
    Flux de prix en temps réel pour Solana.
    
    Sources :
    - Birdeye API (prix, OHLCV)
    - Jupiter API (prix DEX)
    - RPC Solana (prix pools)
    """
    
    def __init__(self, config: SolanaConfig):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
        
        # Prix actuels
        self._prices: Dict[str, float] = {}  # symbol -> price_sol
        self._prices_usd: Dict[str, float] = {}  # symbol -> price_usd
        
        # Historique pour les indicateurs techniques
        self._price_history: Dict[str, deque] = {}  # symbol -> deque of (timestamp, price)
        self._ohlcv: Dict[str, List[Dict]] = {}  # symbol -> list of OHLCV candles
        
        # Sol price
        self._sol_price_usd = 0.0
    
    async def start(self):
        """Démarre le price feed"""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        )
        self.running = True
        
        # Charger les prix initiaux
        await self._load_initial_prices()
        
        logger.info(f"Price feed démarré - {len(self._prices)} tokens chargés")
    
    async def stop(self):
        """Arrête le price feed"""
        self.running = False
        if self.session:
            await self.session.close()
    
    async def _load_initial_prices(self):
        """Charge les prix initiaux pour la watchlist"""
        for symbol, mint in TOKEN_MINTS.items():
            price = await self._fetch_price_from_birdeye(mint)
            if price:
                self._prices[symbol] = price
                self._price_history[symbol] = deque(maxlen=1000)
                self._price_history[symbol].append((datetime.utcnow(), price))
    
    async def update_price(self, symbol: str):
        """Met à jour le prix d'un token"""
        mint = TOKEN_MINTS.get(symbol)
        if not mint:
            return
        
        price = await self._fetch_price_from_birdeye(mint)
        if price:
            old_price = self._prices.get(symbol, 0)
            self._prices[symbol] = price
            
            if symbol not in self._price_history:
                self._price_history[symbol] = deque(maxlen=1000)
            self._price_history[symbol].append((datetime.utcnow(), price))
            
            # Log si changement significatif
            if old_price > 0:
                change_pct = ((price - old_price) / old_price) * 100
                if abs(change_pct) > 2.0:
                    direction = "+" if change_pct > 0 else ""
                    logger.info(
                        f"[PRICE] {symbol}: {price:.8f} SOL ({direction}{change_pct:.1f}%)"
                    )
    
    async def _fetch_price_from_birdeye(self, mint_address: str) -> Optional[float]:
        """Récupère le prix d'un token via Birdeye"""
        if not self.session:
            return None
        
        url = "https://public-api.birdeye.so/defi/price"
        params = {"address": mint_address}
        headers = {}
        if self.config.birdeye_api_key:
            headers["X-API-KEY"] = self.config.birdeye_api_key
        
        try:
            async with self.session.get(url, params=params, headers=headers) as resp:
                data = await resp.json()
                if data.get("success"):
                    return float(data["data"].get("value", 0))
        except Exception as e:
            logger.debug(f"Price fetch error for {mint_address}: {e}")
        
        # Fallback: Jupiter API
        return await self._fetch_price_from_jupiter(mint_address)
    
    async def _fetch_price_from_jupiter(self, mint_address: str) -> Optional[float]:
        """Récupère le prix via Jupiter (fallback)"""
        if not self.session:
            return None
        
        try:
            url = f"https://price.jup.ag/v6/price?ids={mint_address}"
            async with self.session.get(url) as resp:
                data = await resp.json()
                if mint_address in data.get("data", {}):
                    return float(data["data"][mint_address].get("price", 0))
        except Exception as e:
            logger.debug(f"Jupiter price error for {mint_address}: {e}")
        
        return None
    
    async def get_ohlcv(
        self, 
        symbol: str, 
        timeframe: str = "1H",
        limit: int = 100
    ) -> List[Dict]:
        """
        Récupère les données OHLCV (Open, High, Low, Close, Volume)
        pour les indicateurs techniques.
        """
        mint = TOKEN_MINTS.get(symbol)
        if not mint or not self.session:
            return []
        
        url = "https://public-api.birdeye.so/defi/ohlcv"
        params = {
            "address": mint,
            "type": timeframe,
            "limit": limit,
        }
        headers = {}
        if self.config.birdeye_api_key:
            headers["X-API-KEY"] = self.config.birdeye_api_key
        
        try:
            async with self.session.get(url, params=params, headers=headers) as resp:
                data = await resp.json()
                if data.get("success"):
                    items = data.get("data", {}).get("items", [])
                    self._ohlcv[symbol] = items
                    return items
        except Exception as e:
            logger.debug(f"OHLCV fetch error: {e}")
        
        return []
    
    def get_price(self, symbol: str) -> Optional[float]:
        """Retourne le prix actuel d'un token en SOL"""
        return self._prices.get(symbol)
    
    def get_price_usd(self, symbol: str) -> Optional[float]:
        """Retourne le prix actuel d'un token en USD"""
        price_sol = self._prices.get(symbol)
        if price_sol and self._sol_price_usd:
            return price_sol * self._sol_price_usd
        return self._prices_usd.get(symbol)
    
    def get_price_history(self, symbol: str) -> List[tuple]:
        """Retourne l'historique des prix"""
        return list(self._price_history.get(symbol, []))
    
    def get_latest_candles(self, symbol: str, count: int = 50) -> List[Dict]:
        """Retourne les dernières bougies OHLCV"""
        candles = self._ohlcv.get(symbol, [])
        return candles[-count:] if candles else []
