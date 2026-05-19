"""
SOLARIS - Main Entry Point
==========================
Point d'entrée principal du système de trading SOLARIS.

Usage:
    python main.py                     # Paper trading (simulation)
    python main.py --mode live         # Trading réel (DANGER)
    python main.py --mode backtest     # Backtesting
    python main.py --config my.yaml    # Configuration custom
"""

import asyncio
import argparse
import logging
import sys
import os
import json
from datetime import datetime, timezone
from pathlib import Path

# Ajouter le répertoire parent au path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import SolarisConfig, TradingMode, RiskLevel
from core.engine import SolarisEngine


def setup_logging(config: SolarisConfig):
    """Configure le logging"""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_format = "%H:%M:%S"
    
    logging.basicConfig(
        level=getattr(logging, config.monitoring.log_level),
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.monitoring.log_file),
        ]
    )


def print_banner(config: SolarisConfig):
    """Affiche la bannière SOLARIS"""
    banner = f"""
    
    ╔══════════════════════════════════════════════════════════╗
    ║                                                          ║
    ║   ███████╗ ██████╗ ██╗      █████╗ ██████╗ ███████╗    ║
    ║   ██╔════╝██╔═══██╗██║     ██╔══██╗██╔══██╗██╔════╝    ║
    ║   ███████╗██║   ██║██║     ███████║██████╔╝█████╗      ║
    ║   ╚════██║██║   ██║██║     ██╔══██║██╔══██╗██╔══╝      ║
    ║   ███████║╚██████╔╝███████╗██║  ██║██║  ██║███████╗    ║
    ║   ╚══════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝    ║
    ║                                                          ║
    ║   Solana On-chain Ledger Analysis                        ║
    ║   & Real-time Intelligence System                        ║
    ║                                                          ║
    ║   v{config.version}  |  Mode: {config.mode.value.upper():<10}              ║
    ║   Risk: {config.risk.risk_level.value.upper():<13}                            ║
    ║                                                          ║
    ╚══════════════════════════════════════════════════════════╝
    
    """
    print(banner)


def print_disclaimer():
    """Affiche l'avertissement de risque"""
    disclaimer = """
    ⚠️  AVERTISSEMENT IMPORTANT ⚠️
    
    Le trading de crypto-monnaies comporte des risques élevés.
    Vous pouvez perdre la totalité de votre capital.
    
    - SOLARIS est un outil d'aide à la décision, pas une garantie de profit
    - Les performances passées ne garantissent pas les résultats futurs
    - N'investissez jamais plus que ce que vous pouvez vous permettre de perdre
    - Le mode PAPER (simulation) est recommandé pour les tests
    
    En utilisant ce logiciel, vous acceptez l'entière responsabilité
    de vos décisions de trading.
    """
    print(disclaimer)


async def run_solaris(config: SolarisConfig):
    """Lance le système SOLARIS"""
    print_banner(config)
    
    if config.mode == TradingMode.LIVE:
        print_disclaimer()
        response = input("Êtes-vous sûr de vouloir trader en LIVE ? (oui/non): ")
        if response.lower() != "oui":
            print("Mode LIVE annulé. Passage en mode PAPER.")
            config.mode = TradingMode.PAPER
    
    # Configurer le logging
    setup_logging(config)
    
    # Créer et initialiser le moteur
    engine = SolarisEngine(config)
    
    try:
        await engine.initialize()
        
        # Sauvegarder le worklog
        worklog_path = Path("/home/z/my-project/worklog.md")
        with open(worklog_path, "a") as f:
            f.write(f"\n---\nTask ID: solaris-run\nAgent: SOLARIS Engine\n")
            f.write(f"Task: Run SOLARIS trading system\n")
            f.write(f"Work Log:\n")
            f.write(f"- Started at {datetime.now(timezone.utc).isoformat()}\n")
            f.write(f"- Mode: {config.mode.value}\n")
            f.write(f"- Portfolio: {config.risk.risk_level.value} risk\n\n")
        
        await engine.run()
        
    except KeyboardInterrupt:
        logger.info("Interruption clavier détectée")
    except Exception as e:
        logger.error(f"Erreur fatale: {e}", exc_info=True)
    finally:
        await engine.shutdown()


def main():
    """Point d'entrée CLI"""
    parser = argparse.ArgumentParser(
        description="SOLARIS - Solana Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  python main.py                        # Paper trading
  python main.py --mode live            # Trading réel
  python main.py --risk aggressive      # Risk level
  python main.py --sol 50               # Capital initial (paper)
        """
    )
    
    parser.add_argument(
        "--mode", 
        choices=["paper", "live", "backtest"],
        default="paper",
        help="Mode de trading (défaut: paper)"
    )
    
    parser.add_argument(
        "--risk",
        choices=["conservative", "moderate", "aggressive"],
        default="moderate",
        help="Niveau de risque (défaut: moderate)"
    )
    
    parser.add_argument(
        "--sol",
        type=float,
        default=10.0,
        help="Capital initial en SOL pour le paper trading (défaut: 10)"
    )
    
    parser.add_argument(
        "--rpc",
        type=str,
        help="URL RPC Solana personnalisée"
    )
    
    parser.add_argument(
        "--helius-key",
        type=str,
        help="Clé API Helius"
    )
    
    parser.add_argument(
        "--birdeye-key",
        type=str,
        help="Clé API Birdeye"
    )
    
    parser.add_argument(
        "--no-snipe",
        action="store_true",
        help="Désactiver le sniping de nouveaux tokens"
    )
    
    parser.add_argument(
        "--no-whale",
        action="store_true",
        help="Désactiver le suivi des baleines"
    )
    
    args = parser.parse_args()
    
    # Créer la configuration
    config = SolarisConfig()
    config.mode = TradingMode(args.mode)
    config.risk.risk_level = RiskLevel(args.risk)
    # Stocker le capital initial pour le paper trading
    config._portfolio_initial_sol = args.sol
    
    if args.rpc:
        config.solana.rpc_url = args.rpc
    if args.helius_key:
        config.solana.helius_api_key = args.helius_key
    if args.birdeye_key:
        config.solana.birdeye_api_key = args.birdeye_key
    if args.no_snipe:
        config.strategy.sniping_enabled = False
    if args.no_whale:
        config.strategy.whale_tracking_enabled = False
    
    # Lancer
    try:
        asyncio.run(run_solaris(config))
    except KeyboardInterrupt:
        print("\nSOLARIS arrêté.")


if __name__ == "__main__":
    main()
