"""Servidor MCP (stdio) para consultar y analizar una liga de ESPN Fantasy Football."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .client import (
    Config,
    EspnClient,
    EspnError,
    load_env_file,
    match_player,
    optimal_lineup,
    parse_matchups,
    parse_player,
    parse_roster,
    parse_standings,
    plan_lineup,
    public,
    starting_slot_counts,
    team_name,
)
from .constants import BENCH_SLOT, NON_STARTING_SLOTS, POSITION_FILTER_SLOTS, SLOT_IDS, SLOTS

mcp = MCPServer(
    name="espn-fantasy",
    instructions=(
        "Herramientas para una liga de ESPN Fantasy Football (NFL). "
        "Si no se indica equipo se usa el del usuario (ESPN_TEAM_ID o ESPN_SWID). "
        "Las semanas son scoringPeriodId de ESPN (1-18). "
        "set_lineup y add_drop modifican tu equipo en ESPN: llámalas primero sin confirm, "
        "enseña el resultado al usuario y repite con confirm=true solo si lo aprueba."
    ),
)


@lru_cache(maxsize=1)
def client() -> EspnClient:
    # .env local (ignorado por git) en la carpeta del proyecto o en el directorio actual
    for env in (Path(__file__).resolve().parents[2] / ".env", Path.cwd() / ".env"):
        load_env_file(env)
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

    No modifica nada en ESPN: para aplicarla usa set_lineup sin moves.
    """
    c = client()
    wk = _week(c, week)
    data = c.league("mTeam", "mRoster", "mSettings", scoringPeriodId=wk)
    t = c.resolve_team(data, team)
    roster = parse_roster(t, week=wk, season=c.config.season)
    result = optimal_lineup(roster, starting_slot_counts(data.get("settings", {})))
    return {"team": team_name(t), "week": wk, **public(result)}


WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)


def _my_team(c: EspnClient, data: dict) -> dict:
    if c.my_team_id(data) is None:
        raise EspnError("No sé cuál es tu equipo: configura ESPN_TEAM_ID o ESPN_SWID.")
    return c.resolve_team(data, None)


@mcp.tool(annotations=WRITE)
def set_lineup(moves: dict[str, str] | None = None, week: int | None = None,
               confirm: bool = False) -> dict[str, Any]:
    """Cambia la alineación de TU equipo en ESPN.

    moves: {jugador: slot}, p. ej. {"Deshaun Watson": "QB", "Caleb Williams": "BE"}.
    Slots: QB, RB, WR, TE, FLEX, WR/TE, RB/WR, OP, D/ST, K, BE (banquillo), IR.
    Si alguien entra en un hueco ocupado, el que estaba pasa al hueco que queda libre.
    Sin moves aplica la alineación óptima de suggest_lineup.
    confirm: false = solo muestra los cambios; true = los envía a ESPN. Pide permiso
    al usuario antes de usar true.
    """
    c = client()
    wk = _week(c, week)
    data = c.league("mTeam", "mRoster", "mSettings", scoringPeriodId=wk)
    t = _my_team(c, data)
    roster = parse_roster(t, week=wk, season=c.config.season)
    counts = starting_slot_counts(data.get("settings", {}))

    if moves:
        target = {}
        for name, slot in moves.items():
            slot_id = SLOT_IDS.get(slot.upper().replace(" ", ""))
            if slot_id is None:
                raise EspnError(f"Slot '{slot}' no válido. Usa: QB, RB, WR, TE, FLEX, WR/TE, OP, D/ST, K, BE, IR.")
            target[match_player(roster, name)["id"]] = slot_id
        items = plan_lineup(roster, target, counts)
    else:
        assignment = optimal_lineup(roster, counts)["_assignment"]
        starters = {p["id"] for p in roster if p["_slot_id"] not in NON_STARTING_SLOTS}
        # Cambio mínimo: solo quien entra y quien sale; si no encaja, reordena todo
        minimal = {pid: s for pid, s in assignment.items() if pid not in starters}
        minimal |= {pid: BENCH_SLOT for pid in starters if pid not in assignment}
        try:
            items = plan_lineup(roster, minimal, counts)
        except EspnError:
            full = assignment | {pid: BENCH_SLOT for pid in starters if pid not in assignment}
            items = plan_lineup(roster, full, counts)

    names = {p["id"]: p["name"] for p in roster}
    changes = [
        {"player": names[i["playerId"]], "from": SLOTS.get(i["fromLineupSlotId"]),
         "to": SLOTS.get(i["toLineupSlotId"])}
        for i in items
    ]
    out: dict[str, Any] = {"team": team_name(t), "week": wk, "changes": changes}
    if not items:
        return {**out, "status": "sin cambios"}
    if not confirm:
        return {**out, "status": "pendiente de confirmar (repite con confirm=true)"}
    c.submit_transaction(t["id"], wk, "ROSTER", items)
    return {**out, "status": "aplicado en ESPN"}


@mcp.tool(annotations=WRITE)
def add_drop(add: str, drop: str | None = None, bid: int = 0, week: int | None = None,
             confirm: bool = False) -> dict[str, Any]:
    """Ficha un agente libre (o reclama uno en waivers) para TU equipo y, opcionalmente, suelta a otro.

    add: nombre del jugador libre. drop: jugador de tu plantilla a soltar (necesario si
    la plantilla está llena). bid: puja FAAB, solo para waivers.
    confirm: false = solo muestra la operación; true = la envía a ESPN. Pide permiso
    al usuario antes de usar true: soltar a un jugador puede no tener vuelta atrás.
    """
    c = client()
    wk = _week(c, week)
    data = c.league("mTeam", "mRoster", scoringPeriodId=wk)
    t = _my_team(c, data)
    wanted = match_player([{"id": i, "name": n} for i, n in c.player_names().items()], add)
    [entry] = c.players_by_id([wanted["id"]], wk) or [None]
    if entry is None or entry.get("status") not in ("FREEAGENT", "WAIVERS"):
        owner = next((team_name(x) for x in data.get("teams", []) if entry and x["id"] == entry.get("onTeamId")), None)
        raise EspnError(f"{wanted['name']} no está libre" + (f": juega en {owner}." if owner else "."))
    new = {**parse_player(entry["player"], week=wk, season=c.config.season), "status": entry["status"]}
    items = [{"playerId": new["id"], "type": "ADD", "toTeamId": t["id"]}]
    out: dict[str, Any] = {
        "team": team_name(t),
        "add": {k: new[k] for k in ("name", "position", "pro_team", "injury_status", "projected_points", "status")},
    }
    if drop:
        old = match_player(parse_roster(t, week=wk, season=c.config.season), drop)
        items.append({"playerId": old["id"], "type": "DROP", "fromTeamId": t["id"]})
        out["drop"] = {k: old[k] for k in ("name", "position", "pro_team", "projected_points")}
    waiver = new["status"] == "WAIVERS"
    out["type"] = "waiver (se procesa cuando ESPN resuelva los waivers)" if waiver else "agente libre (inmediato)"
    if not confirm:
        return {**out, "status": "pendiente de confirmar (repite con confirm=true)"}
    c.submit_transaction(t["id"], wk, "WAIVER" if waiver else "FREEAGENT", items, bid=bid if waiver else None)
    return {**out, "status": "reclamación enviada" if waiver else "aplicado en ESPN"}


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
        ids = [i for i, n in c.player_names().items() if q in (n or "").lower()][:25]
        for p in c.players_by_id(ids, wk) if ids else []:
            if p.get("status") in ("FREEAGENT", "WAIVERS"):
                player = parse_player(p["player"], week=wk, season=c.config.season)
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
