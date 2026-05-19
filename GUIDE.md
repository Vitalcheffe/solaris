# SOLARIS - Guide Complet

## 🌟 Solana On-chain Ledger Analysis & Real-time Intelligence System

---

## 📋 Table des matières

1. [Qu'est-ce que SOLARIS ?](#quest-ce-que-solaris-)
2. [Architecture](#architecture)
3. [Installation](#installation)
4. [Configuration](#configuration)
5. [Stratégies](#stratégies)
6. [Moteur de Confluence](#moteur-de-confluence)
7. [Gestion du Risque](#gestion-du-risque)
8. [Utilisation](#utilisation)
9. [Roadmap](#roadmap)

---

## Qu'est-ce que SOLARIS ?

SOLARIS est un système de trading algorithmique pour la crypto-monnaie sur Solana qui combine **4 sources de signaux indépendantes** en un seul score de confluence pour prendre des décisions de trading.

### Pourquoi c'est différent du copy trading ?

| Copy Trading | SOLARIS |
|---|---|
| Suit un seul trader | Combine 4+ sources de données |
| Pas de compréhension du "pourquoi" | Chaque signal est explicite et pondéré |
| Latence (tu arrives après) | Temps réel via WebSocket on-chain |
| Un seul point de défaillance | Confluence = diversification des signaux |
| Pas de gestion du risque | Risk Manager intégré |

### Le concept clé : la Confluence

Un signal seul peut être du bruit. Mais quand **une baleine achète ET le volume explose ET le RSI est survendu**, la probabilité d'un bon trade augmente significativement. C'est comme 3 médecins qui font le même diagnostic indépendamment.

---

## Architecture

```
SOLARIS/
├── main.py                    # Point d'entrée CLI
├── config/
│   └── settings.py            # Configuration complète
├── core/
│   ├── engine.py              # Moteur principal (orchestrateur)
│   └── models.py              # Modèles de données
├── data/
│   ├── onchain_fetcher.py     # Données on-chain (RPC, Birdeye)
│   └── price_feed.py          # Flux de prix temps réel
├── strategies/
│   ├── whale_tracker.py       # Suivi des baleines
│   ├── volume_analyzer.py     # Analyse des volumes DEX
│   ├── technical_analyzer.py  # RSI, MACD, Bollinger
│   ├── token_sniper.py        # Détection nouveaux tokens
│   └── confluence_engine.py   # Combinaison des signaux
├── risk/
│   └── manager.py             # Gestion du risque
├── execution/
│   └── executor.py            # Exécution des trades
├── monitoring/
│   └── dashboard.py           # Dashboard temps réel
└── utils/
```

### Flux de données

```
Solana Blockchain (RPC/WebSocket)
        ↓
  OnChainFetcher → PriceFeed
        ↓              ↓
  WhaleTracker   VolumeAnalyzer   TechnicalAnalyzer   TokenSniper
        ↓              ↓                ↓                 ↓
        └──────────────┴────────────────┴─────────────────┘
                              ↓
                    ConfluenceEngine
                              ↓
                      RiskManager
                              ↓
                    TradeExecutor
                              ↓
                   Dashboard / Alerts
```

---

## Installation

### Prérequis

```bash
# Python 3.9+
python --version

# Installer les dépendances
pip install aiohttp solders solana-py
```

### Variables d'environnement (optionnel mais recommandé)

```bash
# RPC Helius (gratuit, 1M requests/mois)
export HELIUS_API_KEY="votre-cle-helius"

# Birdeye API (gratuit pour le basic)
export BIRDEYE_API_KEY="votre-cle-birdeye"

# Wallet (seulement pour le mode LIVE)
export SOLANA_PUBLIC_KEY="votre-adresse"
export SOLANA_PRIVATE_KEY="votre-cle-privee-base58"
```

### Obtenir les clés API gratuites

1. **Helius** : https://dev.helius.xyz → Créer un compte → Copier la clé API
2. **Birdeye** : https://birdeye.so → Settings → API Key → Copier

---

## Configuration

Le système est entièrement configurable via `config/settings.py` :

### Modes de Trading

| Mode | Description | Risque |
|------|------------|--------|
| `paper` | Simulation, pas d'argent réel | Aucun |
| `live` | Trading réel avec votre SOL | **Élevé** |
| `backtest` | Test sur données historiques | Aucun |

### Niveaux de Risque

| Niveau | Taille max/trade | Exposition max | Stop-loss |
|--------|-----------------|----------------|-----------|
| `conservative` | 1-2% | 15% | Serré (3%) |
| `moderate` | 2-5% | 30% | Moyen (5%) |
| `aggressive` | 5-10% | 50% | Large (7%) |

### Seuils de Confluence

- **Score minimum** : 0.6 (sur 1.0) pour entrer en trade
- **Signaux minimum** : 2 sources différentes doivent s'aligner
- **Poids des sources** :
  - Whale Tracking : 35% (le plus fiable)
  - Volume Analysis : 25%
  - Technical : 20%
  - Smart Money : 15%
  - New Token : 5% (le moins fiable seul)

---

## Stratégies

### 1. 🐋 Whale Tracking (Poids: 35%)

**Principe** : Suivre les gros portefeuilles qui ont une histoire de profits.

**Comment ça marche** :
- Scanne les transactions > 50 SOL sur les DEX
- Identifie les wallets "Smart Money" (win rate > 60%, ROI > 10%)
- Génère un signal pondéré par la fiabilité du wallet

**Score de confiance** :
- Base : 0.3
- + Smart Money identifié : +0.2
- + ROI élevé (> 10%) : +0.15
- + Montant élevé (> 100 SOL) : +0.1
- + Wallet labelisé : +0.1

### 2. 📊 Volume Analysis (Poids: 25%)

**Principe** : Un pic de volume signale que quelque chose se passe.

**Comment ça marche** :
- Calcule le ratio volume actuel / volume moyen
- Détecte les spikes > 3x la moyenne
- Mesure la pression acheteuse vs vendeuse
- Compte les acheteurs/vendeurs uniques

**Signals forts** :
- Volume 3x + pression acheteuse 70%+ = signal haussier
- Volume 5x + pression acheteuse 80%+ = signal très fort

### 3. 📈 Technical Analysis (Poids: 20%)

**Indicateurs utilisés** :
- **RSI** : Survente (< 30) = opportunité d'achat, Surachat (> 70) = signal de vente
- **MACD** : Crossover haussier/baissier
- **Bollinger Bands** : Prix sous la bande inférieure = survendu, squeeze = breakout imminent
- **SMA/EMA** : Tendance court terme vs long terme

**Score composite** : Combinaison pondérée de tous les indicateurs (-1 à +1)

### 4. 🆕 Token Sniping (Poids: 5%)

**Principe** : Détecter les nouveaux tokens avec filtres anti-rug pull.

**Filtres obligatoires** :
1. Liquidité minimum (configurable, défaut: 5 SOL)
2. Score honeypot < 0.3
3. Top holder < 50% de l'offre
4. Vérification RugCheck

**AVERTISSEMENT** : C'est la stratégie la plus risquée. 90% des nouveaux tokens sont des rug pulls.

---

## Moteur de Confluence

### Comment ça marche

```
Signal 1: [WHALE] BUY SOL, score=0.8   × 0.35 = 0.28
Signal 2: [VOLUME] BUY SOL, score=0.7  × 0.25 = 0.175
Signal 3: [TECH] BUY SOL, score=0.6    × 0.20 = 0.12
                                         Total = 0.575
                                    + 3 sources (×1.15) = 0.66
                                    > Seuil 0.6 → BUY ✅
```

### Exemples de confluence

| Signaux | Score | Action |
|---------|-------|--------|
| Whale 0.8 + Volume 0.7 + Tech 0.6 | 0.66 | **BUY** |
| Whale 0.7 + Volume 0.6 | 0.41 | HOLD (score trop bas) |
| Whale BUY 0.8 + Whale SELL 0.9 | Conflicting | HOLD |
| Volume 0.9 seul | 0.23 | HOLD (1 source) |

---

## Gestion du Risque

### Règles automatiques

1. **Taille max par trade** : 5% du portefeuille (modéré)
2. **Exposition totale max** : 30% du portefeuille
3. **Perte journalière max** : 3% du portefeuille
4. **Max 20 trades/jour**
5. **Pause 1h après 5 pertes consécutives**
6. **Max 10% sur un seul token**
7. **Trailing stop automatique** : Active à +5%, trail de 2%

### Stop-Loss / Take-Profit

| Confluence | Stop-Loss | Take-Profit | Ratio R/R |
|-----------|-----------|-------------|-----------|
| ≥ 0.8 | 3% | 15% | 1:5 |
| ≥ 0.6 | 5% | 10% | 1:2 |
| < 0.6 | 7% | 10% | 1:1.4 |

---

## Utilisation

### Paper Trading (recommandé pour commencer)

```bash
cd solaris
python main.py --sol 10
```

### Mode LIVE (⚠️ argent réel)

```bash
python main.py --mode live --helius-key VOTRE_CLE
```

### Options CLI

```bash
python main.py --help

Options:
  --mode {paper,live,backtest}   Mode de trading
  --risk {conservative,moderate,aggressive}  Niveau de risque
  --sol FLOAT                    Capital initial (paper trading)
  --rpc URL                      RPC Solana personnalisé
  --helius-key KEY               Clé API Helius
  --birdeye-key KEY              Clé API Birdeye
  --no-snipe                     Désactiver le sniping
  --no-whale                     Désactiver le suivi baleines
```

---

## Roadmap

- [x] Architecture multi-stratégie
- [x] Moteur de confluence
- [x] Risk management
- [x] Paper trading
- [ ] Backtesting complet
- [ ] Exécution LIVE via Jupiter
- [ ] Dashboard web (Flask/FastAPI)
- [ ] Alertes Telegram
- [ ] Smart Money auto-détection
- [ ] Support multi-chaînes
- [ ] Machine Learning pour l'optimisation des poids

---

## ⚠️ Avertissement

Ce logiciel est fourni à des fins éducatives et expérimentales. Le trading de crypto-monnaies comporte des risques très élevés. Les performances passées ne garantissent pas les résultats futurs. N'investissez jamais plus que ce que vous pouvez vous permettre de perdre.
