"""CMS SPUF pharmacy channel identifiers used in beneficiary_cost lookups."""

PHARMACY_CHANNELS: tuple[str, ...] = (
    "preferred_retail",
    "standard_retail",
    "preferred_mail",
    "standard_mail",
)

PHARMACY_CHANNEL_LABELS: dict[str, str] = {
    "preferred_retail": "Preferred retail",
    "standard_retail": "Standard retail",
    "preferred_mail": "Preferred mail-order",
    "standard_mail": "Standard mail-order",
}


def channel_cost_bounds(channels: dict) -> tuple[float | None, float | None]:
    """Min/max out-of-pocket across pharmacy channels with numeric estimates."""
    lows: list[float] = []
    highs: list[float] = []
    for channel in channels.values():
        if not isinstance(channel, dict):
            continue
        low = channel.get("cost_low")
        high = channel.get("cost_high")
        if low is not None:
            lows.append(float(low))
        if high is not None:
            highs.append(float(high))
        elif low is not None:
            highs.append(float(low))
    if not lows:
        return None, None
    return min(lows), max(highs) if highs else min(lows)
