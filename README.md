# Trading AI

Sistema de trading algorítmico con inteligencia artificial.

## Setup rápido

```bash
# 1. Crear entorno virtual
python3 -m venv .venv
source .venv/bin/activate

# 2. Instalar dependencias
pip install -e ".[dev,ml]"

# 3. macOS: instalar OpenMP (necesario para XGBoost/LightGBM)
brew install libomp

# 4. Copiar configuración
cp .env.example .env
# Editar .env con tus API keys (opcional para datos públicos)

# 5. Inicializar base de datos
python main.py init-db
```

## Comandos disponibles

| Comando | Qué hace | Internet? |
|---|---|---|
| `python main.py collect` | Baja datos históricos de Binance | Sí (una vez) |
| `python main.py backtest` | Prueba estrategias con datos pasados | No |
| `python main.py train-ml` | Entrena modelos XGBoost/LightGBM | No |
| `python main.py train-rl` | Entrena agente de Reinforcement Learning | No |
| `python main.py paper-trade` | Trading simulado con datos en vivo | Sí (24/7) |
| `python main.py live-collect` | Recolección continua de datos | Sí (24/7) |
| `python main.py init-db` | Crear tablas en la base de datos | No |
| `python main.py tsmom-alert` | Resumen TSMOM + Telegram (solo **viernes UTC**) | Sí |
| `python main.py tsmom-alert --force` | Igual, cualquier día (pruebas) | Sí |
| `python main.py poly-edge --days 3 --spread-sensitivity` | Mide empíricamente la "edge" de los mercados Polymarket *Up/Down* 5/15 min vs Binance | Sí |

Parámetros TSMOM en `config/default.yaml` → sección **`tsmom:`** (rebalanceo, vol objetivo, etc.).

### `poly-edge` — verificación empírica del bot de Polymarket

Prueba la tesis central del hilo de "bot de $1M en Polymarket": ¿realmente existe edge
explotable entre el mid de Polymarket y Binance en los mercados de 5/15 minutos?

El comando:

1. Baja todos los mercados *Up/Down* resueltos en la ventana (Gamma API).
2. Para cada uno, baja el histórico de precios del CLOB y velas 1m de Binance.
3. Calcula la probabilidad **justa** P(Up) con un modelo log-normal sin drift.
4. Detecta señales cuando |p_fair − p_poly| supera un umbral.
5. Simula la ejecución con medio spread + fee aplicados.
6. Reporta: win-rate, PnL, calibración del modelo, sensibilidad al spread.

Flags útiles:

```bash
python main.py poly-edge --days 7                        # ventana de 7 días
python main.py poly-edge --start 2026-05-01 --end 2026-05-15
python main.py poly-edge --threshold 0.05                # umbral 5pp
python main.py poly-edge --half-spread-cents 5           # asumir spread más amplio
python main.py poly-edge --spread-sensitivity            # tabla con spreads 1¢-10¢
python main.py poly-edge --csv data/poly_edge.csv        # exportar por mercado
```

**Limitaciones conocidas** (las imprime el script al final):

- Resolución 1 min en Polymarket; no medimos el edge sub-segundo del que habla el artículo.
- Usamos *mid* en vez de *bid/ask* real → infraestima costos.
- No modelamos *fillability* (la profundidad del libro se la come otro bot en milisegundos).
- Tenemos hasta resolución (binarios pagan 0/1, no hay slippage de salida).
- 3 días = muestra pequeña. Correr con `--days 14+` para conclusiones estables.

## Correr tests

```bash
# Unit tests (sin internet)
python -m pytest tests/unit/ -v

# Integration tests (requiere internet)
python -m pytest tests/integration/ -v -m integration
```

## Estrategias destacadas

- **`RegimeAdaptiveUltraConservative`** — intradía/1h, spot sin cortos; paper-trading usa esta.
- **`tsmom_vol_weekly`** — **TSMOM** (momentum temporal) en **diario**: largo si la mayoría de lookbacks (63/126/252 días o adaptados si hay poco histórico) son positivos; si no, **caja**. Rebalanceo **viernes** (`W-FRI`). **Vol targeting** ~12 % anual con tope 1×. Se ejecuta al final de `backtest` usando velas 1h resampleadas a día.

Para lookbacks clásicos 63/126/252, sube `history_days` en `config/default.yaml` (p. ej. 400+) y vuelve a correr `collect`.

## Estructura del proyecto

```
src/
├── core/           Config, Logger, Database
├── data/           Exchange client, Parquet storage, Collector
├── features/       15 indicadores técnicos + pipeline de 50+ features
├── strategies/     Mean Reversion, Trend Following, Breakout + Backtesting
├── models/         XGBoost, LightGBM, RL Agent (Q-learning)
├── execution/      Paper trading, MetaTrader 5
├── risk/           Position sizing, circuit breaker, daily limits
└── notifications/  Alertas Telegram
```

## Costos

- **Backtesting/entrenamiento**: $0 (todo offline)
- **Paper trading**: $0 (datos públicos gratuitos)
- **Trading en vivo**: comisión de ~0.1% por trade
- **VPS (opcional)**: $5-20/mes para operación 24/7
