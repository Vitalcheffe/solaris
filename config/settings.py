"""
SOLARIS - Configuration Settings
=================================
Solana On-chain Ledger Analysis & Real-time Intelligence System
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict
from enum import Enum


class TradingMode(Enum):
    PAPER = "paper"          # Simulation, pas d'argent réel
    LIVE = "live"            # Argent réel
    BACKTEST = "backtest"    # Test sur données historiques


class RiskLevel(Enum):
    CONSERVATIVE = "conservative"   # 1-2% par trade
    MODERATE = "moderate"           # 2-5% par trade
    AGGRESSIVE = "aggressive"       # 5-10% par trade


@dataclass
class SolanaConfig:
    """Configuration Solana RPC et connexion"""
    rpc_url: str = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    ws_url: str = os.getenv("SOLANA_WS_URL", "wss://api.mainnet-beta.solana.com")
    helius_api_key: str = os.getenv("HELIUS_API_KEY", "")
    birdeye_api_key: str = os.getenv("BIRDEYE_API_KEY", "")
    
    # Timeouts
    rpc_timeout: int = 30
    ws_reconnect_delay: int = 5
    max_retries: int = 3

    @property
    def helius_rpc_url(self) -> str:
        if self.helius_api_key:
            return f"https://mainnet.helius-rpc.com/?api-key={self.helius_api_key}"
        return self.rpc_url

    @property
    def helius_ws_url(self) -> str:
        if self.helius_api_key:
            return f"wss://mainnet.helius-rpc.com/?api-key={self.helius_api_key}"
        return self.ws_url


@dataclass
class WalletConfig:
    """Configuration du wallet pour l'exécution"""
    private_key: str = os.getenv("SOLANA_PRIVATE_KEY", "")  # Base58 encoded
    public_key: str = os.getenv("SOLANA_PUBLIC_KEY", "")
    
    # DEX
    default_slippage_bps: int = 100  # 1% slippage par défaut
    priority_fee_lamports: int = 100000  # 0.0001 SOL priority fee
    jito_tip_lamports: int = 100000  # 0.0001 SOL Jito tip


@dataclass
class StrategyConfig:
    """Configuration des stratégies individuelles"""
    
    # --- Whale Tracking ---
    whale_tracking_enabled: bool = True
    whale_min_sol_amount: float = 50.0        # Minimum SOL pour considérer comme baleine
    whale_wallets_to_track: List[str] = field(default_factory=lambda: [
        # Ces wallets seront populés dynamiquement via la détection smart money
    ])
    whale_follow_delay_ms: int = 500          # Délai avant de suivre un mouvement de baleine
    
    # --- Volume Analysis ---
    volume_analysis_enabled: bool = True
    volume_spike_threshold: float = 3.0       # x3 le volume moyen = spike
    volume_lookback_hours: int = 24           # Fenêtre de calcul du volume moyen
    volume_min_liquidity_sol: float = 10.0    # Liquidité minimum pour considérer
    
    # --- Technical Indicators ---
    technical_enabled: bool = True
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    
    # --- New Token Sniping ---
    sniping_enabled: bool = True
    sniping_max_buy_sol: float = 0.1          # Max SOL par snipe
    sniping_take_profit_pct: float = 50.0     # Take profit à +50%
    sniping_stop_loss_pct: float = 20.0       # Stop loss à -20%
    sniping_anti_rug_checks: bool = True
    sniping_min_liquidity_sol: float = 5.0
    sniping_max_honeypot_score: float = 0.3   # Score max pour honeypot (0-1)
    sniping_auto_sell_timeout_s: int = 300    # Auto-sell après 5 min si pas de profit


@dataclass
class ConfluenceConfig:
    """Configuration du moteur de confluence"""
    min_signals_for_entry: int = 2            # Minimum 2 signaux alignés
    signal_weights: Dict[str, float] = field(default_factory=lambda: {
        "whale_tracking": 0.35,      # Signal baleine = poids le plus fort
        "volume_analysis": 0.25,     # Volume anormal
        "technical": 0.20,           # Signal technique
        "smart_money": 0.15,         # Flux smart money
        "new_token": 0.05,           # Nouveau token legit
    })
    min_confluence_score: float = 0.6         # Score minimum pour entrer (0-1)
    cooldown_between_trades_s: int = 60       # 1 min entre chaque trade


@dataclass
class RiskConfig:
    """Configuration de gestion du risque"""
    risk_level: RiskLevel = RiskLevel.MODERATE
    
    # Position sizing
    max_position_size_pct: float = 5.0        # Max 5% du portefeuille par trade
    max_total_exposure_pct: float = 30.0      # Max 30% du portefeuille investi
    
    # Stop losses
    default_stop_loss_pct: float = 5.0        # Stop loss par défaut
    trailing_stop_enabled: bool = True
    trailing_stop_activation_pct: float = 5.0  # Active trailing stop à +5%
    trailing_stop_distance_pct: float = 2.0    # Trail de 2%
    
    # Daily limits
    max_daily_loss_pct: float = 3.0           # Max 3% perte/jour
    max_daily_trades: int = 20                # Max 20 trades/jour
    max_consecutive_losses: int = 5           # Pause après 5 pertes consécutives
    cooldown_after_max_losses_s: int = 3600   # 1h de pause après trop de pertes
    
    # Token safety
    max_single_token_exposure_pct: float = 10.0  # Max 10% sur un seul token


@dataclass
class MonitoringConfig:
    """Configuration du monitoring et alertes"""
    log_level: str = "INFO"
    log_file: str = "solaris.log"
    
    # Dashboard
    dashboard_enabled: bool = True
    dashboard_port: int = 8080
    dashboard_refresh_s: int = 5
    
    # Alerts
    telegram_enabled: bool = False
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    
    # Trade logging
    trade_log_file: str = "trades.json"
    performance_log_file: str = "performance.json"


@dataclass
class SolarisConfig:
    """Configuration principale SOLARIS"""
    app_name: str = "SOLARIS"
    version: str = "1.0.0"
    mode: TradingMode = TradingMode.PAPER
    
    solana: SolanaConfig = field(default_factory=SolanaConfig)
    wallet: WalletConfig = field(default_factory=WalletConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    confluence: ConfluenceConfig = field(default_factory=ConfluenceConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    
    # Tokens surveillés par défaut
    watchlist: List[str] = field(default_factory=lambda: [
        "SOL",    # Solana
        "BONK",   # Meme coin populaire
        "JUP",    # Jupiter DEX
        "WIF",    # Dog wif hat
        "JTO",    # Jito
    ])
