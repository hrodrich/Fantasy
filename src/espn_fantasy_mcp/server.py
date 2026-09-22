"""Servidor MCP (stdio) para consultar y analizar una liga de ESPN Fantasy Football."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from mcp.server.mcpserver import MCPServer

from .client import (
    Config,
    EspnClient,
    EspnError,
    optimal_lineup,
    parse_matchups,
    parse_player,
    parse_roster,
    parse_standings,
    public,
    starting_slot_counts,
    team_name,
)
from .constants import POSITION_FILTER_SLOTS, SLOTS

mcp = MCPServer(
    name="espn-fantasy",
    instructions=(
        "Herramientas de solo lectura para una liga de ESPN Fantasy Football (NFL). "
        "Si no se indica equipo se usa el del usuario (ESPN_TEAM_ID o ESPN_SWID). "
        "Las semanas son scoringPeriodId de ESPN (1-18)."
    ),
)


@lru_cache(maxsize=1)
def client() -> EspnClient:
    return EspnClient(Config.from_env())


def _week(c: EspnClient, week: int | None) -> int:
    return week or c.current_week()


@mcp.tool()
def get_league_info() -> dict[str, Any]:
    """Nombre, temporada, semana actual, número de equipos, alineación y tipo de puntuación de la liga."""
    c = client()
    data = c.league("mSettings", "mStatus", "mTeam")
    settings = data.get("settings", {})
    scoring = settings.get("scoringSettings", {})
    rec_items = [i for i in scoring.get("scoringItems", []) if i.get("statId") == 53]
    ppr = rec_items[0].get("points") if rec_items else 0
    my_id = c.my_team_id(data)
    return {
        "name": settings.get("name"),
        "season": data.get("seasonId"),
        "current_week": c.current_week(data),
        "current_matchup_period": data.get("status", {}).get("currentMatchupPeriod"),
        "teams": len(data.get("teams", [])),
        "my_team": team_name(c.resolve_team(data, my_id)) if my_id else None,
        "scoring": {1: "PPR", 0.5: "Half-PPR", 0: "Standard"}.get(ppr, f"{ppr} pts/recepción"),
        "starting_lineup": {SLOTS.get(k, str(k)): v for k, v in starting_slot_counts(settings).items()},
        "regular_season_matchups": (settings.get("scheduleSettings") or {}).get("matchupPeriodCount"),
        "playoff_teams": (settings.get("scheduleSettings") or {}).get("playoffTeamCount"),
        "trade_deadline": (settings.get("tradeSettings") or {}).get("deadlineDate"),
    }


@mcp.tool()
def get_standings() -> list[dict[str, Any]]:
    """Clasificación: récord, puntos a favor/en contra, racha y seed de playoffs de cada equipo."""
    return parse_standings(client().league("mTeam", "mStandings"))


@mcp.tool()
def get_roster(team: str | None = None, week: int | None = None) -> dict[str, Any]:
    """Plantilla de un equipo con slot de alineación, lesiones, puntos y proyecciones.

    team: ID, nombre (o parte) o abreviatura del equipo. Por defecto, el tuyo.
    week: semana; por defecto la actual.
    """
    c = client()
    wk = _week(c, week)
    data = c.league("mTeam", "mRoster", scoringPeriodId=wk)
    t = c.resolve_team(data, team)
    roster = parse_roster(t, week=wk, season=c.config.season)
    return {"team": team_name(t), "team_id": t["id"], "week": wk, "players": [public(p) for p in roster]}


@mcp.tool()
def get_matchups(week: int | None = None) -> dict[str, Any]:
    """Enfrentamientos de una jornada con puntos (en vivo si está en curso) y proyecciones."""
    c = client()
    status = c.league("mStatus")
    period = week or status.get("status", {}).get("currentMatchupPeriod") or c.current_week(status)
    data = c.league("mTeam", "mMatchupScore", "mScoreboard", scoringPeriodId=period)
    return {"matchup_period": period, "matchups": parse_matchups(data, period)}


@mcp.tool()
def suggest_lineup(team: str | None = None, week: int | None = None) -> dict[str, Any]:
    """Alineación óptima según las proyecciones de ESPN y los cambios respecto a la actual.

    No modifica nada en ESPN: aplica los cambios en la app.
    """
    c = client()
    wk = _week(c, week)
    data = c.league("mTeam", "mRoster", "mSettings", scoringPeriodId=wk)
    t = c.resolve_team(data, team)
    roster = parse_roster(t, week=wk, season=c.config.season)
    result = optimal_lineup(roster, starting_slot_counts(data.get("settings", {})))
    return {"team": team_name(t), "week": wk, **result}


@mcp.tool()
def get_free_agents(position: str | None = None, limit: int = 25, week: int | None = None,
                    include_waivers: bool = True) -> dict[str, Any]:
    """Mejores agentes libres (ordenados por % de propiedad) con proyecciones.

    position: QB, RB, WR, TE, FLEX, D/ST o K. Vacío = todas.
    """
    c = client()
    wk = _week(c, week)
    slot = None
    if position:
        slot = POSITION_FILTER_SLOTS.get(position.upper().replace(" ", ""))
        if slot is None:
            raise EspnError(f"Posición '{position}' no válida. Usa: {', '.join(POSITION_FILTER_SLOTS)}")
    statuses = ["FREEAGENT", "WAIVERS"] if include_waivers else ["FREEAGENT"]
    players = c.free_agents(wk, slot, max(1, min(limit, 100)), statuses)
    return {
        "week": wk,
        "players": [
            {**public(parse_player(p["player"], week=wk, season=c.config.season)), "status": p.get("status")}
            for p in players
        ],
    }


@mcp.tool()
def find_player(name: str, week: int | None = None) -> list[dict[str, Any]]:
    """Busca un jugador por nombre en las plantillas de la liga y entre los agentes libres."""
    c = client()
    wk = _week(c, week)
    q = name.lower().strip()
    data = c.league("mTeam", "mRoster", scoringPeriodId=wk)
    found = []
    for t in data.get("teams", []):
        for p in parse_roster(t, week=wk, season=c.config.season):
            if q in (p["name"] or "").lower():
                found.append({**public(p), "fantasy_team": team_name(t)})
    if not found:
        for p in c.free_agents(wk, None, 500, ["FREEAGENT", "WAIVERS"]):
            player = parse_player(p["player"], week=wk, season=c.config.season)
            if q in (player["name"] or "").lower():
                found.append({**public(player), "fantasy_team": None, "status": p.get("status")})
    return found


@mcp.tool()
def get_recent_transactions(week: int | None = None) -> list[dict[str, Any]]:
    """Fichajes, waivers y traspasos ejecutados en la liga durante una semana."""
    c = client()
    wk = _week(c, week)
    data = c.league("mTeam", "mTransactions2", scoringPeriodId=wk)
    teams = {t["id"]: team_name(t) for t in data.get("teams", [])}
    names = c.player_names()
    out = []
    for tx in data.get("transactions", []):
        if tx.get("status") != "EXECUTED" or tx.get("type") not in ("FREEAGENT", "WAIVER", "TRADE_ACCEPT"):
            continue
        out.append({
            "type": tx.get("type"),
            "team": teams.get(tx.get("teamId")),
            "date_ms": tx.get("processDate") or tx.get("proposedDate"),
            "bid": tx.get("bidAmount"),
            "items": [
                {
                    "action": i.get("type"),
                    "player": names.get(i.get("playerId"), i.get("playerId")),
                    "from": teams.get(i.get("fromTeamId"), "FA"),
                    "to": teams.get(i.get("toTeamId"), "FA"),
                }
                for i in tx.get("items", [])
            ],
        })
    return sorted(out, key=lambda x: x["date_ms"] or 0, reverse=True)


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
