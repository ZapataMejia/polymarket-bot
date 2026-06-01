# Historial y contexto — Migración de los bots de Polymarket al VPS

> Documento de referencia de TODO lo que se hizo para mover los dos paper traders
> de Polymarket desde el PC local a un VPS Windows, dejándolos corriendo 24/7.
> Fecha de la migración: **1 de junio de 2026**.

---

## 1. Objetivo

Los dos bots de Polymarket corrían en el PC local y se apagaban cuando se cerraba
el equipo. Se migraron al **VPS de StrategyQuant (NeuraVPS, Windows Server)** para
que operen **24/7** de forma independiente del PC.

Son **paper trading** (operativa **simulada**, sin dinero real). Usan datos
públicos de **Polymarket** (Gamma API) y de **Binance** (klines públicas vía ccxt)
y notifican por **Telegram**.

---

## 2. Qué son los dos bots

Ambos ejecutan el mismo programa (`scripts/run_paper_trader.py`) con parámetros
distintos. Cada uno escribe en su propio estado, log y bot de Telegram.

### Bot V1 — "por defecto"
- Bankroll inicial: **$100**
- `threshold = 0.05` (umbral de edge 5pp)
- Sizing: Kelly 0.25, máx 10% por trade
- Series: `btc / eth / solana / xrp -up-or-down-hourly`
- Telegram: **`TELEGRAM_BOT_TOKEN`**
- Estado: `data/paper_trading/state.json`
- Log: `logs/paper_v1.log`

### Bot V2B — "Selective"
- Bankroll inicial: **$100**
- `threshold = 0.15` (umbral 15pp) + filtros:
  - `min-volume = 5000` (USD)
  - `skip-hours-utc = 21 23`
  - `skip-weekdays = Saturday`
- Sizing: Kelly 0.50, máx 20% por trade
- Telegram: **`TELEGRAM_BOT_TOKEN_V2`** (segundo bot, distinto al de V1)
- Estado: `data/paper_trading_v2b/state.json`
- Log: `logs/paper_v2b.log`

---

## 3. Decisiones de arquitectura

- **Transferencia vía GitHub privado** (no copia manual de archivos): permite
  actualizar después con `git pull` y deja el código respaldado.
  - Repo: **`https://github.com/ZapataMejia/polymarket-bot`** (cuenta personal
    ZapataMejia, **privado**).
  - Para clonar sin meter cuentas personales en el VPS, se puso el repo **público
    2 minutos**, se clonó, y se volvió a **privado**.
- **Python puro, sin Docker**: el paper trader no necesita Docker, así que en
  Windows es solo instalar Python + dependencias y ejecutar. (Los bots de
  freqtrade-lab, que sí usan Docker, NO se migraron — eran otra cosa.)
- **Secretos fuera del repo**: el `.env` está en `.gitignore`; nunca se subió.
- **Dependencias mínimas**: `requirements-bot.txt` (sin librerías pesadas de
  ML/RL como torch o gymnasium, que no hacen falta para el paper trader).

---

## 4. Qué hay en el VPS

- Ruta del proyecto: **`C:\Users\Administrador\polymarket-bot`**
- Python **3.13.13 (64-bit)** instalado con "Add to PATH".
- Git **2.54.0** (instalado con `winget install --id Git.Git`).
- Entorno virtual: `.venv\` (creado por `vps\setup.ps1`).
- Archivo `.env` con las claves reales (copiado/pegado desde el `.env` del Mac).

### Scripts creados (carpeta `vps/`)
| Archivo | Para qué |
|---|---|
| `setup.ps1` | Crea el `.venv` e instala dependencias. Correr UNA vez. |
| `run_v1.ps1` | Corre el bot V1 con auto-reinicio (loop). |
| `run_v2b.ps1` | Corre el bot V2B con auto-reinicio. Lee `TELEGRAM_BOT_TOKEN_V2` del `.env`. |
| `start_bots.ps1` | Lanza V1 y V2B, cada uno en su ventana minimizada. |
| `install_autostart.ps1` | Registra la tarea programada `PolymarketBots` (arranque al iniciar sesión). |
| `README_VPS.md` | Guía operativa paso a paso. |

---

## 5. Pasos que se ejecutaron en el VPS (en orden)

1. Instalar **Python 3.13.13** (marcando "Add python.exe to PATH").
2. Instalar **Git** (`winget install --id Git.Git -e`), reabrir PowerShell.
3. Clonar el repo:
   ```powershell
   cd $HOME
   git clone https://github.com/ZapataMejia/polymarket-bot.git
   cd polymarket-bot
   ```
4. Instalar dependencias:
   ```powershell
   powershell -ExecutionPolicy Bypass -File vps\setup.ps1
   ```
5. Crear el `.env` (copiando los valores del `.env` del Mac):
   ```powershell
   copy .env.example .env
   notepad .env
   ```
6. Arrancar los bots:
   ```powershell
   powershell -ExecutionPolicy Bypass -File vps\start_bots.ps1
   ```
7. Instalar el auto-arranque:
   ```powershell
   powershell -ExecutionPolicy Bypass -File vps\install_autostart.ps1
   ```

---

## 6. Problemas encontrados y cómo se resolvieron

### 6.1. Error de DNS en Windows (`Could not contact DNS servers`)
- **Síntoma**: los bots no resolvían `gamma-api.polymarket.com` ni
  `api.telegram.org`, aunque git/pip/navegador sí tenían internet.
- **Causa**: `aiodns` (resolvedor c-ares que arrastra `ccxt`) no detecta los
  servidores DNS en este Windows.
- **Solución permanente (en el código)**: en `scripts/run_paper_trader.py` se
  fuerza el `ThreadedResolver` de aiohttp (usa el DNS del sistema operativo):
  ```python
  import aiohttp.connector as _aiohttp_connector
  from aiohttp.resolver import ThreadedResolver as _ThreadedResolver
  _aiohttp_connector.DefaultResolver = _ThreadedResolver
  ```
- **Solución rápida aplicada esa vez**: `pip uninstall -y aiodns` en el `.venv`.

### 6.2. Reset a $100 (arrancar de cero)
- Al migrar se conservó el histórico (semilla en `vps/seed/`), pero V1 venía en
  **$29.46** (por debajo del piso de $30 → pausaba entradas).
- Se decidió **arrancar de cero en $100** ambos bots:
  ```powershell
  Remove-Item data\paper_trading\state.json -ErrorAction SilentlyContinue
  Remove-Item data\paper_trading_v2b\state.json -ErrorAction SilentlyContinue
  Remove-Item vps\seed\state_v1.json, vps\seed\state_v2b.json -ErrorAction SilentlyContinue
  ```
- Se quitaron las semillas del repo y el seeding de `setup.ps1` se hizo
  **opcional** (por defecto ya no hay semilla → arranca limpio).

---

## 7. Operación y mantenimiento

### Encender / reiniciar manualmente
```powershell
cd $HOME\polymarket-bot
powershell -ExecutionPolicy Bypass -File vps\start_bots.ps1
```

### Ver que están operando
```powershell
Get-Content logs\paper_v1.log -Tail 20 -Wait    # V1  (Ctrl+C para salir)
Get-Content logs\paper_v2b.log -Tail 20 -Wait   # V2B
```

### Detener los bots
- Cierra las dos ventanas azules `[V1]` y `[V2B]`, o:
```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process powershell -ErrorAction SilentlyContinue | Stop-Process -Force
```

### Actualizar el código cuando se cambie algo
> El repo es privado: para `git pull` hay que autenticar (o ponerlo público un
> momento, como en la migración).
```powershell
cd $HOME\polymarket-bot
git pull
powershell -ExecutionPolicy Bypass -File vps\start_bots.ps1
```

---

## 8. Reglas de oro (muy importante)

- Las **dos ventanas azules `[V1]` y `[V2B]` corren DENTRO del VPS** → déjalas
  siempre abiertas. Cerrarlas = apagar ese bot.
- **Cerrar la "Windows App" (escritorio remoto) en el Mac = solo desconecta la
  pantalla.** El VPS sigue encendido y los bots siguen operando aunque apagues el
  Mac.
- **NUNCA** "Cerrar sesión / Sign out" dentro del VPS si quieres que sigan
  corriendo en esa sesión. Usa **"Desconectar"**.
- El auto-arranque (`PolymarketBots`) los relanza al **iniciar sesión** en el VPS
  (p. ej. tras un reinicio, cuando vuelvas a entrar por escritorio remoto).
- Para no duplicar mensajes en Telegram: si alguna vez vuelves a encender los
  bots en el PC, recuerda que los del VPS ya están mandando.

---

## 9. Claves usadas (en el `.env`, NO en el repo)
- `BINANCE_API_KEY`, `BINANCE_SECRET` → datos de mercado (Binance).
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` → bot de Telegram de V1.
- `TELEGRAM_BOT_TOKEN_V2` → bot de Telegram de V2B.
- (`BYBIT_*` y `OPENAI_API_KEY` existen pero el paper trader no los usa.)

---

## 10. Estado final (al cierre de la migración)
- ✅ V1 y V2B corriendo en el VPS, de cero en **$100** cada uno.
- ✅ DNS resuelto; conexión OK a Polymarket, Binance y Telegram.
- ✅ Mensajes de Telegram **confirmados** llegando.
- ✅ Auto-arranque instalado (tarea `PolymarketBots`, estado *Ready*).
- ✅ Código respaldado en GitHub privado `ZapataMejia/polymarket-bot`.
