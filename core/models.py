"""
SOLARIS - Data Models
=====================
Modèles de données pour le système de trading
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalSource(Enum):
    WHALE_TRACKING = "whale_tracking"
    VOLUME_ANALYSIS = "volume_analysis"
    TECHNICAL = "technical"
    SMART_MONEY = "smart_money"
    NEW_TOKEN = "new_token"


class TradeStatus(Enum):
    PENDING = "pending"
    EXECUTED = "executed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TokenRisk(Enum):
    SAFE = "safe"
    MEDIUM = "medium"
    RISKY = "risky"
    DANGEROUS = "dangerous"


@dataclass
class TokenInfo:
    """Information sur un token Solana"""
    address: str
    symbol: str
    name: str
    decimals: int = 9
    logo_uri: Optional[str] = None
    
    # Métriques on-chain
    price_usd: float = 0.0
    price_sol: float = 0.0
    volume_24h_usd: float = 0.0
    volume_24h_sol: float = 0.0
    liquidity_usd: float = 0.0
    liquidity_sol: float = 0.0
    market_cap_usd: float = 0.0
    holder_count: int = 0
    
    # Score de risque
    risk_score: float = 0.0  # 0 = safe, 1 = dangerous
    is_honeypot: bool = False
    is_mint_renounced: bool = False
    is_lp_burned: bool = False
    
    # Timestamp
    last_updated: datetime = field(default_factory=datetime.utcnow)


@dataclass
class WhaleTransaction:
    """Transaction d'une baleine détectée"""
    signature: str
    wallet_address: str
    token_address: str
    token_symbol: str
    signal_type: SignalType
    amount_sol: float
    amount_tokens: float
    price_sol: float
    timestamp: datetime
    
    # Classification du wallet
    wallet_label: Optional[str] = None  # ex: "Smart Money", "CEX", "Whale"
    wallet_win_rate: Optional[float] = None
    wallet_avg_roi: Optional[float] = None
    
    @property
    def is_smart_money(self) -> bool:
        return (
            self.wallet_win_rate is not None
            and self.wallet_win_rate > 0.6
            and self.wallet_avg_roi is not None
            and self.wallet_avg_roi > 0.1
        )


@dataclass
class VolumeData:
    """Données de volume pour un token"""
    token_address: str
    token_symbol: str
    current_volume_sol: float
    average_volume_sol: float
    volume_ratio: float  # current / average
    volume_change_1h_pct: float
    volume_change_4h_pct: float
    volume_change_24h_pct: float
    buy_volume_pct: float  # % du volume qui est de l'achat
    sell_volume_pct: float
    unique_buyers_1h: int
    unique_sellers_1h: int
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def is_spike(self) -> bool:
        return self.volume_ratio >= 3.0
    
    @property
    def is_buy_pressure(self) -> bool:
        return self.buy_volume_pct > 60.0


@dataclass
class TechnicalIndicators:
    """Indicateurs techniques calculés"""
    token_address: str
    token_symbol: str
    timeframe: str  # "1m", "5m", "15m", "1h", "4h", "1d"
    
    # Prix
    current_price: float
    price_change_1h_pct: float
    price_change_24h_pct: float
    
    # RSI
    rsi: float
    rsi_signal: str  # "overbought", "oversold", "neutral"
    
    # MACD
    macd_line: float
    macd_signal_line: float
    macd_histogram: float
    macd_crossover: str  # "bullish", "bearish", "none"
    
    # Bollinger Bands
    bb_upper: float
    bb_middle: float
    bb_lower: float
    bb_position: str  # "above_upper", "below_lower", "middle"
    bb_squeeze: bool  # True si les bands se resserrent
    
    # Moyennes mobiles
    sma_7: float
    sma_25: float
    sma_99: float
    ema_12: float
    ema_26: float
    
    # Signal composite
    technical_signal: SignalType = SignalType.HOLD
    technical_score: float = 0.0  # -1 (bearish) à +1 (bullish)
    
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class NewTokenEvent:
    """Nouveau token détecté sur Solana"""
    token_address: str
    token_symbol: str
    token_name: str
    creator_address: str
    launch_platform: str  # "pump.fun", "raydium", "orca"
    initial_liquidity_sol: float
    launch_time: datetime
    
    # Anti-rug checks
    is_mint_renounced: bool = False
    is_lp_burned: bool = False
    creator_history_score: float = 0.0  # 0 = nouveau, 1 = très fiable
    honeypot_score: float = 1.0  # 0 = safe, 1 = probablement honeypot
    top_holder_pct: float = 100.0  # % détenu par le plus gros holder
    
    # Métriques post-launch
    price_at_launch_sol: float = 0.0
    current_price_sol: float = 0.0
    price_change_pct: float = 0.0
    volume_5m_sol: float = 0.0
    buyers_5m: int = 0
    sellers_5m: int = 0
    
    @property
    def risk_level(self) -> TokenRisk:
        if self.honeypot_score > 0.5:
            return TokenRisk.DANGEROUS
        if self.top_holder_pct > 50 or not self.is_mint_renounced:
            return TokenRisk.RISKY
        if self.creator_history_score < 0.3 or not self.is_lp_burned:
            return TokenRisk.MEDIUM
        return TokenRisk.SAFE


@dataclass
class Signal:
    """Signal de trading généré par une stratégie"""
    source: SignalSource
    signal_type: SignalType
    token_address: str
    token_symbol: str
    score: float  # 0.0 à 1.0, confiance du signal
    reason: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ConfluenceResult:
    """Résultat du moteur de confluence - combine tous les signaux"""
    token_address: str
    token_symbol: str
    signals: List[Signal]
    confluence_score: float  # 0.0 à 1.0
    recommended_action: SignalType
    recommended_size_sol: float
    stop_loss_pct: float
    take_profit_pct: float
    reasons: List[str]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def is_actionable(self) -> bool:
        return (
            self.confluence_score >= 0.6
            and self.recommended_action != SignalType.HOLD
            and len(self.signals) >= 2
        )


@dataclass
class Trade:
    """Trade exécuté ou en cours"""
    id: str
    token_address: str
    token_symbol: str
    side: SignalType  # BUY or SELL
    entry_price_sol: float
    amount_sol: float
    amount_tokens: float
    stop_loss_sol: float
    take_profit_sol: float
    status: TradeStatus
    confluence_score: float
    signals: List[str]  # Sources des signaux
    
    # Exécution
    tx_signature: Optional[str] = None
    entry_time: datetime = field(default_factory=datetime.utcnow)
    exit_time: Optional[datetime] = None
    exit_price_sol: Optional[float] = None
    
    # Résultat
    pnl_sol: Optional[float] = None
    pnl_pct: Optional[float] = None
    
    # Trailing stop
    highest_price_sol: Optional[float] = None
    trailing_stop_sol: Optional[float] = None
    
    @property
    def is_open(self) -> bool:
        return self.status == TradeStatus.EXECUTED and self.exit_time is None
    
    @property
    def current_pnl_pct(self) -> Optional[float]:
        if self.exit_price_sol:
            return ((self.exit_price_sol - self.entry_price_sol) / self.entry_price_sol) * 100
        return None


@dataclass
class PortfolioState:
    """État du portefeuille"""
    total_sol: float = 0.0
    available_sol: float = 0.0
    invested_sol: float = 0.0
    
    open_positions: List[Trade] = field(default_factory=list)
    total_pnl_sol: float = 0.0
    total_pnl_pct: float = 0.0
    
    # Stats journalières
    daily_pnl_sol: float = 0.0
    daily_trades: int = 0
    daily_wins: int = 0
    daily_losses: int = 0
    consecutive_losses: int = 0
    
    # Stats globales
    total_trades: int = 0
    total_wins: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    
    last_updated: datetime = field(default_factory=datetime.utcnow)
