"""Mapping from import batch names to the agreed source codes (agreed convention section 7.8).

This module has no dependencies so that table_matches and avg_cells can both use it.
"""
from __future__ import annotations

from typing import Optional

# Import batch names such as "5df_hist_gapfill_20261014" live only in the database. The interface never prints a batch
# name as a source. A source that starts with one of these prefixes and is not already an agreed source code is
# mapped to the agreed code of the same real origin. The agreed code list itself does not change.
# 5df_hist batches contain whole 5DollarFootballAPI history files, so they map to "5df_hist_asof_full".
BATCH_SOURCE_PREFIX_TO_CODE = (
    ("5df_hist", "5df_hist_asof_full"),
)
AGREED_5DF_HISTORY_CODES = frozenset({"5df_hist_asof", "5df_hist_asof_full"})


def canonical_source_code(code: Optional[str]) -> Optional[str]:
    """Map an import batch name to the agreed source code; every other value is returned unchanged."""
    if not isinstance(code, str) or code in AGREED_5DF_HISTORY_CODES:
        return code
    for prefix, agreed in BATCH_SOURCE_PREFIX_TO_CODE:
        if code.startswith(prefix):
            return agreed
    return code
