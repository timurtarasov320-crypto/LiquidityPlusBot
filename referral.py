"""Referral-system helpers shared by handlers and admin analytics."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ReferralLevel:
    referrals: int
    discount: int
    title: str


LEVELS = (
    ReferralLevel(0, 0, "START"),
    ReferralLevel(10, 5, "BRONZE"),
    ReferralLevel(20, 10, "SILVER"),
    ReferralLevel(30, 15, "GOLD"),
    ReferralLevel(40, 20, "PLATINUM"),
    ReferralLevel(50, 25, "DIAMOND"),
    ReferralLevel(100, 30, "ELITE"),
    ReferralLevel(200, 35, "MASTER"),
    ReferralLevel(300, 40, "VIP PARTNER"),
    ReferralLevel(400, 45, "AMBASSADOR"),
    ReferralLevel(500, 50, "LEGEND"),
)


def get_level(referrals: int) -> ReferralLevel:
    current = LEVELS[0]
    for level in LEVELS:
        if referrals >= level.referrals:
            current = level
        else:
            break
    return current


def progress_bar(referrals: int, width: int = 10) -> str:
    next_level = next((level for level in LEVELS if level.referrals > referrals), None)
    if next_level is None:
        return "█" * width
    previous = get_level(referrals).referrals
    span = max(1, next_level.referrals - previous)
    progress = max(0.0, min(1.0, (referrals - previous) / span))
    filled = round(progress * width)
    return "█" * filled + "░" * (width - filled)
