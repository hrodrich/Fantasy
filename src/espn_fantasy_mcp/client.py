"""Cliente para la API (no oficial) de ESPN Fantasy Football.

Lee datos de la liga y, con las cookies de sesión, envía cambios de alineación y
fichajes de tu equipo. Las funciones ``parse_*``, ``optimal_lineup`` y
``plan_lineup`` son puras (trabajan sobre el JSON
que devuelve ESPN) para poder probarlas sin red.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from .constants import (
    BENCH_SLOT,
    IR_SLOT,
    NON_STARTING_SLOTS,
    POSITIONS,
    PRO_TEAMS,
    SLOTS,
    STAT_SOURCE_ACTUAL,
    STAT_SOURCE_PROJECTED,
    STAT_SPLIT_SEASON,
    STAT_SPLIT_WEEK,
)

BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
WRITE_URL = "https://lm-api-writes.fantasy.espn.com/apis/v3/games/ffl"


class EspnError(ToolError):
    """Error cuyo mensaje se muestra tal cual al modelo."""


def load_env_file(path: Path) -> None:
    """Carga ``CLAVE=valor`` de un .env sin pisar variables ya definidas con valor."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key or key.startswith("#"):
            continue
        if not os.environ.get(key, "").strip():
            os.environ[key] = value.strip().strip("'\"")


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


RECENT_WEEKS = 3


def _recent_points(stats: list[dict], *, week: int, season: int) -> dict[int, float]:
    """Puntos reales por semana en las últimas RECENT_WEEKS semanas ya jugadas."""
    out = {}
    for s in stats or []:
        wk = s.get("scoringPeriodId")
        if (s.get("statSourceId") == STAT_SOURCE_ACTUAL and s.get("statSplitTypeId") == STAT_SPLIT_WEEK
                and s.get("seasonId", season) == season and wk and week - RECENT_WEEKS <= wk < week):
            out[wk] = round(float(s.get("appliedTotal", 0.0)), 2)
    return dict(sorted(out.items()))


def parse_player(player: dict, *, week: int, season: int, lineup_slot: int | None = None) -> dict:
    stats = player.get("stats", [])
    recent = _recent_points(stats, week=week, season=season)
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
        "recent_points": recent,
        "recent_avg": round(sum(recent.values()) / len(recent), 2) if recent else None,
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
    assignment: dict[int, int] = {}
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
            assignment[best["id"]] = slot
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
        "_assignment": assignment,
    }


def plan_lineup(roster: list[dict], moves: dict[int, int], slot_counts: dict[int, int]) -> list[dict]:
    """Traduce ``{player_id: slot_id}`` a los items LINEUP de una transacción de ESPN.

    Si un jugador entra en un hueco ya ocupado por otro al que no se mueve, este
    pasa al hueco que deja libre el primero (o al banquillo si no puede jugar ahí).
    """
    by_id = {p["id"]: p for p in roster}
    final = {p["id"]: p["_slot_id"] for p in roster}
    for pid, slot in moves.items():
        p = by_id[pid]
        if slot not in p.get("_eligible_slot_ids", []):
            raise EspnError(f"{p['name']} no puede jugar en {SLOTS.get(slot, slot)}.")
        if slot not in NON_STARTING_SLOTS and not slot_counts.get(slot):
            raise EspnError(f"Tu liga no tiene hueco {SLOTS.get(slot, slot)} en la alineación.")
        final[pid] = slot

    for pid, slot in moves.items():
        if slot in NON_STARTING_SLOTS:
            continue
        occupants = [q for q, s in final.items() if s == slot]
        extra = len(occupants) - slot_counts[slot]
        if extra <= 0:
            continue
        displaced = [q for q in occupants if q not in moves]
        if len(displaced) != extra:
            names = ", ".join(by_id[q]["name"] for q in displaced)
            raise EspnError(
                f"El hueco {SLOTS[slot]} está lleno: indica a quién sacar de ahí ({names})."
            )
        vacated = by_id[pid]["_slot_id"]
        for q in displaced:
            final[q] = vacated if vacated in by_id[q].get("_eligible_slot_ids", []) else BENCH_SLOT

    for slot, cap in slot_counts.items():
        taken = [q for q, s in final.items() if s == slot]
        if len(taken) > cap:
            names = ", ".join(by_id[q]["name"] for q in taken)
            raise EspnError(f"Demasiados jugadores en {SLOTS.get(slot, slot)} ({cap} máx.): {names}.")

    return [
        {
            "playerId": pid,
            "type": "LINEUP",
            "fromLineupSlotId": by_id[pid]["_slot_id"],
            "toLineupSlotId": slot,
        }
        for pid, slot in final.items()
        if slot != by_id[pid]["_slot_id"]
    ]


def match_player(players: list[dict], query: str) -> dict:
    """Un único jugador por nombre (exacto o parcial, sin distinguir mayúsculas)."""
    q = query.lower().strip()
    exact = [p for p in players if (p.get("name") or "").lower() == q]
    found = exact or [p for p in players if q in (p.get("name") or "").lower()]
    if not found:
        raise EspnError(f"No encuentro a '{query}'.")
    if len(found) > 1:
        raise EspnError(f"'{query}' es ambiguo: {', '.join(p['name'] for p in found)}.")
    return found[0]


def public(p: dict) -> dict:
    """Quita los campos internos (con prefijo '_')."""
    return {k: v for k, v in p.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _error_message(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    msgs = [d.get("message") for d in data.get("details", []) if isinstance(d, dict)]
    msgs += [m for m in data.get("messages", []) if isinstance(m, str)]
    return "; ".join(m for m in msgs if m) or None

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

    def submit_transaction(self, team_id: int, week: int, tx_type: str, items: list[dict],
                           bid: int | None = None, extra: dict | None = None) -> dict:
        """Envía una transacción (ROSTER, FREEAGENT, WAIVER, TRADE_PROPOSAL) de tu equipo a ESPN."""
        c = self.config
        if not (c.espn_s2 and c.swid):
            raise EspnError("Para hacer cambios en ESPN hacen falta ESPN_S2 y ESPN_SWID.")
        payload: dict[str, Any] = {
            "isLeagueManager": False,
            "teamId": team_id,
            "type": tx_type,
            "memberId": c.swid if c.swid.startswith("{") else "{" + c.swid + "}",
            "scoringPeriodId": week,
            "executionType": "EXECUTE",
            "items": items,
        }
        if bid is not None:
            payload["bidAmount"] = bid
        payload |= extra or {}
        url = f"{WRITE_URL}/seasons/{c.season}/segments/0/leagues/{c.league_id}/transactions/"
        try:
            resp = self.http.post(url, json=payload, headers={"Accept": "application/json"})
        except httpx.HTTPError as e:
            raise EspnError(f"No se pudo conectar con ESPN: {e}") from e
        try:
            data = resp.json()
        except ValueError:
            data = None
        if resp.status_code in (401, 403):
            raise EspnError("ESPN no autorizó el cambio: revisa ESPN_S2/ESPN_SWID (quizá han caducado).")
        if resp.is_error:
            detail = _error_message(data) or resp.text[:300]
            raise EspnError(f"ESPN rechazó el cambio (HTTP {resp.status_code}): {detail}")
        if data is None:
            raise EspnError("Respuesta no válida de ESPN; revisa ESPN_S2/ESPN_SWID.")
        return data

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

    def _stats_filter(self, week: int) -> dict:
        return {
            "value": 2,
            "additionalValue": [f"00{self.config.season}", f"10{self.config.season}",
                                f"11{self.config.season}{week}"]
                               + [f"01{self.config.season}{w}" for w in range(max(1, week - RECENT_WEEKS), week)],
        }

    def players_by_id(self, ids: list[int], week: int) -> list[dict]:
        """Entradas de jugadores con su estado en la liga (FREEAGENT, WAIVERS u ONTEAM)."""
        players_filter = {"filterIds": {"value": ids}, "filterStatsForTopScoringPeriodIds": self._stats_filter(week)}
        data = self._get(self.league_url, ["kona_player_info"], {"scoringPeriodId": week},
                         fantasy_filter={"players": players_filter})
        return data.get("players", [])

    def free_agents(self, week: int, slot_id: int | None, limit: int, statuses: list[str]) -> list[dict]:
        players_filter: dict[str, Any] = {
            "filterStatus": {"value": statuses},
            "limit": limit,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
            "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
            "filterRanksForScoringPeriodIds": {"value": [week]},
            "filterStatsForTopScoringPeriodIds": self._stats_filter(week),
        }
        if slot_id is not None:
            players_filter["filterSlotIds"] = {"value": [slot_id]}
        data = self._get(self.league_url, ["kona_player_info"], {"scoringPeriodId": week},
                         fantasy_filter={"players": players_filter})
        return data.get("players", [])
