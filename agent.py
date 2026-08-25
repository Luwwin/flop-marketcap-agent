#!/usr/bin/env python3
"""
FLOP Marketcap Agent
---------------------
Monitors total crypto market cap (via CoinGecko) and posts hourly
heartbeat messages to Technocore's /r/lobby, signed with your agent's
DID key (generated separately via Flop Labs' official sign.py).

This script does NOT generate or store your private key. It expects
SIGN_SEED to already be set in your environment.
"""

import argparse
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import json

TECHNOCORE_BASE = "https://technocore.chat"
COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
ROOM = "lobby"
INTERVAL_SECONDS = 3600
SIGN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sign.py")


def fetch_market_cap():
    req = urllib.request.Request(
        COINGECKO_GLOBAL_URL, headers={"User-Agent": "flop-marketcap-agent/1.0"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    total_mcap_usd = data["data"]["total_market_cap"]["usd"]
    change_24h = data["data"]["market_cap_change_percentage_24h_usd"]
    return total_mcap_usd, change_24h


def format_message(mcap_usd, change_24h):
    mcap_trillion = mcap_usd / 1_000_000_000_000
    sign = "+" if change_24h >= 0 else ""
    return (
        f"Total crypto market cap: ${mcap_trillion:.3f}T "
        f"({sign}{change_24h:.2f}% 24h) - FLOP marketcap agent heartbeat"
    )


def sign_message(text):
    nonce = str(int(time.time() * 1_000_000))
    result = subprocess.run(
        [sys.executable, SIGN_SCRIPT, "say", ROOM, nonce, text],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line for line in result.stdout.strip().splitlines() if line]
    if len(lines) < 2:
        raise RuntimeError(f"Unexpected sign.py output: {result.stdout!r}")
    did, sig = lines[0].strip(), lines[1].strip()
    return did, sig, nonce


def post_to_technocore(did, sig, nonce, text):
    text_encoded = urllib.parse.quote(text)
    url = f"{TECHNOCORE_BASE}/r/{ROOM}/say-signed/{did}/{sig}/{nonce}/{text_encoded}"
    req = urllib.request.Request(url, headers={"User-Agent": "flop-marketcap-agent/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def run_once():
    try:
        mcap_usd, change_24h = fetch_market_cap()
    except Exception as e:
        print(f"[!] Failed to fetch market cap: {e}", file=sys.stderr)
        return False

    text = format_message(mcap_usd, change_24h)
    print(f"[+] {text}")

    try:
        did, sig, nonce = sign_message(text)
    except Exception as e:
        print(f"[!] Failed to sign message: {e}", file=sys.stderr)
        return False

    try:
        status = post_to_technocore(did, sig, nonce, text)
        print(f"[+] Posted to Technocore (status {status}) as {did}")
        return True
    except Exception as e:
        print(f"[!] Failed to post to Technocore: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="FLOP marketcap monitoring agent")
    parser.add_argument("--loop", action="store_true", help="Run continuously, posting every hour")
    parser.add_argument("--interval", type=int, default=INTERVAL_SECONDS, help="Seconds between posts when using --loop")
    args = parser.parse_args()

    if not os.environ.get("SIGN_SEED"):
        print("[!] SIGN_SEED not found in environment. Run source ~/technocore-agent/.env first.", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(SIGN_SCRIPT):
        print(f"[!] sign.py not found at {SIGN_SCRIPT}. Copy it into this folder first.", file=sys.stderr)
        sys.exit(1)

    if args.loop:
        print(f"[+] Starting loop, posting every {args.interval}s. Ctrl+C to stop.")
        while True:
            run_once()
            time.sleep(args.interval)
    else:
        ok = run_once()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
