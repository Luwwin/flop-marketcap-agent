#!/usr/bin/env python3
"""
FLOP Inference & Faucet Module (skeleton)
-------------------------------------------
Not wired into agent.py yet. This is a placeholder built ahead of Flop
Labs publishing the actual testnet faucet and inference API.

Per Arthur Hayes (Aug 25, 2026): the testnet faucet will be accessed via
Technocore, gated by DID key, and airdrop allocation will be based on
testnet activity - specifically agents spending test FLOP on inference,
not just chat messages in /r/lobby.

TODO once Flop Labs publishes specs:
  1. Set FAUCET_ENDPOINT to the real faucet URL (currently unknown).
  2. Set INFERENCE_ENDPOINT to the real inference-request URL (unknown).
  3. Confirm the request/response format below matches the real API -
     the shapes here are guesses, not confirmed spec.
  4. Import claim_faucet() / spend_on_inference() into agent.py and call
     them from run_cycle(), the same way heartbeats are posted now.

Until then, running this file does nothing destructive - every function
either no-ops or raises a clear NotImplementedError.
"""

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

FAUCET_ENDPOINT = None       # e.g. "https://technocore.chat/faucet/claim"
INFERENCE_ENDPOINT = None    # e.g. "https://technocore.chat/inference/run"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIGN_SCRIPT = os.path.join(SCRIPT_DIR, "sign.py")
INFERENCE_STATE_FILE = os.path.join(SCRIPT_DIR, "inference_state.json")


def load_inference_state():
    if not os.path.exists(INFERENCE_STATE_FILE):
        return {"test_balance": 0, "total_spent": 0, "last_claim_ts": 0}
    try:
        with open(INFERENCE_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"test_balance": 0, "total_spent": 0, "last_claim_ts": 0}


def save_inference_state(state):
    tmp = INFERENCE_STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, INFERENCE_STATE_FILE)


def get_my_did():
    result = subprocess.run(
        [sys.executable, SIGN_SCRIPT, "did"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def sign_payload(payload_text):
    nonce = str(int(time.time() * 1_000_000))
    result = subprocess.run(
        [sys.executable, SIGN_SCRIPT, "say", "faucet", nonce, payload_text],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line for line in result.stdout.strip().splitlines() if line]
    if len(lines) < 2:
        raise RuntimeError(f"Unexpected sign.py output: {result.stdout!r}")
    did, sig = lines[0].strip(), lines[1].strip()
    return did, sig, nonce


def claim_faucet():
    if FAUCET_ENDPOINT is None:
        raise NotImplementedError(
            "FAUCET_ENDPOINT not set yet - Flop Labs hasn't published the "
            "faucet URL. Check @flop_labs / technocore.chat and fill this "
            "in when it's live."
        )
    did, sig, nonce = sign_payload("faucet-claim")
    url = f"{FAUCET_ENDPOINT}/{did}/{sig}/{nonce}"
    req = urllib.request.Request(url, headers={"User-Agent": "flop-marketcap-agent/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    state = load_inference_state()
    claimed = data.get("amount", 0)
    state["test_balance"] += claimed
    state["last_claim_ts"] = int(time.time())
    save_inference_state(state)
    print(f"[+] Claimed {claimed} test FLOP. Balance: {state['test_balance']}")
    return claimed


def spend_on_inference(prompt_text, amount):
    if INFERENCE_ENDPOINT is None:
        raise NotImplementedError(
            "INFERENCE_ENDPOINT not set yet - Flop Labs hasn't published "
            "the inference API. Check @flop_labs / technocore.chat and "
            "fill this in when it's live."
        )
    state = load_inference_state()
    if state["test_balance"] < amount:
        raise ValueError(
            f"Insufficient test balance: have {state['test_balance']}, need {amount}. "
            "Call claim_faucet() first."
        )
    did, sig, nonce = sign_payload(prompt_text)
    payload = urllib.parse.urlencode({"prompt": prompt_text, "amount": amount})
    url = f"{INFERENCE_ENDPOINT}/{did}/{sig}/{nonce}?{payload}"
    req = urllib.request.Request(url, headers={"User-Agent": "flop-marketcap-agent/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode())
    state["test_balance"] -= amount
    state["total_spent"] += amount
    save_inference_state(state)
    print(f"[+] Spent {amount} test FLOP on inference. Remaining: {state['test_balance']}")
    return result


def generate_market_commentary(metrics, spend_amount=1):
    """Option 1: pay for a short natural-language take on current market
    data, instead of just posting raw numbers."""
    prompt = (
        f"In one short sentence, comment on crypto markets right now: "
        f"total market cap ${metrics['total_mcap_usd'] / 1_000_000_000_000:.3f}T, "
        f"24h change {metrics['change_24h']:.2f}%, "
        f"BTC dominance {metrics.get('btc_dominance', 0):.1f}%, "
        f"Fear & Greed {metrics.get('fear_greed', 'unknown')}."
    )
    result = spend_on_inference(prompt, spend_amount)
    return result.get("text", "")


def generate_contextual_reply(original_text, metrics, spend_amount=1):
    """Option 2: pay for a real reply to what another agent actually said."""
    prompt = (
        f"Another agent in a crypto chat room said: \"{original_text.strip()}\". "
        f"Reply in one short sentence with current data: total market cap "
        f"${metrics['total_mcap_usd'] / 1_000_000_000_000:.3f}T "
        f"({metrics['change_24h']:.2f}% 24h), "
        f"BTC dominance {metrics.get('btc_dominance', 0):.1f}%."
    )
    result = spend_on_inference(prompt, spend_amount)
    return result.get("text", "")


def send_spend_heartbeat(spend_amount=1):
    """Option 3: cheapest possible inference call, purely to build spend
    history toward the airdrop allocation."""
    prompt = "Reply with one word: online."
    result = spend_on_inference(prompt, spend_amount)
    return result


def print_status():
    state = load_inference_state()
    print(f"Test balance: {state['test_balance']}")
    print(f"Total spent on inference: {state['total_spent']}")
    print(f"Last faucet claim: {state['last_claim_ts']}")
    if FAUCET_ENDPOINT is None:
        print("Faucet: not yet configured (waiting on Flop Labs to publish endpoint)")
    if INFERENCE_ENDPOINT is None:
        print("Inference: not yet configured (waiting on Flop Labs to publish endpoint)")


if __name__ == "__main__":
    print_status()
