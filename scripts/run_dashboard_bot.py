"""Entry point para el Dashboard Bot de Telegram.

Lee TELEGRAM_BOT_TOKEN_DASHBOARD del .env y arranca el long-polling loop.
Lee los state.json de los 5 bots desde data/paper_trading*/state.json.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Permitir importar desde src/
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Intentar cargar .env si existe (sin pip install python-dotenv)
def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv(ROOT / ".env")

from src.polymarket.dashboard_bot import run  # noqa: E402


def main() -> int:
    log_file = os.environ.get("DASHBOARD_LOG_FILE", str(ROOT / "logs" / "dashboard_bot.log"))
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-5s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(log_file, mode="a", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    token = os.environ.get("TELEGRAM_BOT_TOKEN_DASHBOARD", "").strip()
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN_DASHBOARD no esta seteado en .env", file=sys.stderr)
        print("Agrega esta linea al .env:", file=sys.stderr)
        print("  TELEGRAM_BOT_TOKEN_DASHBOARD=<tu_token_aqui>", file=sys.stderr)
        return 2

    print(f"[Dashboard] Arrancando bot dashboard. Root={ROOT}")
    print(f"[Dashboard] Log file: {log_file}")
    run(token=token, root=ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
