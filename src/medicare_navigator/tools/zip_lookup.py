"""Static zip (ZIP3 prefix) -> state lookup.

This is a fixed USPS geographic fact table (which state a zip code's first three
digits were assigned to), completely independent of CMS/plan data. It is used
purely to prefill/suggest a state for plan discovery in the UI — it must never be
used to filter or adjust drug-cost estimates, since CMS SPUF pricing/cost-share
data is not zip- or state-of-residence-keyed.

A handful of ZIP3 prefixes straddle a state border in real life (an inherent USPS
routing quirk); this table assigns each range to a single state, which is a
reasonable best-effort approximation for a UI convenience feature.
"""

from __future__ import annotations

# (start_zip3, end_zip3, state) — checked in order, first match wins.
_ZIP3_RANGES: tuple[tuple[int, int, str], ...] = (
    (6, 9, "PR"),
    (10, 27, "MA"),
    (28, 29, "RI"),
    (30, 38, "NH"),
    (39, 49, "ME"),
    (50, 59, "VT"),
    (60, 69, "CT"),
    (70, 89, "NJ"),
    (100, 149, "NY"),
    (150, 196, "PA"),
    (197, 199, "DE"),
    (200, 205, "DC"),
    (206, 219, "MD"),
    (220, 246, "VA"),
    (247, 268, "WV"),
    (270, 289, "NC"),
    (290, 299, "SC"),
    (300, 319, "GA"),
    (320, 349, "FL"),
    (350, 369, "AL"),
    (370, 385, "TN"),
    (386, 397, "MS"),
    (398, 399, "GA"),
    (400, 427, "KY"),
    (430, 459, "OH"),
    (460, 479, "IN"),
    (480, 499, "MI"),
    (500, 528, "IA"),
    (530, 549, "WI"),
    (550, 567, "MN"),
    (570, 577, "SD"),
    (580, 588, "ND"),
    (590, 599, "MT"),
    (600, 629, "IL"),
    (630, 658, "MO"),
    (660, 679, "KS"),
    (680, 693, "NE"),
    (700, 714, "LA"),
    (716, 729, "AR"),
    (730, 731, "OK"),
    (733, 733, "TX"),
    (734, 749, "OK"),
    (750, 799, "TX"),
    (800, 816, "CO"),
    (820, 831, "WY"),
    (832, 838, "ID"),
    (840, 847, "UT"),
    (850, 865, "AZ"),
    (870, 884, "NM"),
    (885, 885, "TX"),
    (889, 898, "NV"),
    (900, 961, "CA"),
    (962, 966, "CA"),
    (967, 968, "HI"),
    (969, 969, "GU"),
    (970, 979, "OR"),
    (980, 994, "WA"),
    (995, 999, "AK"),
)


def zip_to_state(zip_code: str | None) -> str | None:
    """Resolve a 5-digit zip code to its 2-letter state code, or None if unrecognized."""
    if not zip_code:
        return None
    candidate = zip_code.strip()
    if not (len(candidate) == 5 and candidate.isdigit()):
        return None
    zip3 = int(candidate[:3])
    for start, end, state in _ZIP3_RANGES:
        if start <= zip3 <= end:
            return state
    return None
