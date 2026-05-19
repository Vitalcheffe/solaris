# SOLARIS 🌟

**Solana On-chain Ledger Analysis & Real-time Intelligence System**

Système de trading algorithmique intelligent pour la crypto-monnaie sur Solana qui combine **4 sources de signaux indépendantes** en un seul score de confluence.

---

## 🧠 Concept : La Confluence

Un signal seul = du bruit. Mais quand **une baleine achète ET le volume explose ET le RSI est survendu**, la probabilité d'un bon trade augmente significativement.

| Signal | Poids | Description |
|--------|-------|-------------|
| 🐋 Whale Tracking | 35% | Suit les wallets "smart money" on-chain |
| 📊 Volume Analysis | 25% | Détecte les pics de volume + pression acheteuse |
| 📈 Technical | 20% | RSI, MACD, Bollinger Bands |
| 🆕 Token Sniping | 5% | Nouveaux tokens avec filtres anti-rug |

**→ Le système ne trade QUE quand 2+ signaux s'alignent et que le score dépasse 0.6/1.0**

---

## 🚀 Quick Start

```bash
# Installer les dépendances
pip install -r requirements.txt

# Configurer les API keys (gratuites)
cp .env.example .env
# Éditer .env avec vos clés

# Lancer en mode simulation (paper trading)
python main.py --sol 10

# Lancer avec des options
python main.py --risk aggressive --no-snipe --sol 50
```

## 🛡️ Risk Management Intégré

- Max 5% du portefeuille par trade
- Max 30% d'exposition totale
- Stop-loss + trailing stop automatiques
- Pause après 5 pertes consécutives
- Limite de perte journalière (3%)

## 📁 Architecture

```
solaris/
├── main.py                     # Point d'entrée CLI
├── config/settings.py          # Configuration complète
├── core/
│   ├── engine.py               # Moteur principal
│   └── models.py               # Modèles de données
├── data/
│   ├── onchain_fetcher.py      # Données on-chain Solana
│   └── price_feed.py           # Flux de prix temps réel
├── strategies/
│   ├── whale_tracker.py        # Suivi des baleines
│   ├── volume_analyzer.py      # Analyse des volumes
│   ├── technical_analyzer.py   # RSI, MACD, Bollinger
│   ├── token_sniper.py         # Sniping + anti-rug
│   ├── confluence_engine.py    # Combinaison des signaux
│   └── backtest.py             # Backtesting
├── risk/manager.py             # Gestion du risque
├── execution/executor.py       # Exécution des trades
├── monitoring/dashboard.py     # Dashboard temps réel
├── utils/helpers.py            # Utilitaires
└── tests/test_solaris.py       # 22 tests unitaires
```

## ⚙️ API Keys (gratuites)

| Service | URL | Usage |
|---------|-----|-------|
| Helius | https://dev.helius.xyz | RPC Solana (1M req/mois gratuit) |
| Birdeye | https://birdeye.so | Données de marché, prix, volume |

## ⚠️ Avertissement

Le trading de crypto-monnaies comporte des risques très élevés. Ce logiciel est fourni à des fins éducatives et expérimentales. N'investissez jamais plus que ce que vous pouvez vous permettre de perdre.

## 📄 License

MIT
