"""Ordres « maker » lus sur la blockchain Polygon.

Le flux public /v2/trades n'enregistre que le taker de chaque échange. Un compte qui achète avec des ordres
limités (maker) y apparaît sous le nom de sa contrepartie : c'était le cas du compte ljhggk (NVIDIA, 23/11).
On relit donc, sur Polygon, les transactions du flux où un maker a pu acheter une issue improbable, et on
ajoute ses achats au flux comme des trades ordinaires.

Événement OrderFilled de l'exchange Polymarket v2 :
  topics = [signature, orderHash, maker, taker]
  data   = [side (0 achat, 1 vente), tokenId, makerAmountFilled, takerAmountFilled, fee, …]  (montants en 1e-6)
Le log dont le taker est l'exchange lui-même décrit l'ordre du taker : on l'ignore (déjà dans le flux).
"""
from __future__ import annotations

import json
import urllib.request

from . import config

ORDER_FILLED = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
RPCS = ["https://polygon-bor-rpc.publicnode.com", "https://1rpc.io/matic"]
BATCH = 100
COMPLEMENT = {"Yes": "No", "No": "Yes"}


def maker_side_cash(t: dict) -> float:
    """Montant qu'un maker a pu engager sur une issue improbable dans cet échange (0 si impossible).
    - le taker achète X à q ≥ 65 % : un maker a pu acheter l'issue opposée à 1 - q (création de parts) ;
    - le taker vend X à q ≤ 35 % : un maker a acheté X à q."""
    q, size = t["price"], t["size"]
    if t["side"] == "BUY" and 1 - config.MAX_PRICE <= q <= 1 - config.MIN_PRICE:
        return size * (1 - q)
    if t["side"] == "SELL" and config.MIN_PRICE <= q <= config.MAX_PRICE:
        return size * q
    return 0.0


def decode_maker_buys(receipt: dict, feed_trade: dict) -> list[dict]:
    """Achats des makers dans une transaction, au format des trades du flux."""
    out = []
    for log in (receipt or {}).get("logs", []):
        topics = log.get("topics") or []
        if len(topics) != 4 or topics[0].lower() != ORDER_FILLED:
            continue
        maker, taker = "0x" + topics[2][-40:], "0x" + topics[3][-40:]
        if taker.lower() == str(log.get("address", "")).lower():
            continue  # ordre du taker
        d = log.get("data", "0x")[2:]
        words = [int(d[i:i + 64], 16) for i in range(0, len(d), 64)]
        if len(words) < 4 or words[0] != 0:
            continue  # vente du maker : sans intérêt ici
        token, usdc, shares = str(words[1]), words[2] / 1e6, words[3] / 1e6
        if shares <= 0:
            continue
        same = token == feed_trade.get("tokenId")
        outcome = feed_trade.get("outcome", "") if same else COMPLEMENT.get(feed_trade.get("outcome", ""), "")
        idx = int(feed_trade.get("outcomeIndex") or 0)
        out.append({
            "proxyWallet": maker.lower(), "side": "BUY", "size": shares, "price": usdc / shares,
            "timestamp": feed_trade["timestamp"], "conditionId": feed_trade["conditionId"],
            "outcome": outcome, "outcomeIndex": idx if same else 1 - idx, "title": feed_trade.get("title", ""),
            "slug": feed_trade.get("slug", ""), "eventSlug": feed_trade.get("eventSlug", ""), "pseudonym": "",
            "transactionHash": feed_trade.get("transactionHash", ""), "tokenId": token, "maker": True,
        })
    return out


def receipts(tx_hashes: list[str]) -> dict[str, dict]:
    """Reçus de transactions, par lots, en basculant sur un autre accès public si le premier échoue."""
    out: dict[str, dict] = {}
    for i in range(0, len(tx_hashes), BATCH):
        chunk = tx_hashes[i:i + BATCH]
        body = json.dumps([{"jsonrpc": "2.0", "id": j, "method": "eth_getTransactionReceipt", "params": [h]}
                           for j, h in enumerate(chunk)]).encode()
        last_error = None
        for rpc in RPCS:
            try:
                req = urllib.request.Request(rpc, data=body, headers={"Content-Type": "application/json",
                                                                      "User-Agent": "polymarket-radar/0.1"})
                res = json.load(urllib.request.urlopen(req, timeout=60))
                for r in res if isinstance(res, list) else []:
                    if r.get("result"):
                        out[chunk[int(r["id"])]] = r["result"]
                last_error = None
                break
            except Exception as e:
                last_error = e
        if last_error:
            raise last_error
    return out


def maker_trades(trades: list[dict], keep=lambda t: True) -> tuple[list[dict], dict]:
    """Achats des makers sur des issues improbables, retrouvés dans les transactions du flux.
    keep : filtre bon marché sur le trade du flux (ex. titre d'actualité) avant toute lecture on-chain."""
    by_tx: dict[str, dict] = {}
    for t in trades:
        h = t.get("transactionHash")
        if h and h not in by_tx and maker_side_cash(t) >= config.MAKER_MIN_CASH and keep(t):
            by_tx[h] = t
    hashes = sorted(by_tx, key=lambda h: -maker_side_cash(by_tx[h]))[: config.MAKER_MAX_TX]
    found = receipts(hashes) if hashes else {}
    out = []
    for h, rec in found.items():
        out += decode_maker_buys(rec, by_tx[h])
    return out, {"tx": len(hashes), "read": len(found), "fills": len(out)}
