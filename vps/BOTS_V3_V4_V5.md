# Bots V3, V4 y V5 — 5 estrategias en paralelo

> Documento operativo para los nuevos bots añadidos sobre la base de V1 + V2B
> que ya corrían en el VPS. Fecha: **1 de junio de 2026**.

---

## 1. Resumen de las 5 estrategias

| Bot | WR objetivo | Estrategia | Engine | Notas |
|---|---:|---|---|---|
| **V1** | ~53% | Latency arb genérico | `run_paper_trader.py` | threshold 5pp |
| **V2B** | ~64% | Latency arb selectivo | `run_paper_trader.py` | threshold 15pp + filtros |
| **V3 SumOne** | **~95%** | Sum-to-one arbitrage | `run_paper_sumone.py` | Compra YES+NO cuando suman < $1 (risk-free) |
| **V4 Endgame** | **~95%** | Latency arb solo últimos 5 min | `run_paper_trader.py` | threshold 30pp + max-time 300s |
| **V5 Maker** | **~80%** | Latency arb high-conviction | `run_paper_trader.py` | threshold 20pp + skip noche/finde |

Los 5 corren **independientes**, cada uno con su bot de Telegram, su bankroll y su estado.

---

## 2. Telegram bots requeridos

Tres bots NUEVOS hay que crear en `@BotFather` (mismo procedimiento que V1/V2B):

| Bot | Token en `.env` | Sugerencia username |
|---|---|---|
| V3 SumOne | `TELEGRAM_BOT_TOKEN_V3` | `@Santiago_trades_v3_bot` |
| V4 Endgame | `TELEGRAM_BOT_TOKEN_V4` | `@Santiago_trades_v4_bot` |
| V5 Maker | `TELEGRAM_BOT_TOKEN_V5` | `@Santiago_trades_v5_bot` |

Pasos por bot:
1. `@BotFather` → `/newbot` → seguir prompts.
2. Copiar token.
3. **Mandar `/start`** al bot recién creado (sino no puede enviarte mensajes).
4. Pegar el token en `.env` del VPS.

> El `chat_id` (`TELEGRAM_CHAT_ID`) es el mismo para los 5 — sigue siendo el tuyo.

`.env` del VPS queda así (los 3 nuevos al final):
```
BINANCE_API_KEY=...
BINANCE_SECRET=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_BOT_TOKEN_V2=...
TELEGRAM_BOT_TOKEN_V3=...
TELEGRAM_BOT_TOKEN_V4=...
TELEGRAM_BOT_TOKEN_V5=...
```

---

## 3. Deployment en el VPS

### 3.1. Actualizar código del repo
```powershell
cd $HOME\polymarket-bot
git pull
```

### 3.2. Editar `.env` con los 3 tokens nuevos
```powershell
notepad .env
```

### 3.3. Detener los bots actuales y arrancar los 5
```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process powershell | Where-Object { $_.MainWindowTitle -match "V1|V2B|V3|V4|V5" } | Stop-Process -Force
powershell -ExecutionPolicy Bypass -File vps\start_bots.ps1
```

Cinco ventanas minimizadas deben aparecer: `[V1]`, `[V2B]`, `[V3]`, `[V4]`, `[V5]`.

### 3.4. Confirmar que cada bot mandó "🤖 Bot encendido"
Abrí los 5 chats de Telegram. Cada uno debe haber recibido el mensaje de bienvenida.

---

## 4. Estrategia: detalle por bot

### V3 SumOne (`scripts/run_paper_sumone.py`)
- En cada mercado Up/Down, fetcha precio de UP y DOWN.
- Si `up + down < $1 - margin`, compra AMBOS lados.
- Como exactamente UNO de los dos paga $1 al cierre → **plata garantizada**.
- En la práctica los eventos sum<1 son **raros** (1-3 por día) pero **risk-free**.
- Poll cada 15s (vs 30s de V1/V2B) — necesario porque las ventanas duran 30-90s.
- Position cap: 10% bankroll o $200 absoluto por arb.

### V4 Endgame (`run_paper_trader.py` con flags)
```
--threshold 0.30
--max-seconds-to-resolution 300
--min-seconds-to-resolution 90
```
- Solo entra cuando faltan ≤ 5 min para la resolución del mercado.
- Solo entra con edge ≥ 30pp (vs 5pp V1, 15pp V2B).
- A esa distancia + ese edge, fair_prob suele estar > 95% — bet de alta convicción.
- Muy pocos trades (1-5 por día) pero alta WR esperada.

### V5 Maker (`run_paper_trader.py` con flags)
```
--threshold 0.20 --min-volume 8000
--skip-hours-utc 0 1 2 21 22 23
--skip-weekdays Saturday Sunday
--kelly-fraction 0.50 --max-pct-per-trade 0.20
```
- Solo opera en mercados líquidos (>$8k volumen) y en horas con mayor profundidad.
- Threshold 20pp = intermedio entre V2B y V4.
- Posición más grande (20% bankroll cap) porque cada trade tiene más convicción.
- Trades esperados: 5-15 por día.

---

## 5. Reglas operativas

- **Cada bot escribe en su estado propio**: borrar uno NO afecta a los otros.
  - `data/paper_trading/state.json` → V1
  - `data/paper_trading_v2b/state.json` → V2B
  - `data/paper_trading_v3/state.json` → V3
  - `data/paper_trading_v4/state.json` → V4
  - `data/paper_trading_v5/state.json` → V5
- **Cada bot escribe en su log propio** (`logs/paper_v1.log`, `paper_v2b.log`, ...).
- **Todos arrancan con $100** — comparable contra el otro al cabo de la semana.
- **Si querés reset a $100 de un bot**: borrá el `state.json` correspondiente y reiniciá.

---

## 6. Auto-arranque

El task scheduler `PolymarketBots` (instalado en la migración) ya invoca
`start_bots.ps1`. Al actualizar este script para lanzar los 5 bots, **el
auto-arranque cubre los 5 automáticamente** después del próximo `git pull`.

---

## 7. Después de 1 semana

Sacar el balance comparativo de los 5:
- Bankroll final ($)
- ROI (%)
- Total trades / WR (%)
- Avg profit per trade
- Drawdown

El bot ganador (mejor riesgo/retorno) es candidato para dinero real.
