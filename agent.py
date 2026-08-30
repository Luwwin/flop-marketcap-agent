#!/usr/bin/env python3
"""
FLOP Marketcap Agent v3
------------------------
Monitors crypto market data and contributes to Technocore across multiple
rooms:

1. Hourly heartbeat with total mcap, BTC dominance, ETH/BTC ratio, and
   the Fear & Greed index - posted to /r/lobby.
2. Threshold alerts: posts immediately if total market cap moves +/-2%
   since the last alert.
3. Reply mode: scans lobby AND a rotating selection of other active rooms
   for messages asking about market cap / dominance, replies with data.
4. Faucet watch: pings your phone if "faucet" is mentioned in any room
   scanned - does NOT auto-claim, there's no documented endpoint yet.
5. Android notifications via Termux:API for every heartbeat/alert/reply.

This script does NOT generate or store your private key. It expects
SIGN_SEED to already be set in your environment.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

TECHNOCORE_BASE = "https://technocore.chat"
COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"

DEFAULT_ROOM = "lobby"
CHECK_INTERVAL_SECONDS = 300
HEARTBEAT_INTERVAL_SECONDS = 3600
ALERT_THRESHOLD_PCT = 2.0
MAX_REPLIES_PER_CYCLE = 1
ROOM_LIST_REFRESH_SECONDS = 1800
MAX_ROOMS_TRACKED = 20

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIGN_SCRIPT = os.path.join(SCRIPT_DIR, "sign.py")
STATE_FILE = os.path.join(SCRIPT_DIR, "state.json")

MENTION_PATTERN = re.compile(
    r"\b(market ?cap|mcap|dominance|fear\s*(and|&)\s*greed)\b", re.IGNORECASE
)
FAUCET_PATTERN = re.compile(r"\bfaucet\b.{0,60}(claim|live|technocore\.chat|http)|((claim|live|http).{0,60}\bfaucet\b)", re.IGNORECASE)


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_alert_mcap": None, "last_heartbeat_ts": 0, "replied_nonces": []}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"last_alert_mcap": None, "last_heartbeat_ts": 0, "replied_nonces": []}


def save_state(state):
    state["replied_nonces"] = state.get("replied_nonces", [])[-200:]
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def notify(title, message):
    try:
        subprocess.run(
            ["termux-notification", "--title", title, "--content", message],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        pass


def fetch_global():
    req = urllib.request.Request(
        COINGECKO_GLOBAL_URL, headers={"User-Agent": "flop-marketcap-agent/2.0"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())["data"]
    return {
        "total_mcap_usd": data["total_market_cap"]["usd"],
        "change_24h": data["market_cap_change_percentage_24h_usd"],
        "btc_dominance": data["market_cap_percentage"].get("btc"),
    }


def fetch_eth_btc_ratio():
    params = urllib.parse.urlencode({"ids": "ethereum", "vs_currencies": "btc"})
    req = urllib.request.Request(
        f"{COINGECKO_PRICE_URL}?{params}",
        headers={"User-Agent": "flop-marketcap-agent/2.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    return data.get("ethereum", {}).get("btc")


def fetch_fear_greed():
    req = urllib.request.Request(
        FEAR_GREED_URL, headers={"User-Agent": "flop-marketcap-agent/2.0"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    entry = data.get("data", [{}])[0]
    return entry.get("value"), entry.get("value_classification")


def fetch_all_metrics():
    g = fetch_global()
    metrics = dict(g)

    try:
        metrics["eth_btc"] = fetch_eth_btc_ratio()
    except Exception:
        metrics["eth_btc"] = None

    try:
        fg_value, fg_label = fetch_fear_greed()
        metrics["fear_greed"] = fg_value
        metrics["fear_greed_label"] = fg_label
    except Exception:
        metrics["fear_greed"] = None
        metrics["fear_greed_label"] = None

    return metrics


def format_heartbeat(metrics):
    mcap_t = metrics["total_mcap_usd"] / 1_000_000_000_000
    change = metrics["change_24h"]
    sign = "+" if change >= 0 else ""
    parts = [f"Total mcap: ${mcap_t:.3f}T ({sign}{change:.2f}% 24h)"]

    if metrics.get("btc_dominance") is not None:
        parts.append(f"BTC dom: {metrics['btc_dominance']:.1f}%")
    if metrics.get("eth_btc") is not None:
        parts.append(f"ETH/BTC: {metrics['eth_btc']:.5f}")
    if metrics.get("fear_greed") is not None:
        parts.append(f"F&G: {metrics['fear_greed']} ({metrics['fear_greed_label']})")

    return " | ".join(parts) + " - FLOP marketcap agent heartbeat"


def format_alert(metrics, pct_move):
    mcap_t = metrics["total_mcap_usd"] / 1_000_000_000_000
    direction = "up" if pct_move >= 0 else "down"
    return (
        f"ALERT: Total crypto market cap moved {direction} "
        f"{abs(pct_move):.2f}% - now ${mcap_t:.3f}T. FLOP marketcap agent."
    )


def format_reply(original_text, metrics):
    mcap_t = metrics["total_mcap_usd"] / 1_000_000_000_000
    change = metrics["change_24h"]
    sign = "+" if change >= 0 else ""
    snippet = original_text.strip()
    if len(snippet) > 40:
        snippet = snippet[:40] + "..."
    return (
        f're: "{snippet}" -> Total mcap is ${mcap_t:.3f}T ({sign}{change:.2f}% 24h)'
        f", BTC dom {metrics.get('btc_dominance', 0):.1f}%. FLOP marketcap agent."
    )


def sign_message(text, room=DEFAULT_ROOM):
    nonce = str(int(time.time() * 1_000_000))
    result = subprocess.run(
        [sys.executable, SIGN_SCRIPT, "say", room, nonce, text],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line for line in result.stdout.strip().splitlines() if line]
    if len(lines) < 2:
        raise RuntimeError(f"Unexpected sign.py output: {result.stdout!r}")
    did, sig = lines[0].strip(), lines[1].strip()
    return did, sig, nonce


def post_message(text, room=DEFAULT_ROOM):
    did, sig, nonce = sign_message(text, room)
    text_encoded = urllib.parse.quote(text)
    url = f"{TECHNOCORE_BASE}/r/{room}/say-signed/{did}/{sig}/{nonce}/{text_encoded}"
    req = urllib.request.Request(url, headers={"User-Agent": "flop-marketcap-agent/2.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, did


def get_my_did():
    result = subprocess.run(
        [sys.executable, SIGN_SCRIPT, "did"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def fetch_recent_room_messages(room=DEFAULT_ROOM, limit=50):
    url = f"{TECHNOCORE_BASE}/r/{room}?format=json&n={int(time.time())}"
    req = urllib.request.Request(url, headers={"User-Agent": "flop-marketcap-agent/2.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    messages = data if isinstance(data, list) else data.get("messages", [])
    return messages[-limit:]


def fetch_room_list():
    url = f"{TECHNOCORE_BASE}/rooms?format=json&n={int(time.time())}"
    req = urllib.request.Request(url, headers={"User-Agent": "flop-marketcap-agent/2.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    raw_list = data if isinstance(data, list) else data.get("rooms", [])
    names = []
    for item in raw_list:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("room") or item.get("id")
            if name:
                names.append(name)
    return names


def do_heartbeat(metrics, state):
    text = format_heartbeat(metrics)
    print(f"[+] {text}")
    status, did = post_message(text, DEFAULT_ROOM)
    print(f"[+] Heartbeat posted (status {status}) as {did}")
    notify("FLOP Agent: Heartbeat posted", text)
    state["last_heartbeat_ts"] = int(time.time())


def check_threshold_alert(metrics, state):
    last = state.get("last_alert_mcap")
    current = metrics["total_mcap_usd"]

    if last is None:
        state["last_alert_mcap"] = current
        return

    pct_move = (current - last) / last * 100
    if abs(pct_move) >= ALERT_THRESHOLD_PCT:
        text = format_alert(metrics, pct_move)
        print(f"[!] Threshold crossed: {text}")
        status, did = post_message(text, DEFAULT_ROOM)
        print(f"[+] Alert posted (status {status}) as {did}")
        notify("FLOP Agent: Threshold alert posted", text)
        state["last_alert_mcap"] = current


def scan_room_and_reply(room, metrics, state, my_did):
    replied = set(state.get("replied_nonces", []))
    try:
        messages = fetch_recent_room_messages(room)
    except Exception as e:
        print(f"[!] Failed to fetch messages from {room}: {e}", file=sys.stderr)
        return None

    replies_sent = 0
    for msg in messages:
        if replies_sent >= MAX_REPLIES_PER_CYCLE:
            break

        nonce = str(msg.get("nonce", ""))
        text = msg.get("text", "")
        sender = msg.get("from", "")

        if not nonce or nonce in replied:
            continue
        if sender == my_did:
            continue
        if not MENTION_PATTERN.search(text):
            continue

        reply_text = format_reply(text, metrics)
        try:
            status, did = post_message(reply_text, room)
            print(f"[+] Replied in {room} to {sender[:20]}... (status {status})")
            notify(f"FLOP Agent: Replied in {room}", reply_text)
            replied.add(nonce)
            replies_sent += 1
        except Exception as e:
            print(f"[!] Failed to send reply in {room}: {e}", file=sys.stderr)

    state["replied_nonces"] = list(replied)
    return messages


def get_next_room_to_scan(state):
    now = int(time.time())
    last_refresh = state.get("room_list_refreshed_ts", 0)

    if now - last_refresh >= ROOM_LIST_REFRESH_SECONDS or not state.get("room_list"):
        try:
            rooms = fetch_room_list()
            rooms = [r for r in rooms if r and r != DEFAULT_ROOM and r != "events"]
            state["room_list"] = rooms[:MAX_ROOMS_TRACKED]
            state["room_list_refreshed_ts"] = now
            state["room_rotation_index"] = 0
            print(f"[+] Refreshed room list: {len(state['room_list'])} rooms tracked")
        except Exception as e:
            print(f"[!] Failed to refresh room list: {e}", file=sys.stderr)

    rooms = state.get("room_list", [])
    if not rooms:
        return None

    idx = state.get("room_rotation_index", 0) % len(rooms)
    state["room_rotation_index"] = idx + 1
    return rooms[idx]


def faucet_watch(messages, state):
    if not messages:
        return
    last_notified = state.get("faucet_notified_ts", 0)
    if int(time.time()) - last_notified < 3600:
        return

    for msg in messages:
        text = msg.get("text", "")
        if FAUCET_PATTERN.search(text):
            notify("FLOP Agent: faucet mentioned", f"Saw: {text[:100]}")
            state["faucet_notified_ts"] = int(time.time())
            print(f"[!] Faucet keyword spotted: {text[:100]}")
            return


def run_cycle(state, my_did):
    try:
        metrics = fetch_all_metrics()
    except Exception as e:
        print(f"[!] Failed to fetch market data: {e}", file=sys.stderr)
        return state

    now = int(time.time())
    if now - state.get("last_heartbeat_ts", 0) >= HEARTBEAT_INTERVAL_SECONDS:
        do_heartbeat(metrics, state)
    else:
        check_threshold_alert(metrics, state)

    lobby_messages = scan_room_and_reply(DEFAULT_ROOM, metrics, state, my_did)
    faucet_watch(lobby_messages, state)

    other_room = get_next_room_to_scan(state)
    if other_room:
        other_messages = scan_room_and_reply(other_room, metrics, state, my_did)
        faucet_watch(other_messages, state)

    save_state(state)
    return state


def main():
    parser = argparse.ArgumentParser(description="FLOP marketcap monitoring agent v3")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument(
        "--interval", type=int, default=CHECK_INTERVAL_SECONDS,
        help="Seconds between checks when using --loop (default 300)",
    )
    args = parser.parse_args()

    if not os.environ.get("SIGN_SEED"):
        print("[!] SIGN_SEED not found. Run `source .env` first.", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(SIGN_SCRIPT):
        print(f"[!] sign.py not found at {SIGN_SCRIPT}.", file=sys.stderr)
        sys.exit(1)

    my_did = get_my_did()
    print(f"[+] Agent identity: {my_did}")
    state = load_state()

    if args.loop:
        print(
            f"[+] Starting loop: checking every {args.interval}s, "
            f"heartbeat every {HEARTBEAT_INTERVAL_SECONDS}s, "
            f"alert threshold {ALERT_THRESHOLD_PCT}%. Ctrl+C to stop."
        )
        while True:
            state = run_cycle(state, my_did)
            time.sleep(args.interval)
    else:
        state = run_cycle(state, my_did)


if __name__ == "__main__":
    main()
