"""Diagnóstico de credenciales Polymarket LIVE.

Compara signer (EOA), funder (.env), balance CLOB y USDC on-chain.

Usage en VPS:
    python scripts/diagnose_live_clob.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

from src.polymarket.live_clob import CLOB_HOST, _parse_usdc_balance, load_live_config

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
POLYGON_RPC = "https://polygon-rpc.com"
DATA_API = "https://data-api.polymarket.com/value"


def _rpc_usdc_balance(address: str, token: str, label: str) -> float:
    addr = address.lower().replace("0x", "").zfill(64)
    data = "0x70a08231" + addr
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": token, "data": data}, "latest"],
        "id": 1,
    }).encode()
    req = urllib.request.Request(
        POLYGON_RPC, data=payload, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
        if "error" in result:
            print(f"  {label}: RPC error {result['error']}")
            return 0.0
        raw = int(result.get("result", "0x0"), 16)
        return raw / 1_000_000.0
    except Exception as exc:
        print(f"  {label}: {exc}")
        return 0.0


def _data_api_value(address: str) -> float:
    url = f"{DATA_API}?user={address}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        if isinstance(data, list) and data:
            return float(data[0].get("value", 0))
    except Exception as exc:
        print(f"  data-api: {exc}")
    return 0.0


def _clob_balance(key: str, funder: str | None, sig_type: int) -> tuple[float, dict]:
    kwargs: dict = {"key": key, "chain_id": 137}
    if funder:
        kwargs["funder"] = funder
        kwargs["signature_type"] = sig_type
    client = ClobClient(CLOB_HOST, **kwargs)
    client.set_api_creds(client.create_or_derive_api_creds())
    params = BalanceAllowanceParams(
        asset_type=AssetType.COLLATERAL,
        signature_type=sig_type if funder else 0,
    )
    raw = client.get_balance_allowance(params)
    return _parse_usdc_balance(raw), raw if isinstance(raw, dict) else {"raw": str(raw)}


async def main() -> None:
    cfg = load_live_config()
    signer = Account.from_key(cfg.private_key).address

    print("=" * 60)
    print("DIAGNÓSTICO POLYMARKET LIVE")
    print("=" * 60)
    print(f"Signer EOA (de private key): {signer}")
    print(f"Funder (.env):               {cfg.funder_address}")
    print(f"Signature type (.env):       {cfg.signature_type}")
    print()

    if signer.lower() == cfg.funder_address.lower():
        print("⚠️  Signer y funder son IGUALES.")
        print("   Con cuenta Google/Magic deberían ser DIFERENTES:")
        print("   - private key = EOA que firmás")
        print("   - funder = dirección del perfil en polymarket.com")
        print()

    print("--- USDC on-chain (Polygon) ---")
    for who, addr in [("Signer", signer), ("Funder", cfg.funder_address)]:
        usdc_e = _rpc_usdc_balance(addr, USDC_E, f"{who} USDC.e")
        usdc_n = _rpc_usdc_balance(addr, USDC_NATIVE, f"{who} USDC native")
        portfolio = _data_api_value(addr)
        print(f"{who} {addr[:10]}...{addr[-6:]}")
        print(f"  USDC.e on-chain:    ${usdc_e:.2f}")
        print(f"  USDC native:        ${usdc_n:.2f}")
        print(f"  Portfolio data-api: ${portfolio:.2f}")
    print()

    print("--- Balance CLOB (colateral para trading) ---")
    tests = [
        (0, None, "EOA directo (type 0, sin funder)"),
        (1, cfg.funder_address, "Magic/Google proxy (type 1)"),
        (2, cfg.funder_address, "Browser wallet proxy (type 2)"),
    ]
    best = (0.0, -1, "")
    for sig, funder, label in tests:
        try:
            bal, raw = _clob_balance(cfg.private_key, funder, sig)
            mark = "✅" if bal >= 1 else "  "
            print(f"{mark} {label}: ${bal:.2f}")
            if bal < 1 and raw:
                print(f"     raw={str(raw)[:120]}")
            if bal > best[0]:
                best = (bal, sig, label)
        except Exception as exc:
            print(f"   {label}: ERROR — {exc}")
    print()

    if best[0] >= 1:
        print(f"✅ Usá POLYMARKET_SIGNATURE_TYPE={best[1]}")
        if best[1] in (1, 2):
            print(f"   POLYMARKET_FUNDER_ADDRESS={cfg.funder_address}")
    else:
        print("❌ Ninguna combinación devolvió balance > $0.")
        print()
        print("Checklist:")
        print("1. En polymarket.com → avatar → copiá la wallet del perfil")
        print("   Esa dirección va en POLYMARKET_FUNDER_ADDRESS")
        print("2. Settings → Export Private Key → POLYMARKET_PRIVATE_KEY")
        print("   (debe ser la key exportada DE Polymarket, no otra wallet)")
        print("3. Cuenta Google/email (Magic) → SIGNATURE_TYPE=1")
        print("   MetaMask/Coinbase conectado → SIGNATURE_TYPE=2")
        print("4. Verificá en polymarket.com que el balance muestre ~$95 USDC")
        print("5. Si todo coincide y sigue $0, re-derivá credenciales:")
        print("   borrá state.json y reiniciá después de corregir .env")


if __name__ == "__main__":
    asyncio.run(main())
