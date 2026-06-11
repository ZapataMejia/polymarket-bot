"""Diagnóstico de credenciales Polymarket LIVE.

Compara signer (EOA), funder (.env), balance CLOB y USDC on-chain.

Usage en VPS:
    python scripts/diagnose_live_clob.py
"""
from __future__ import annotations

import asyncio
import json
import ssl
import sys
import urllib.request
from pathlib import Path

import certifi

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from eth_account import Account
from py_clob_client_v2 import ClobClient
from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

from src.polymarket.live_clob import CLOB_HOST, _parse_usdc_balance, load_live_config

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
POLYGON_RPC = "https://polygon-rpc.com"
DATA_API = "https://data-api.polymarket.com/value"


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


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
        with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx()) as resp:
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
        with urllib.request.urlopen(url, timeout=15, context=_ssl_ctx()) as resp:
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
    client.set_api_creds(client.create_or_derive_api_key())
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
        (2, cfg.funder_address, "Browser wallet Safe (type 2)"),
        (3, cfg.funder_address, "Deposit wallet API (type 3) — Perfil → Dirección"),
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
        print(f"   POLYMARKET_FUNDER_ADDRESS={cfg.funder_address}")
        if best[1] == 3:
            print("   (cuenta Google nueva — Perfil → Dirección, no el nombre de usuario)")
    else:
        print("❌ Ninguna combinación devolvió balance > $0.")
        print()
        print("Tu Perfil muestra 'Dirección ... solo para uso de API' → cuenta NUEVA")
        print("(deposit wallet, signature_type=3). El USDC de la web puede estar en")
        print("otra wallet distinta a la del Perfil.")
        print()
        print("Checklist:")
        print("1. polymarket.com → avatar ARRIBA A LA DERECHA → copiar wallet")
        print("   Compará con Perfil → Dirección. Si son DISTINTAS, probá la del avatar")
        print("   como POLYMARKET_FUNDER_ADDRESS con SIGNATURE_TYPE=1")
        print("2. Settings → Export Private Key (de Polymarket, no otra wallet)")
        print("3. Si UI muestra ~$95 pero TODO da $0 aquí:")
        print("   → Cuenta post-migración CLOB V2; py-clob-client v1 no opera LIVE.")
        print("   → Opciones: retirar USDC y re-depositar con MetaMask (cuenta vieja),")
        print("     o esperar fix de Polymarket SDK para signature_type=3.")
        print("4. Corré: python scripts/diagnose_live_clob.py (este script)")


if __name__ == "__main__":
    asyncio.run(main())
