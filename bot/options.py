# Option codes for listing fields. Human-readable labels live in locales
# under the key "<field>_<code>", e.g. "sector_centru".

SECTOR = ("centru", "botanica", "buiucani", "ciocana", "riscani", "suburb")
ROOMS = ("studio", "1", "2", "3", "4")
BUILDING = ("new", "old")
HEATING = ("autonomous", "central", "none")
RENOVATION = ("euro", "design", "cosmetic", "none")
FURNITURE = ("full", "partial", "none")
AMENITIES = (
    "washer", "fridge", "ac", "dishwasher", "tv", "wifi", "boiler",
    "balcony", "elevator", "parking", "pets", "kids", "video",
)
TERMS = ("day", "m1_3", "m3_6", "m6")
UTILITIES = ("included", "separate")

MIN_PHOTOS = 3
MAX_PHOTOS = 10
