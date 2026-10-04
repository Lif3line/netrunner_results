"""Event Categories based on ABR lists."""

# Current championship categories first, then other categories
EVENT_TYPES = {
    "District championship": "district championship",
    "Megacity championship": "megacity championship",
    "Continental championship": "continental championship",
    "Intercontinental championship": "intercontinental championship",
    "World championship": "worlds championship",
    "Circuit Breaker Invitational": "circuit breaker invitational",
    "Game night kit / seasonal": "gnk / seasonal",
    "Casual tournament kit": "casual tournament kit",
    "Players circuit": "players circuit",
    "NANPC event": "nanpc event",
    "Accelerated meta test": "accelerated meta test",
    "Community tournament": "community tournament",
    "Online event": "online event",
    "Team tournament": "team tournament",
    "Asynchronous tournament": "asynchronous tournament",
    "Non-tournament event": "non-tournament event",
    "Store championship": "store championship",
    "Regional championship": "regional championship",
    "National championship": "national championship",
    "Unknown / unlabelled": "unknown",
}


def normalise_type(value):
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    name = value.strip().casefold()
    return {
        "game night kit": "gnk / seasonal",
        "world championship": "worlds championship",
        "community": "community tournament",
        "asynchronous": "asynchronous tournament",
    }.get(name, name)
