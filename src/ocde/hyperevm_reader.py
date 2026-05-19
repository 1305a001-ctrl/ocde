"""RedStone on-chain HYPE/USD reader via raw JSON-RPC.

HyperLend (the largest HyperEVM lending market) prices HYPE off RedStone
push feeds, not Chainlink Data Streams. To measure the gap between our
edge feed (Streams) and what HyperLend actually reads, we call the
RedStone price source contract directly through `eth_call`.

Source-of-truth pick (verified via HyperEVM forked-block reads
2026-05-20, documented in memory/arch_hyperevm_lending_audit.md):
  - `0x40EA33eA76Fbe35e9FB422eDd175b8c8D84A63Cc` (WHYPE source) →
    description() = "RedStone Price Feed for HYPE". Single-source
    HYPE/USD. This is what we want.
  - `0x6dcFA746f7b11918eF3522c92e6429CA589C3875` (kHYPE source) →
    description() = "kHYPE-prim:redstone/fundam-sec:chainlink/...".
    Composite kHYPE/USD that mixes in the kHYPE-vs-HYPE staking
    ratio. NOT what we want for HYPE-price divergence — would
    contaminate the signal with staking-ratio drift.

If HyperLend rotates the WHYPE oracle source (which it can do via
governance — there's a `setSourceOfAsset` call on the Aave-V3 Oracle),
the reader will silently keep reading the old (possibly stale)
contract. Re-verify the source for `WHYPE` against the live
Oracle.getSourceOfAsset call before each major release.

Programmatic trap-monitor: `verify_hyperlend_whype_source()` does this
check at runtime — call it every N cycles from the divergence loop. On
drift it logs `hyperevm_reader.oracle_source_drift` at ERROR and
returns False; the caller bails on the rest of the divergence emit for
that cycle. We intentionally do NOT auto-rotate to the new source —
loud failure forces the operator to verify which path the rotation
landed on before re-deploying with updated settings.

Implementation note: this module deliberately uses raw httpx JSON-RPC
rather than web3.py — OCDE has a hard no-new-dependencies rule and httpx
is already pinned for the Pyth REST path. The function is best-effort:
any failure (HTTP error, malformed result, contract revert) returns
None and logs a warning. The caller (hype_divergence_loop) must tolerate
None and degrade gracefully.

Timestamp: we record `time.time() * 1000` at read time. RedStone exposes
`latestRoundData()` for the true on-chain update time, but for divergence
the read-time stamp is sufficient — the gap is what we care about, not
absolute freshness. If we later need on-chain age, swap selector to
`0xfeaf968c` (`latestRoundData()`) and decode the 5-tuple.
"""
from __future__ import annotations

import logging
import re
import time

import httpx

from .divergence import OraclePrice
from .settings import settings

log = logging.getLogger(__name__)

# Selector for `latestAnswer() returns (int256)` — RedStone aggregator interface.
_LATEST_ANSWER_SELECTOR = "0x50d25bcd"
# Selector for Aave-V3 `IAaveOracle.getSourceOfAsset(address) returns (address)`.
# Hardcoded (not derived from keccak at import-time) so we don't pull a new
# dep — OCDE has a strict no-new-deps rule. Verified by computing
# `keccak256("getSourceOfAsset(address)")[:4]` once externally and pasting
# the result here. (Same hex any Ethereum tooling will produce.)
_GET_SOURCE_OF_ASSET_SELECTOR = "0x92bf2be0"
_HTTP_TIMEOUT_S = 5.0
_ETH_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Strict 0x[40 hex] check — used to validate decoded addresses without
# pulling web3.py for Web3.to_checksum_address.
_ADDR_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _encode_address_for_call(addr: str) -> str | None:
    """Encode an address as a 32-byte left-padded hex string (no `0x` prefix).

    Returns None for malformed input. ABI encoding for a single `address`
    argument is just the 20 raw bytes right-aligned in a 32-byte slot — no
    need for eth_abi. Whitespace-tolerant.
    """
    if not isinstance(addr, str):
        return None
    addr = addr.strip()
    if not _ADDR_PATTERN.match(addr):
        return None
    return "0" * 24 + addr[2:].lower()


def _decode_address_result(hex_result: str) -> str | None:
    """Decode a single-address eth_call return blob → lowercase 0x-prefixed address.

    Returns None for malformed input. We don't EIP-55 checksum the result —
    the comparison in `verify_hyperlend_whype_source` is case-insensitive
    so an uppercase-vs-lowercase mismatch never produces a false positive.
    """
    if not isinstance(hex_result, str) or not hex_result.startswith("0x"):
        return None
    hex_body = hex_result[2:]
    if len(hex_body) != 64:
        return None
    try:
        int(hex_body, 16)  # validate it's hex
    except ValueError:
        return None
    # Last 40 chars = the 20-byte address.
    return "0x" + hex_body[-40:].lower()


def _decode_int256(hex_result: str) -> int | None:
    """Decode a 32-byte hex int256 (two's complement) from an eth_call result.

    Returns None for malformed input. Empty/short results (a reverted call
    typically returns '0x') are treated as malformed.
    """
    if not isinstance(hex_result, str) or not hex_result.startswith("0x"):
        return None
    hex_body = hex_result[2:]
    if len(hex_body) != 64:
        return None
    try:
        raw = int(hex_body, 16)
    except ValueError:
        return None
    # Two's complement: if the top bit is set, this is negative.
    if raw >= 1 << 255:
        raw -= 1 << 256
    return raw


async def verify_hyperlend_whype_source(  # noqa: PLR0911, PLR0912
    client: httpx.AsyncClient | None = None,
) -> bool | None:
    """Check HyperLend's WHYPE oracle source against the expected aggregator.

    Trap-surface monitor (called every N cycles from hype_divergence_loop):
    HyperLend uses the Aave-V3 oracle pattern where `getSourceOfAsset(token)`
    returns the per-asset aggregator. A governance call to `setSourceOfAsset`
    can rotate this without warning. Before this check existed, OCDE would
    silently keep reading whatever `settings.redstone_hype_source` pointed
    at — potentially the OLD aggregator after a rotation, or potentially
    the kHYPE composite (contaminating the HYPE divergence signal with
    staking-ratio drift).

    Returns:
        True  — the live source equals settings.expected_hyperlend_whype_source.
                (Note: the equality is case-insensitive.)
        False — DRIFT detected. Logs `hyperevm_reader.oracle_source_drift` at
                ERROR. The caller should LOG-and-bail rather than try to
                auto-rotate: the trade-off is "loud failure with manual
                redeploy" vs "silently read the wrong aggregator". We chose
                loud failure (see memory/arch_hyperevm_lending_audit.md).
        None  — the watchdog itself could not reach the oracle. The caller
                should treat this as "no signal" (don't assume no-drift).
    """
    encoded_token = _encode_address_for_call(settings.hyperlend_whype_token)
    if encoded_token is None:
        # Misconfigured token address — bail loudly. Treat as ERROR, not
        # silent skip; this would surface only after a bad config edit.
        log.error(
            "hyperevm_reader.oracle_source_bad_token_config token=%r",
            settings.hyperlend_whype_token,
        )
        return None
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [
            {
                "to": settings.hyperlend_oracle_address,
                "data": _GET_SOURCE_OF_ASSET_SELECTOR + encoded_token,
            },
            "latest",
        ],
    }
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S)
    try:
        try:
            resp = await client.post(settings.hyperevm_rpc_url, json=payload)
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("hyperevm_reader.oracle_source_http_error err=%s", e)
            return None

        if resp.status_code != 200:
            log.warning(
                "hyperevm_reader.oracle_source_bad_status status=%d",
                resp.status_code,
            )
            return None

        try:
            body = resp.json()
        except (ValueError, TypeError):
            log.warning("hyperevm_reader.oracle_source_bad_json")
            return None

        if not isinstance(body, dict):
            log.warning("hyperevm_reader.oracle_source_bad_body_shape")
            return None

        if "error" in body:
            log.warning(
                "hyperevm_reader.oracle_source_rpc_error err=%s", body["error"],
            )
            return None

        result = body.get("result")
        if not isinstance(result, str) or not result.startswith("0x"):
            log.warning("hyperevm_reader.oracle_source_missing_result")
            return None

        observed = _decode_address_result(result)
        if observed is None:
            log.warning(
                "hyperevm_reader.oracle_source_decode_failed result=%s", result,
            )
            return None

        expected = settings.expected_hyperlend_whype_source
        if observed == _ETH_ZERO_ADDRESS:
            # An unset source — HyperLend governance can clear via
            # setSourceOfAsset(token, 0). This is also a CRITICAL drift case
            # (every Aave-style lookup against this token would now revert).
            log.error(
                "hyperevm_reader.oracle_source_drift expected=%s observed=%s "
                "(source is zero-address — HyperLend has cleared the WHYPE "
                "source; OCDE HYPE divergence signal will degrade)",
                expected, observed,
            )
            return False

        if observed != expected.lower():
            # Loud failure — the operator needs to investigate which source
            # was rotated to (RedStone alternative? kHYPE composite?) and
            # update both settings.expected_hyperlend_whype_source AND
            # settings.redstone_hype_source after manual verification.
            log.error(
                "hyperevm_reader.oracle_source_drift expected=%s observed=%s "
                "(HyperLend has rotated WHYPE oracle source — update "
                "settings.expected_hyperlend_whype_source + "
                "settings.redstone_hype_source after manual verification, "
                "then redeploy)",
                expected, observed,
            )
            return False

        return True
    finally:
        if owns_client:
            await client.aclose()


async def read_redstone_hype_price(  # noqa: PLR0911
    client: httpx.AsyncClient | None = None,
) -> OraclePrice | None:
    """Fetch RedStone HYPE/USD via eth_call to the price source contract.

    The return-count is intentionally high — defensive parsing of an
    untrusted RPC response surfaces ~7 distinct failure modes that each
    deserve their own warning + early return rather than nested branches.

    Args:
        client: optional reusable httpx.AsyncClient (tests inject mocks).
                When None, a fresh client is created with a short timeout.

    Returns:
        OraclePrice with source='redstone', or None on any failure.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [
            {
                "to": settings.redstone_hype_source,
                "data": _LATEST_ANSWER_SELECTOR,
            },
            "latest",
        ],
    }

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S)

    try:
        try:
            resp = await client.post(settings.hyperevm_rpc_url, json=payload)
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("hyperevm_reader.http_error err=%s", e)
            return None

        if resp.status_code != 200:
            log.warning("hyperevm_reader.bad_status status=%d", resp.status_code)
            return None

        try:
            body = resp.json()
        except (ValueError, TypeError):
            log.warning("hyperevm_reader.bad_json")
            return None

        if not isinstance(body, dict):
            log.warning("hyperevm_reader.bad_body_shape")
            return None

        if "error" in body:
            log.warning("hyperevm_reader.rpc_error err=%s", body["error"])
            return None

        result = body.get("result")
        if result is None:
            log.warning("hyperevm_reader.missing_result")
            return None

        raw_int = _decode_int256(result)
        if raw_int is None:
            log.warning("hyperevm_reader.decode_failed result=%s", result)
            return None
        if raw_int <= 0:
            log.warning("hyperevm_reader.non_positive_price raw=%d", raw_int)
            return None

        price_usd = raw_int / (10 ** settings.redstone_hype_decimals)
        ts_ms = int(time.time() * 1000)
        # Note: ts_ms is read time, not the on-chain update time. Use
        # latestRoundData() (selector 0xfeaf968c) if true age is needed.

        return OraclePrice(
            source="redstone",
            asset_alias="hype",
            price_usd=price_usd,
            confidence_usd=0.0,
            timestamp_ms=ts_ms,
        )
    finally:
        if owns_client:
            await client.aclose()


__all__ = ["read_redstone_hype_price", "verify_hyperlend_whype_source"]
