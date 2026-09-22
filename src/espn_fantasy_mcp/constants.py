"""Mapeos de IDs internos de la API de ESPN Fantasy Football."""

# Posición por defecto del jugador (player.defaultPositionId)
POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}

# Huecos de alineación (lineupSlotId / eligibleSlots / lineupSlotCounts)
SLOTS = {
    0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE",
    7: "OP", 8: "DT", 9: "DE", 10: "LB", 11: "DL", 12: "CB", 13: "S",
    14: "DB", 15: "DP", 16: "D/ST", 17: "K", 18: "P", 19: "HC", 20: "BE",
    21: "IR", 23: "FLEX", 24: "ER",
}
BENCH_SLOT = 20
IR_SLOT = 21
NON_STARTING_SLOTS = {BENCH_SLOT, IR_SLOT}

# Slot usado para filtrar agentes libres por posición
POSITION_FILTER_SLOTS = {
    "QB": 0, "RB": 2, "WR": 4, "TE": 6, "FLEX": 23, "D/ST": 16, "DST": 16, "K": 17,
}

PRO_TEAMS = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR",
    15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI",
    22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WSH",
    29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}

# stats[].statSourceId
STAT_SOURCE_ACTUAL = 0
STAT_SOURCE_PROJECTED = 1
# stats[].statSplitTypeId
STAT_SPLIT_SEASON = 0
STAT_SPLIT_WEEK = 1
