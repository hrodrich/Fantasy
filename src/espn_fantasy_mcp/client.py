"""Cliente de solo lectura para la API (no oficial) de ESPN Fantasy Football.

Las funciones ``parse_*`` y ``optimal_lineup`` son puras (trabajan sobre el JSON
que devuelve ESPN) para poder probarlas sin red.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from .constants import (
    IR_SLOT,
    NON_STARTING_SLOTS,
    POSITIONS,
    PRO_TEAMS,
    SLOTS,
    STAT_SOURCE_ACTUAL,
    STAT_SOURCE_PROJECTED,
    STAT_SPLIT_SEASON,
)

BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"


class EspnError(ToolError):
    """Error cuyo mensaje se muestra tal cual al modelo."""


@dataclass
class Config:
    league_id: int
    season: int
    espn_s2: str | None = None
    swid: str | None = None
    team_id: int | None = None

    @classmethod
    def from_env(cls) -> "Config":
        league_id = os.environ.get("ESPN_LEAGUE_ID", "").strip()
        if not league_id.isdigit():
            raise EspnError(
                "Falta ESPN_LEAGUE_ID (el número tras 'leagueId=' en la URL de tu liga)."
            )
        season = os.environ.get("ESPN_SEASON", "").strip()
        team_id = os.environ.get("ESPN_TEAM_ID", "").strip()
        return cls(
            league_id=int(league_id),
            season=int(season) if season.isdigit() else dt.date.today().year,
            espn_s2=os.environ.get("ESPN_S2", "").strip() or None,
            swid=os.environ.get("ESPN_SWID", "").strip() or None,
            team_id=int(team_id) if team_id.isdigit() else None,
        )


# --------------------------------------------------------------------------- #
# Parseo
# --------------------------------------------------------------------------- #

def _norm_swid(swid: str | None) -> str:
    return (swid or "").strip().strip("{}").upper()


def team_name(team: dict) -> str:
    if team.get("name"):
        return team["name"]
    return f"{team.get('location', '')} {team.get('nickname', '')}".strip() or f"Equipo {team['id']}"


def _stat_total(stats: list[dict], *, period: int, source: int, season: int | None = None) -> float | None:
    for s in stats or []:
        if s.get("scoringPeriodId") != period or s.get("statSourceId") != source:
            continue
        if period == 0 and (s.get("statSplitTypeId") != STAT_SPLIT_SEASON or (season and s.get("seasonId") != season)):
            continue
        return round(float(s.get("appliedTotal", 0.0)), 2)
    return None


def parse_player(player: dict, *, week: int, season: int, lineup_slot: int | None = None) -> dict:
    stats = player.get("stats", [])
    out = {
        "id": player.get("id"),
        "name": player.get("fullName"),
        "position": POSITIONS.get(player.get("defaultPositionId"), str(player.get("defaultPositionId"))),
        "pro_team": PRO_TEAMS.get(player.get("proTeamId"), str(player.get("proTeamId"))),
        "injury_status": player.get("injuryStatus") or ("INJURED" if player.get("injured") else "ACTIVE"),
        "eligible_slots": [SLOTS[s] for s in player.get("eligibleSlots", []) if s in SLOTS],
        "projected_points": _stat_total(stats, period=week, source=STAT_SOURCE_PROJECTED),
        "points": _stat_total(stats, period=week, source=STAT_SOURCE_ACTUAL),
        "season_points": _stat_total(stats, period=0, source=STAT_SOURCE_ACTUAL, season=season),
        "season_projected_points": _stat_total(stats, period=0, source=STAT_SOURCE_PROJECTED, season=season),
    }
    owned = (player.get("ownership") or {}).get("percentOwned")
    if owned is not None:
        out["percent_owned"] = round(owned, 1)
    if lineup_slot is not None:
        out["lineup_slot"] = SLOTS.get(lineup_slot, str(lineup_slot))
        out["_slot_id"] = lineup_slot
        out["_eligible_slot_ids"] = player.get("eligibleSlots", [])
    return out


def parse_roster(team: dict, *, week: int, season: int) -> list[dict]:
    entries = (team.get("roster") or {}).get("entries", [])
    return [
        parse_player(e["playerPoolEntry"]["player"], week=week, season=season, lineup_slot=e.get("lineupSlotId"))
        for e in entries
    ]


def parse_standings(data: dict) -> list[dict]:
    members = {m["id"]: m.get("displayName") for m in data.get("members", [])}
    rows = []
    for t in data.get("teams", []):
        rec = (t.get("record") or {}).get("overall", {})
        rows.append({
            "team_id": t["id"],
            "name": team_name(t),
            "abbrev": t.get("abbrev"),
            "owners": [members.get(o, o) for o in t.get("owners", [])],
            "wins": rec.get("wins", 0),
            "losses": rec.get("losses", 0),
            "ties": rec.get("ties", 0),
            "points_for": round(rec.get("pointsFor", 0.0), 2),
            "points_against": round(rec.get("pointsAgainst", 0.0), 2),
            "streak": f"{rec.get('streakType', '')[:1]}{rec.get('streakLength', '')}".strip() or None,
            "playoff_seed": t.get("playoffSeed"),
        })
    rows.sort(key=lambda r: (r["playoff_seed"] or 99, -r["wins"], -r["points_for"]))
    return rows


def parse_matchups(data: dict, matchup_period: int) -> list[dict]:
    names = {t["id"]: team_name(t) for t in data.get("teams", [])}

    def side(s: dict | None) -> dict | None:
        if not s:
            return None
        points = s.get("totalPointsLive", s.get("totalPoints"))
        proj = s.get("totalProjectedPointsLive")
        return {
            "team_id": s.get("teamId"),
            "name": names.get(s.get("teamId")),
            "points": round(points, 2) if points is not None else None,
            "projected_points": round(proj, 2) if proj is not None else None,
        }

    return [
        {"home": side(m.get("home")), "away": side(m.get("away")), "winner": m.get("winner")}
        for m in data.get("schedule", [])
        if m.get("matchupPeriodId") == matchup_period
    ]


def starting_slot_counts(settings: dict) -> dict[int, int]:
    counts = ((settings.get("rosterSettings") or {}).get("lineupSlotCounts")) or {}
    return {int(k): v for k, v in counts.items() if v and int(k) not in NON_STARTING_SLOTS}


def optimal_lineup(roster: list[dict], slot_counts: dict[int, int]) -> dict:
    """Mejor alineación según proyecciones.

    Rellena primero los huecos más restrictivos (menos posiciones elegibles) y
    después los flex, cogiendo siempre al jugador con mayor proyección.
    """
    pool = [
        p for p in roster
        if p.get("_slot_id") != IR_SLOT and p.get("injury_status") not in ("OUT", "INJURY_RESERVE", "SUSPENSION")
    ]

    def flexibility(slot: int) -> int:
        return sum(1 for p in pool if slot in p.get("_eligible_slot_ids", []))

    used: set = set()
    starters = []
    for slot in sorted(slot_counts, key=lambda s: (flexibility(s), s)):
        for _ in range(slot_counts[slot]):
            candidates = [
                p for p in pool
                if p["id"] not in used and slot in p.get("_eligible_slot_ids", [])
            ]
            best = max(candidates, key=lambda p: p.get("projected_points") or 0.0, default=None)
            if best is None:
                starters.append({"slot": SLOTS.get(slot, str(slot)), "player": None})
                continue
            used.add(best["id"])
            starters.append({
                "slot": SLOTS.get(slot, str(slot)),
                "player": best["name"],
                "projected_points": best.get("projected_points"),
                "currently_in": best.get("lineup_slot"),
            })

    current_starters = {p["id"] for p in roster if p.get("_slot_id") not in NON_STARTING_SLOTS}
    changes = {
        "start": [p["name"] for p in roster if p["id"] in used and p["id"] not in current_starters],
        "bench": [p["name"] for p in roster if p["id"] in current_starters and p["id"] not in used],
    }
    current_total = sum(p.get("projected_points") or 0 for p in roster if p["id"] in current_starters)
    optimal_total = sum(s.get("projected_points") or 0 for s in starters)
    return {
        "lineup": starters,
        "changes": changes,
        "projected_total_current": round(current_total, 2),
        "projected_total_optimal": round(optimal_total, 2),
    }


def public(p: dict) -> dict:
    """Quita los campos internos (con prefijo '_')."""
    return {k: v for k, v in p.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

class EspnClient:
    def __init__(self, config: Config, http: httpx.Client | None = None):
        self.config = config
        cookies = {}
        if config.espn_s2 and config.swid:
            swid = config.swid if config.swid.startswith("{") else "{" + config.swid + "}"
            cookies = {"espn_s2": config.espn_s2, "SWID": swid}
        self.http = http or httpx.Client(timeout=20.0, cookies=cookies, follow_redirects=True)
        self._player_names: dict[int, str] | None = None

    @property
    def league_url(self) -> str:
        c = self.config
        return f"{BASE_URL}/seasons/{c.season}/segments/0/leagues/{c.league_id}"

    def _get(self, url: str, views: list[str], params: dict | None = None, fantasy_filter: dict | None = None) -> Any:
        query: list[tuple[str, Any]] = [("view", v) for v in views]
        query += list((params or {}).items())
        headers = {"Accept": "application/json"}
        if fantasy_filter is not None:
            headers["x-fantasy-filter"] = json.dumps(fantasy_filter)
        try:
            resp = self.http.get(url, params=query, headers=headers)
        except httpx.HTTPError as e:
            raise EspnError(f"No se pudo conectar con ESPN: {e}") from e
        if resp.status_code in (401, 403):
            raise EspnError(
                "ESPN rechazó la petición (liga privada). Configura ESPN_S2 y ESPN_SWID con las cookies de tu sesión."
            )
        if resp.status_code == 404:
            raise EspnError(
                f"Liga {self.config.league_id} no encontrada para la temporada {self.config.season}."
            )
        if resp.is_error:
            raise EspnError(f"ESPN devolvió HTTP {resp.status_code}.")
        try:
            return resp.json()
        except ValueError as e:
            # ESPN devuelve una página HTML de login cuando las cookies no son válidas
            raise EspnError("Respuesta no válida de ESPN; revisa ESPN_S2/ESPN_SWID.") from e

    def league(self, *views: str, **params: Any) -> dict:
        return self._get(self.league_url, list(views), params)

    # -- helpers ------------------------------------------------------------ #

    def current_week(self, data: dict | None = None) -> int:
        data = data or self.league("mStatus")
        return data.get("scoringPeriodId") or data.get("status", {}).get("currentMatchupPeriod") or 1

    def my_team_id(self, data: dict) -> int | None:
        if self.config.team_id:
            return self.config.team_id
        swid = _norm_swid(self.config.swid)
        if not swid:
            return None
        for t in data.get("teams", []):
            if swid in (_norm_swid(o) for o in t.get("owners", [])):
                return t["id"]
        return None

    def resolve_team(self, data: dict, team: str | int | None) -> dict:
        teams = data.get("teams", [])
        if team in (None, ""):
            tid = self.my_team_id(data)
            if tid is None:
                raise EspnError(
                    "No sé cuál es tu equipo: indica el equipo o configura ESPN_TEAM_ID / ESPN_SWID."
                )
            team = tid
        if isinstance(team, int) or str(team).isdigit():
            for t in teams:
                if t["id"] == int(team):
                    return t
        else:
            q = str(team).lower()
            for t in teams:
                if q in team_name(t).lower() or q == (t.get("abbrev") or "").lower():
                    return t
        raise EspnError(f"Equipo '{team}' no encontrado. Usa get_standings para ver los equipos.")

    def player_names(self) -> dict[int, str]:
        if self._player_names is None:
            url = f"{BASE_URL}/seasons/{self.config.season}/players"
            data = self._get(url, ["players_wl"], {"scoringPeriodId": 0},
                             fantasy_filter={"filterActive": {"value": True}})
            self._player_names = {p["id"]: p.get("fullName") for p in data}
        return self._player_names

    def free_agents(self, week: int, slot_id: int | None, limit: int, statuses: list[str]) -> list[dict]:
        players_filter: dict[str, Any] = {
            "filterStatus": {"value": statuses},
            "limit": limit,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
            "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
            "filterRanksForScoringPeriodIds": {"value": [week]},
            "filterStatsForTopScoringPeriodIds": {
                "value": 2,
                "additionalValue": [f"00{self.config.season}", f"10{self.config.season}",
                                    f"11{self.config.season}{week}"],
            },
        }
        if slot_id is not None:
            players_filter["filterSlotIds"] = {"value": [slot_id]}
        data = self._get(self.league_url, ["kona_player_info"], {"scoringPeriodId": week},
                         fantasy_filter={"players": players_filter})
        return data.get("players", [])
