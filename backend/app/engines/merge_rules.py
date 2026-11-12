"""Business-key merge / conflict decisions for Feedstitch."""

from __future__ import annotations

from typing import Any


NON_KEY_FIELDS = ("title", "price", "category")


def non_key_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    return {k: item[k] for k in NON_KEY_FIELDS}


def fields_conflict(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    return non_key_snapshot(existing) != non_key_snapshot(incoming)


def can_version_key(
    existing: dict[str, Any] | None,
    incoming_source_id: int,
    incoming_authority_group: str | None,
    *,
    existing_authority_group: str | None = None,
) -> bool:
    """Whether an incoming source may open a new SCD2 version for the key.

    A key can only be evolved by a source that owns it with the same
    authority: either the same source that currently holds the key, or two
    sources sharing a non-null ``authority_group`` (e.g. a catalog and its
    refresh feed). Divergence from any other source stays a conflict and is
    quarantined instead of touching the master data.
    """
    if existing is None:
        return True
    if existing.get("source_id") == incoming_source_id:
        return True
    return bool(
        incoming_authority_group
        and existing_authority_group
        and incoming_authority_group == existing_authority_group
    )


def decide_merge(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
    versioning_allowed: bool = False,
) -> str:
    """Return one of: insert | upsert | version_update | conflict | skip_stale.

    Base rules (lookback not implemented):
    - no existing → insert
    - non-key diverge:
        * versioning_allowed (same source / authority group) → version_update,
          the caller closes the old SCD2 version and opens a new one;
        * otherwise → conflict, the row is quarantined and master data is
          left untouched;
    - same non-key, incoming revision > existing → upsert
    - else → skip_stale
    """
    if existing is None:
        return "insert"
    if fields_conflict(existing, incoming):
        if versioning_allowed:
            return "version_update"
        return "conflict"
    if int(incoming["source_revision"]) > int(existing["source_revision"]):
        return "upsert"
    return "skip_stale"


def eligible_for_incremental(
    event_ts: str,
    watermark_ts: str | None,
    lookback_seconds: int = 0,
) -> bool:
    """Base: event_ts must be strictly after watermark. lookback reserved for 0-1."""
    del lookback_seconds  # reserved; base ignores lookback window
    if not watermark_ts:
        return True
    return event_ts > watermark_ts
