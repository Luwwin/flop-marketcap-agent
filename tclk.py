#!/usr/bin/env python3
"""
TCLK (Technocore Lock Protocol) module
----------------------------------------
Real, official Flop Labs protocol for agent-to-agent deal coordination via
signed Technocore messages. Spec: https://github.com/flop-labs/tclk

Scope of what's implemented here, deliberately:
  - Frame encoding (tclk1 wire format) - spec gives a worked example, high
    confidence this is correct.
  - Offer id computation - spec gives a worked example for this too.
  - Scanning /r/tclk-offers and parsing any frames found (read-only, safe).
  - Posting a demo offer frame (rails=["demo"], amount="0") so this agent
    shows up as a real, spec-compliant participant.
  - Capability advertisement token on the DID note.

Deliberately NOT implemented yet: accept/lock/reveal and the contract-id
hash. The spec describes that hash in prose rather than a worked example,
and guessing at it risks producing frames that look valid but aren't
actually spec-compliant - exactly what the spec warns against ("do not
hand-roll it against a live deal"). Once the real @flop-labs/tclk
reference implementation is available to test against, this gets filled
in properly.
"""

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import hashlib

TECHNOCORE_BASE = "https://technocore.chat"
OFFERS_ROOM = "tclk-offers"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIGN_SCRIPT = os.path.join(SCRIPT_DIR, "sign.py")
TCLK_STATE_FILE = os.path.join(SCRIPT_DIR, "tclk_state.json")


def get_my_did():
    result = subprocess.run(
        [sys.executable, SIGN_SCRIPT, "did"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def sign_message(text, room=OFFERS_ROOM):
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


def post_message(text, room=OFFERS_ROOM):
    did, sig, nonce = sign_message(text, room)
    text_encoded = urllib.parse.quote(text)
    url = f"{TECHNOCORE_BASE}/r/{room}/say-signed/{did}/{sig}/{nonce}/{text_encoded}"
    req = urllib.request.Request(url, headers={"User-Agent": "flop-marketcap-agent/tclk-0.1"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, did


def fetch_room_messages(room=OFFERS_ROOM, limit=100):
    url = f"{TECHNOCORE_BASE}/r/{room}?format=json&n={int(time.time())}"
    req = urllib.request.Request(url, headers={"User-Agent": "flop-marketcap-agent/tclk-0.1"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    messages = data if isinstance(data, list) else data.get("messages", [])
    return messages[-limit:]


def load_tclk_state():
    if not os.path.exists(TCLK_STATE_FILE):
        return {"seen_nonces": [], "notified_offer_ids": []}
    try:
        with open(TCLK_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"seen_nonces": [], "notified_offer_ids": []}


def save_tclk_state(state):
    state["seen_nonces"] = state.get("seen_nonces", [])[-500:]
    state["notified_offer_ids"] = state.get("notified_offer_ids", [])[-200:]
    tmp = TCLK_STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, TCLK_STATE_FILE)


def notify(title, message):
    try:
        subprocess.run(
            ["termux-notification", "--title", title, "--content", message],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        pass


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def encode_frame(frame_dict):
    return "tclk1 " + canonical_json(frame_dict)


def decode_frame(text):
    if not text.startswith("tclk1 "):
        return None
    try:
        return json.loads(text[len("tclk1 "):])
    except Exception:
        return None


def compute_offer_id(offer_without_id):
    return "0x" + sha256_hex("FLOP::tclk::v1|offer|" + canonical_json(offer_without_id))


def make_demo_offer(my_did, claim_window_ms=3_600_000, refund_window_ms=7_200_000):
    now = int(time.time() * 1000)
    offer = {
        "amount": "0",
        "asset": "FLOP",
        "claimByMs": now + claim_window_ms,
        "expiresMs": now + 600_000,
        "from": my_did,
        "lock": "hash",
        "nonce": secrets.token_hex(8),
        "rails": ["demo"],
        "refundAfterMs": now + refund_window_ms,
        "role": "payer",
        "type": "offer",
    }
    offer["id"] = compute_offer_id(offer)
    return offer


def post_demo_offer(my_did):
    offer = make_demo_offer(my_did)
    frame_text = encode_frame(offer)
    status, did = post_message(frame_text, OFFERS_ROOM)
    print(f"[+] Posted demo tclk/1 offer (status {status}): {offer['id']}")
    notify("FLOP Agent: TCLK offer posted", f"Offer {offer['id'][:18]}...")
    return offer


def scan_offers(state):
    try:
        messages = fetch_room_messages(OFFERS_ROOM)
    except Exception as e:
        print(f"[!] Failed to fetch {OFFERS_ROOM}: {e}", file=sys.stderr)
        return state

    seen = set(state.get("seen_nonces", []))
    notified = set(state.get("notified_offer_ids", []))

    for msg in messages:
        nonce = str(msg.get("nonce", ""))
        text = msg.get("text", "")
        sender = msg.get("from", "")

        if not nonce or nonce in seen:
            continue
        seen.add(nonce)

        frame = decode_frame(text)
        if not frame:
            continue

        ftype = frame.get("type")
        if ftype == "offer":
            offer_id = frame.get("id", "")
            if offer_id and offer_id not in notified:
                print(
                    f"[+] Real tclk/1 offer seen from {sender[:20]}...: "
                    f"{frame.get('amount')} {frame.get('asset')} "
                    f"via {frame.get('rails')}"
                )
                notify(
                    "FLOP Agent: TCLK offer spotted",
                    f"{frame.get('amount')} {frame.get('asset')} from {sender[:20]}...",
                )
                notified.add(offer_id)
        elif ftype in ("accept", "lock", "reveal", "refund", "cancel", "receipt"):
            print(f"[+] tclk/1 {ftype} frame seen from {sender[:20]}...")

    state["seen_nonces"] = list(seen)
    state["notified_offer_ids"] = list(notified)
    save_tclk_state(state)
    return state


def advertise_capability():
    try:
        did = get_my_did()
        fp = hashlib.sha256(did.encode()).hexdigest()[:16]
        note_value = f"{did} tclk1:demo"
        note_encoded = urllib.parse.quote(note_value)
        url = f"{TECHNOCORE_BASE}/kv/did/{fp}/set/{note_encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "flop-marketcap-agent/tclk-0.1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"[+] Capability advertised (status {resp.status})")
            return True
    except Exception as e:
        print(f"[!] Capability advertisement failed (non-critical): {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    my_did = get_my_did()
    print(f"[+] Agent identity: {my_did}")
    advertise_capability()
    post_demo_offer(my_did)
    state = load_tclk_state()
    scan_offers(state)
