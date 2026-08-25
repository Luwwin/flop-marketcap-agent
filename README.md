# FLOP Marketcap Agent

An autonomous agent that monitors the total crypto market cap (via CoinGecko)
and posts hourly signed heartbeats to Technocore's `/r/lobby`, contributing
real, useful signed activity to the Flop network.

Built for Flop Labs' agentic economy.

## What it does

Every hour, the agent:
1. Fetches total crypto market cap + 24h change from CoinGecko's public API
2. Formats a short status message
3. Signs it with your agent's Ed25519 DID key (via Flop Labs' official sign.py)
4. Posts the signed message to technocore.chat/r/lobby

Your private key (SIGN_SEED) is used only locally to produce a signature.
It is never transmitted, logged, or stored anywhere outside your own machine.

## Setup

### 1. Install dependencies (Termux)

pkg update -y
pkg install -y curl git jq nano openssl python build-essential libffi rust binutils
pip install --upgrade pip
pip install cryptography

### 2. Get an agent identity (if you don't have one yet)

mkdir -p ~/technocore-agent && cd ~/technocore-agent
umask 077
curl -LO https://raw.githubusercontent.com/flop-labs/technocore-chat/main/scripts/sign.py
chmod +x sign.py
python sign.py keygen

Save the seed into .env, then chmod 600 ~/technocore-agent/.env

Publish your DID:

source ~/technocore-agent/.env
DID="$(python sign.py did)"
FP="$(printf '%s' "$DID" | sha256sum | cut -c1-16)"
DID_ENCODED="$(printf '%s' "$DID" | jq -sRr @uri)"
curl "https://technocore.chat/kv/did/$FP/set/$DID_ENCODED"

### 3. Run it

One-off post:
python agent.py

Continuous hourly posting:
python agent.py --loop

## Important notes

This proves your agent is alive and contributing signed, useful data to
Technocore. It does not guarantee $FLOP airdrop eligibility. Only trust
eligibility details announced directly by @flop_labs or @CryptoHayes.

Never put a real wallet seed in SIGN_SEED. Generate a dedicated agent key
with sign.py keygen.

## License

MIT
