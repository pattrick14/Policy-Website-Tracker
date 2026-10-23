"""Live-only Step 8 acceptance items (operator steps in docs/ACCEPTANCE.md).

They need the three government sites and a real SMTP account, which the
deterministic suite never touches. Each is a documented skip so the gap is
visible in every test report (``-ra``).
"""

from __future__ import annotations

import pytest

LIVE = "live-only: needs network access to the government sites; see docs/ACCEPTANCE.md"


@pytest.mark.skip(reason=f"{LIVE} (L1: silent live baseline)")
def test_live_silent_baseline_completes_every_feed() -> None:
    """L1: ``policy-tracker baseline`` against the live sites completes every feed."""


@pytest.mark.skip(reason=f"{LIVE} (L2: live baseline totals vs SOURCE_INVENTORY counts)")
def test_live_baseline_totals_match_the_inventory() -> None:
    """L2: per-feed ``last_item_count`` agrees with docs/SOURCE_INVENTORY.md totals."""


@pytest.mark.skip(reason="live-only: needs real SMTP credentials and a test mailbox (L3)")
def test_controlled_real_email_delivery() -> None:
    """L3: one real digest to a controlled mailbox; headers, TLS, Message-ID checked."""


@pytest.mark.skip(reason=f"{LIVE} (L4: live block-page and rate-limit behaviour)")
def test_live_block_pages_are_quarantined() -> None:
    """L4: observed live block/CAPTCHA pages are quarantined, never parsed as empty."""
