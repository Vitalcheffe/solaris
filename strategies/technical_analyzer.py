"""
SOLARIS - Technical Analyzer Strategy
=====================================
Analyse technique avec RSI, MACD, Bollinger Bands et moyennes mobiles.
"""

import logging
from typing import Optional, List
from datetime import datetime, timezone

from config.settings import StrategyConfig
from core.models import (
    TechnicalIndicators, Signal, SignalType, SignalSource
)
from data.price_feed import PriceFeed

logger = logging.getLogger("solaris.technical")


class TechnicalAnalyzer:
    """
    Stratégie d'analyse technique.
    
    Combine plusieurs indicateurs classiques pour générer un signal composite :
    - RSI (Relative Strength Index) : momentum et surachat/survente
    - MACD (Moving Average Convergence Divergence) : tendance et crossover
    - Bollinger Bands : volatilité et retournements
    - SMA/EMA : tendance à court et long terme
    
    L'analyse technique en crypto est différente des marchés traditionnels :
    - Les marchés crypto sont ouverts 24/7
    - La volatilité est beaucoup plus élevée
    - Les indicateurs classiques sont moins fiables seuls
    - Mais combinés avec les données on-chain, ils deviennent puissants
    """
    
    def __init__(self, config: StrategyConfig, price_feed: PriceFeed):
        self.config = config
        self.price_feed = price_feed
    
    async def initialize(self):
        """Initialise l'analyseur technique"""
        logger.info(
            f"Technical Analyzer: RSI({self.config.rsi_period}), "
            f"MACD({self.config.macd_fast}/{self.config.macd_slow}/{self.config.macd_signal}), "
            f"BB({self.config.bollinger_period}/{self.config.bollinger_std})"
        )
    
    async def analyze(self, symbol: str, timeframe: str = "1H") -> Optional[TechnicalIndicators]:
        """
        Analyse technique complète d'un token.
        
        1. Récupère les données OHLCV
        2. Calcule tous les indicateurs
        3. Génère un signal composite
        """
        # Récupérer les bougies
        candles = await self.price_feed.get_ohlcv(symbol, timeframe, limit=100)
        
        if not candles or len(candles) < 30:
            # Pas assez de données, utiliser l'historique de prix
            return self._analyze_from_price_history(symbol, timeframe)
        
        # Extraire les prix de clôture
        closes = [float(c.get("c", c.get("close", 0))) for c in candles]
        volumes = [float(c.get("v", c.get("volume", 0))) for c in candles]
        
        if not closes or len(closes) < 26:
            return None
        
        current_price = closes[-1]
        
        # Calculer RSI
        rsi = self._calculate_rsi(closes)
        rsi_signal = self._interpret_rsi(rsi)
        
        # Calculer MACD
        macd_line, signal_line, histogram = self._calculate_macd(closes)
        macd_crossover = self._interpret_macd(macd_line, signal_line, histogram)
        
        # Calculer Bollinger Bands
        bb_upper, bb_middle, bb_lower = self._calculate_bollinger(closes)
        bb_position, bb_squeeze = self._interpret_bollinger(
            current_price, bb_upper, bb_middle, bb_lower, closes
        )
        
        # Calculer moyennes mobiles
        sma_7 = self._calculate_sma(closes, 7)
        sma_25 = self._calculate_sma(closes, 25)
        sma_99 = self._calculate_sma(closes, 99) if len(closes) >= 99 else sma_25
        ema_12 = self._calculate_ema(closes, 12)
        ema_26 = self._calculate_ema(closes, 26)
        
        # Calculer les changements de prix
        price_change_1h = ((closes[-1] - closes[-2]) / closes[-2] * 100) if len(closes) >= 2 else 0
        price_change_24h = ((closes[-1] - closes[-24]) / closes[-24] * 100) if len(closes) >= 24 else 0
        
        # Signal composite
        technical_score = self._calculate_composite_score(
            rsi, rsi_signal, macd_crossover, bb_position, bb_squeeze,
            current_price, sma_7, sma_25, ema_12, ema_26
        )
        
        if technical_score > 0.3:
            technical_signal = SignalType.BUY
        elif technical_score < -0.3:
            technical_signal = SignalType.SELL
        else:
            technical_signal = SignalType.HOLD
        
        return TechnicalIndicators(
            token_address=symbol,
            token_symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            price_change_1h_pct=price_change_1h,
            price_change_24h_pct=price_change_24h,
            rsi=rsi,
            rsi_signal=rsi_signal,
            macd_line=macd_line,
            macd_signal_line=signal_line,
            macd_histogram=histogram,
            macd_crossover=macd_crossover,
            bb_upper=bb_upper,
            bb_middle=bb_middle,
            bb_lower=bb_lower,
            bb_position=bb_position,
            bb_squeeze=bb_squeeze,
            sma_7=sma_7,
            sma_25=sma_25,
            sma_99=sma_99,
            ema_12=ema_12,
            ema_26=ema_26,
            technical_signal=technical_signal,
            technical_score=technical_score,
            timestamp=datetime.now(timezone.utc),
        )
    
    def _analyze_from_price_history(self, symbol: str, timeframe: str) -> Optional[TechnicalIndicators]:
        """Fallback : analyse à partir de l'historique de prix simple"""
        history = self.price_feed.get_price_history(symbol)
        
        if len(history) < 26:
            return None
        
        closes = [price for _, price in history]
        current_price = closes[-1]
        
        rsi = self._calculate_rsi(closes)
        macd_line, signal_line, histogram = self._calculate_macd(closes)
        bb_upper, bb_middle, bb_lower = self._calculate_bollinger(closes)
        sma_7 = self._calculate_sma(closes, 7)
        sma_25 = self._calculate_sma(closes, 25)
        ema_12 = self._calculate_ema(closes, 12)
        ema_26 = self._calculate_ema(closes, 26)
        
        technical_score = self._calculate_composite_score(
            rsi, self._interpret_rsi(rsi),
            self._interpret_macd(macd_line, signal_line, histogram),
            self._interpret_bollinger(current_price, bb_upper, bb_middle, bb_lower, closes)[0],
            self._interpret_bollinger(current_price, bb_upper, bb_middle, bb_lower, closes)[1],
            current_price, sma_7, sma_25, ema_12, ema_26
        )
        
        return TechnicalIndicators(
            token_address=symbol,
            token_symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            price_change_1h_pct=0,
            price_change_24h_pct=0,
            rsi=rsi,
            rsi_signal=self._interpret_rsi(rsi),
            macd_line=macd_line,
            macd_signal_line=signal_line,
            macd_histogram=histogram,
            macd_crossover=self._interpret_macd(macd_line, signal_line, histogram),
            bb_upper=bb_upper,
            bb_middle=bb_middle,
            bb_lower=bb_lower,
            bb_position="middle",
            bb_squeeze=False,
            sma_7=sma_7,
            sma_25=sma_25,
            sma_99=sma_25,
            ema_12=ema_12,
            ema_26=ema_26,
            technical_signal=SignalType.BUY if technical_score > 0.3 else (SignalType.SELL if technical_score < -0.3 else SignalType.HOLD),
            technical_score=technical_score,
            timestamp=datetime.now(timezone.utc),
        )
    
    # ========================
    # Indicateurs Techniques
    # ========================
    
    def _calculate_rsi(self, prices: List[float], period: int = None) -> float:
        """
        Relative Strength Index.
        
        RSI < 30 = survendu (signal d'achat potentiel)
        RSI > 70 = suracheté (signal de vente potentiel)
        RSI entre 30-70 = neutre
        """
        period = period or self.config.rsi_period
        
        if len(prices) < period + 1:
            return 50.0  # Neutre par défaut
        
        changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        
        gains = [c if c > 0 else 0 for c in changes[-period:]]
        losses = [-c if c < 0 else 0 for c in changes[-period:]]
        
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _calculate_macd(self, prices: List[float]) -> tuple:
        """
        MACD (Moving Average Convergence Divergence).
        
        Retourne: (macd_line, signal_line, histogram)
        
        Crossover bullish: MACD croise au-dessus du signal
        Crossover bearish: MACD croise en-dessous du signal
        
        Calcul complet :
        1. EMA rapide (12) - EMA lente (26) = ligne MACD pour chaque point
        2. EMA de la ligne MACD sur 'signal_period' points = signal line
        3. MACD - Signal = histogramme
        """
        fast = self.config.macd_fast
        slow = self.config.macd_slow
        signal_period = self.config.macd_signal
        
        if len(prices) < slow + signal_period:
            # Pas assez de données pour un MACD complet
            ema_fast = self._calculate_ema(prices, fast)
            ema_slow = self._calculate_ema(prices, slow)
            macd_line = ema_fast - ema_slow
            signal_line = macd_line  # Pas de signal si pas assez de données
            return macd_line, signal_line, 0.0
        
        # Étape 1: Calculer les EMA pour chaque point pour obtenir la série MACD
        macd_series = []
        ema_fast_series = self._calculate_ema_series(prices, fast)
        ema_slow_series = self._calculate_ema_series(prices, slow)
        
        for i in range(len(prices)):
            macd_series.append(ema_fast_series[i] - ema_slow_series[i])
        
        # Les premières valeurs sont instables, on ne garde que celles
        # à partir de l'index 'slow' (quand l'EMA lente est significative)
        stable_macd = macd_series[slow - 1:]
        
        if len(stable_macd) < signal_period:
            macd_line = stable_macd[-1] if stable_macd else 0.0
            return macd_line, macd_line, 0.0
        
        # Étape 2: Signal line = EMA du MACD sur 'signal_period' périodes
        signal_series = self._calculate_ema_series(stable_macd, signal_period)
        
        macd_line = stable_macd[-1]
        signal_line = signal_series[-1]
        histogram = macd_line - signal_line
        
        return macd_line, signal_line, histogram
    
    def _calculate_bollinger(self, prices: List[float]) -> tuple:
        """
        Bollinger Bands.
        
        Retourne: (upper, middle, lower)
        
        Quand les bands se resserrent (squeeze) = breakout imminent
        Prix touche la bande inférieure = survendu
        Prix touche la bande supérieure = suracheté
        """
        period = self.config.bollinger_period
        std_mult = self.config.bollinger_std
        
        if len(prices) < period:
            return prices[-1], prices[-1], prices[-1]
        
        recent = prices[-period:]
        middle = sum(recent) / len(recent)
        
        variance = sum((p - middle) ** 2 for p in recent) / len(recent)
        std = variance ** 0.5
        
        upper = middle + (std_mult * std)
        lower = middle - (std_mult * std)
        
        return upper, middle, lower
    
    def _calculate_sma(self, prices: List[float], period: int) -> float:
        """Simple Moving Average"""
        if len(prices) < period:
            return prices[-1] if prices else 0
        return sum(prices[-period:]) / period
    
    def _calculate_ema(self, prices: List[float], period: int) -> float:
        """Exponential Moving Average - retourne la dernière valeur"""
        series = self._calculate_ema_series(prices, period)
        return series[-1] if series else 0.0
    
    def _calculate_ema_series(self, values: List[float], period: int) -> List[float]:
        """
        Exponential Moving Average - retourne la série complète.
        
        Chaque point a sa valeur EMA, ce qui permet de calculer
        l'EMA d'une série (comme pour le MACD signal line).
        """
        if not values:
            return []
        
        if len(values) < period:
            # Pas assez de données, retourner la SMA de ce qu'on a
            sma = sum(values) / len(values)
            return [sma] * len(values)
        
        multiplier = 2 / (period + 1)
        
        # Commencer avec la SMA des 'period' premières valeurs
        ema = sum(values[:period]) / period
        result = [0.0] * (period - 1) + [ema]  # Remplir le début avec des zéros
        
        for value in values[period:]:
            ema = (value - ema) * multiplier + ema
            result.append(ema)
        
        return result
    
    # ========================
    # Interprétation
    # ========================
    
    def _interpret_rsi(self, rsi: float) -> str:
        if rsi >= self.config.rsi_overbought:
            return "overbought"
        elif rsi <= self.config.rsi_oversold:
            return "oversold"
        return "neutral"
    
    def _interpret_macd(self, macd: float, signal: float, histogram: float) -> str:
        if macd > signal and histogram > 0:
            return "bullish"
        elif macd < signal and histogram < 0:
            return "bearish"
        return "none"
    
    def _interpret_bollinger(
        self, price: float, upper: float, middle: float, lower: float, 
        prices: List[float]
    ) -> tuple:
        if price >= upper:
            position = "above_upper"
        elif price <= lower:
            position = "below_lower"
        else:
            position = "middle"
        
        # Détection de squeeze (bands qui se resserrent)
        if len(prices) >= 20:
            band_width = (upper - lower) / middle if middle > 0 else 1
            # Seuil dynamique : calculer la largeur moyenne des bands
            # sur les 20 dernières périodes pour adapter le seuil
            recent_widths = []
            for i in range(max(0, len(prices) - 20), len(prices) - self.config.bollinger_period + 1):
                subset = prices[i:i + self.config.bollinger_period]
                if len(subset) >= self.config.bollinger_period:
                    sub_mid = sum(subset) / len(subset)
                    sub_var = sum((p - sub_mid) ** 2 for p in subset) / len(subset)
                    sub_std = sub_var ** 0.5
                    sub_width = (2 * self.config.bollinger_std * sub_std) / sub_mid if sub_mid > 0 else 1
                    recent_widths.append(sub_width)
            avg_width = sum(recent_widths) / len(recent_widths) if recent_widths else 0.05
            squeeze = band_width < avg_width * 0.5  # Squeeze = < 50% de la largeur moyenne
        else:
            squeeze = False
        
        return position, squeeze
    
    def _calculate_composite_score(
        self, rsi, rsi_signal, macd_crossover, bb_position, bb_squeeze,
        price, sma_7, sma_25, ema_12, ema_26
    ) -> float:
        """
        Score composite : combine tous les indicateurs en un seul score.
        
        Score positif = bullish, négatif = bearish
        Range: -1.0 à +1.0
        """
        score = 0.0
        
        # RSI contribution (-0.3 à +0.3)
        if rsi_signal == "oversold":
            score += 0.3  # Survendu = opportunité d'achat
        elif rsi_signal == "overbought":
            score -= 0.3  # Suracheté = signal de vente
        else:
            # RSI dans la zone neutre, tendance légère
            if rsi < 45:
                score += 0.1
            elif rsi > 55:
                score -= 0.1
        
        # MACD contribution (-0.3 à +0.3)
        if macd_crossover == "bullish":
            score += 0.3
        elif macd_crossover == "bearish":
            score -= 0.3
        
        # Bollinger contribution (-0.2 à +0.2)
        if bb_position == "below_lower":
            score += 0.2  # Prix en zone survendue
        elif bb_position == "above_upper":
            score -= 0.1  # Prix en zone surachetée (moins pénalisant car momentum)
        
        if bb_squeeze:
            score *= 1.2  # Amplifier le signal en période de squeeze
        
        # Tendance via moyennes mobiles (-0.2 à +0.2)
        if price > sma_7 > sma_25:
            score += 0.2  # Tendance haussière confirmée
        elif price < sma_7 < sma_25:
            score -= 0.2  # Tendance baissière confirmée
        
        if ema_12 > ema_26:
            score += 0.1
        else:
            score -= 0.1
        
        # Normaliser entre -1 et 1
        return max(-1.0, min(1.0, score))
    
    def generate_signal(self, indicators: TechnicalIndicators) -> Optional[Signal]:
        """
        Génère un signal de trading basé sur l'analyse technique.
        
        Le signal technique seul n'est pas suffisant pour trader,
        mais combiné avec les données on-chain (confluence), il devient puissant.
        """
        # Score minimum pour générer un signal
        if abs(indicators.technical_score) < 0.3:
            return None
        
        # Convertir le score technique en score de confiance
        confidence = min(abs(indicators.technical_score), 1.0)
        
        # Les signaux techniques sont moins fiables seuls en crypto
        # On les pondère donc plus bas dans la confluence
        score = confidence * 0.6  # Max 0.6 pour un signal technique seul
        
        reasons = []
        if indicators.rsi_signal == "oversold":
            reasons.append(f"RSI survendu ({indicators.rsi:.1f})")
        elif indicators.rsi_signal == "overbought":
            reasons.append(f"RSI suracheté ({indicators.rsi:.1f})")
        
        if indicators.macd_crossover != "none":
            reasons.append(f"MACD {indicators.macd_crossover}")
        
        if indicators.bb_position == "below_lower":
            reasons.append("Prix sous Bollinger inférieure")
        elif indicators.bb_position == "above_upper":
            reasons.append("Prix au-dessus Bollinger supérieure")
        
        if indicators.bb_squeeze:
            reasons.append("Bollinger squeeze (breakout imminent)")
        
        direction = "haussier" if indicators.technical_signal == SignalType.BUY else "baissier"
        reason = f"Signal technique {direction}: {', '.join(reasons)}"
        
        return Signal(
            source=SignalSource.TECHNICAL,
            signal_type=indicators.technical_signal,
            token_address=indicators.token_address,
            token_symbol=indicators.token_symbol,
            score=score,
            reason=reason,
            data={
                "rsi": indicators.rsi,
                "macd_crossover": indicators.macd_crossover,
                "bb_position": indicators.bb_position,
                "bb_squeeze": indicators.bb_squeeze,
                "technical_score": indicators.technical_score,
            }
        )
