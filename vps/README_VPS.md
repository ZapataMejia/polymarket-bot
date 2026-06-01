# Migrar los bots de Polymarket al VPS (Windows)

Esta guía deja corriendo **24/7** los dos paper traders:

- **V1** — configuración por defecto (threshold 5pp). Usa `TELEGRAM_BOT_TOKEN`.
- **V2B** — Selective (threshold 15pp + filtros). Usa `TELEGRAM_BOT_TOKEN_V2`.

> Son **paper trading** (simulado): cero riesgo de dinero real.

---

## 1. Instalar Python y Git en el VPS (una sola vez)

1. **Python 3.12** → https://www.python.org/downloads/windows/
   - En el instalador, marca **"Add python.exe to PATH"** antes de "Install Now".
2. **Git** → https://git-scm.com/download/win (todo siguiente/next, opciones por defecto).

Verifica abriendo **PowerShell** y escribiendo:

```powershell
python --version
git --version
```

## 2. Clonar el repositorio

```powershell
cd $HOME
git clone https://github.com/ZapataMejia/<NOMBRE_DEL_REPO>.git polymarket-bot
cd polymarket-bot
```

(El nombre exacto del repo te lo paso al crearlo.)

## 3. Instalar dependencias

```powershell
powershell -ExecutionPolicy Bypass -File vps\setup.ps1
```

## 4. Crear el archivo `.env` con tus claves

Copia la plantilla y edítala:

```powershell
copy .env.example .env
notepad .env
```

Rellena al menos:

- `BINANCE_API_KEY`, `BINANCE_SECRET` (datos de mercado)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (bot V1)
- `TELEGRAM_BOT_TOKEN_V2` (bot V2B)

> Usa **los mismos tokens** que ya tenías en tu PC para no perder los chats de Telegram.

## 5. Arrancar los dos bots

```powershell
powershell -ExecutionPolicy Bypass -File vps\start_bots.ps1
```

Se abren dos ventanas minimizadas (V1 y V2B). Para ver que funcionan, revisa los logs:

```powershell
Get-Content logs\paper_v1.log -Tail 20 -Wait
Get-Content logs\paper_v2b.log -Tail 20 -Wait
```

## 6. Que arranquen solos al reiniciar el VPS (recomendado)

```powershell
powershell -ExecutionPolicy Bypass -File vps\install_autostart.ps1
```

Esto crea una tarea programada `PolymarketBots` que lanza ambos bots cada vez que
inicias sesión en el VPS.

---

## Comandos útiles

| Acción | Comando |
|---|---|
| Arrancar ambos bots | `powershell -ExecutionPolicy Bypass -File vps\start_bots.ps1` |
| Detener los bots | Cierra sus ventanas, o en PowerShell: `Get-Process python \| Stop-Process` |
| Actualizar el código (cuando cambiemos algo) | `git pull` y volver a arrancar |
| Ver log V1 en vivo | `Get-Content logs\paper_v1.log -Tail 30 -Wait` |
| Ver log V2B en vivo | `Get-Content logs\paper_v2b.log -Tail 30 -Wait` |

## Notas

- El histórico de paper trading viaja como **semilla** en `vps/seed/` y `setup.ps1`
  lo copia a `data/paper_trading/` (V1) y `data/paper_trading_v2b/` (V2B), así los
  bots **continúan** donde iban. El estado vivo NO se trackea en git (no estorba al `git pull`).
- Antes de arrancarlos aquí, **apaga los de tu PC** para no duplicar mensajes en
  el mismo bot de Telegram.
