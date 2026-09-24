import json

import httpx
import pytest

from espn_fantasy_mcp.client import (
    Config,
    EspnClient,
    EspnError,
    load_env_file,
    optimal_lineup,
    parse_matchups,
    parse_roster,
    parse_standings,
    plan_lineup,
    starting_slot_counts,
)

SEASON = 2026
WEEK = 3


def player(pid, name, pos, slots, proj, injury="ACTIVE"):
    return {
        "id": pid, "fullName": name, "defaultPositionId": pos, "proTeamId": 2,
        "injuryStatus": injury, "eligibleSlots": slots,
        "stats": [
            {"scoringPeriodId": WEEK, "statSourceId": 1, "statSplitTypeId": 1, "seasonId": SEASON, "appliedTotal": proj},
            {"scoringPeriodId": 0, "statSourceId": 0, "statSplitTypeId": 0, "seasonId": SEASON, "appliedTotal": proj * 2},
        ],
    }


QB, RB, WR, TE, FLEX, BE, IR = 0, 2, 4, 6, 23, 20, 21
TEAM = {
    "id": 1, "name": "Los Toros", "abbrev": "TOR", "owners": ["{ABC-123}"], "playoffSeed": 2,
    "record": {"overall": {"wins": 2, "losses": 1, "pointsFor": 300.456, "pointsAgainst": 250, "streakType": "WIN", "streakLength": 2}},
    "roster": {"entries": [
        {"lineupSlotId": QB, "playerPoolEntry": {"player": player(10, "QB Uno", 1, [QB, 7, BE, IR], 20)}},
        {"lineupSlotId": RB, "playerPoolEntry": {"player": player(20, "RB Malo", 2, [RB, 3, FLEX, BE, IR], 5)}},
        {"lineupSlotId": WR, "playerPoolEntry": {"player": player(30, "WR Uno", 3, [3, WR, 5, FLEX, BE, IR], 15)}},
        {"lineupSlotId": FLEX, "playerPoolEntry": {"player": player(31, "WR Dos", 3, [3, WR, 5, FLEX, BE, IR], 9)}},
        {"lineupSlotId": TE, "playerPoolEntry": {"player": player(40, "TE Uno", 4, [5, TE, FLEX, BE, IR], 8)}},
        {"lineupSlotId": BE, "playerPoolEntry": {"player": player(21, "RB Bueno", 2, [RB, 3, FLEX, BE, IR], 14)}},
        {"lineupSlotId": BE, "playerPoolEntry": {"player": player(22, "RB Lesionado", 2, [RB, 3, FLEX, BE, IR], 30, injury="OUT")}},
        {"lineupSlotId": IR, "playerPoolEntry": {"player": player(23, "RB en IR", 2, [RB, 3, FLEX, BE, IR], 25)}},
    ]},
}
OTHER = {"id": 2, "location": "Club", "nickname": "Rival", "owners": ["{XYZ}"], "playoffSeed": 1,
         "record": {"overall": {"wins": 3, "losses": 0, "pointsFor": 350}}}
SETTINGS = {"rosterSettings": {"lineupSlotCounts": {"0": 1, "2": 1, "4": 1, "6": 1, "23": 1, "20": 6, "21": 1, "17": 0}}}


def test_parse_roster():
    roster = parse_roster(TEAM, week=WEEK, season=SEASON)
    qb = roster[0]
    assert qb["name"] == "QB Uno" and qb["position"] == "QB" and qb["pro_team"] == "BUF"
    assert qb["projected_points"] == 20 and qb["season_points"] == 40 and qb["points"] is None
    assert qb["lineup_slot"] == "QB"


def test_starting_slot_counts_excludes_bench_ir_and_zero():
    assert starting_slot_counts(SETTINGS) == {0: 1, 2: 1, 4: 1, 6: 1, 23: 1}


def test_optimal_lineup():
    roster = parse_roster(TEAM, week=WEEK, season=SEASON)
    res = optimal_lineup(roster, starting_slot_counts(SETTINGS))
    by_slot = {s["slot"]: s["player"] for s in res["lineup"]}
    assert by_slot == {"QB": "QB Uno", "RB": "RB Bueno", "WR": "WR Uno", "TE": "TE Uno", "FLEX": "WR Dos"}
    assert res["changes"] == {"start": ["RB Bueno"], "bench": ["RB Malo"]}
    assert res["projected_total_current"] == 57
    assert res["projected_total_optimal"] == 66


def test_parse_standings_and_names():
    rows = parse_standings({"teams": [TEAM, OTHER], "members": [{"id": "{ABC-123}", "displayName": "hector"}]})
    assert [r["name"] for r in rows] == ["Club Rival", "Los Toros"]
    assert rows[1]["owners"] == ["hector"] and rows[1]["points_for"] == 300.46 and rows[1]["streak"] == "W2"


def test_parse_matchups_filters_period():
    data = {"teams": [TEAM, OTHER], "schedule": [
        {"matchupPeriodId": 2, "home": {"teamId": 1, "totalPoints": 100}, "away": {"teamId": 2, "totalPoints": 90}, "winner": "HOME"},
        {"matchupPeriodId": 3, "home": {"teamId": 2, "totalPoints": 0, "totalPointsLive": 12.345, "totalProjectedPointsLive": 110},
         "away": {"teamId": 1, "totalPoints": 0}, "winner": "UNDECIDED"},
    ]}
    [m] = parse_matchups(data, 3)
    assert m["home"] == {"team_id": 2, "name": "Club Rival", "points": 12.35, "projected_points": 110}
    assert m["away"]["name"] == "Los Toros"


def make_client(handler, **cfg):
    config = Config(league_id=123, season=SEASON, **cfg)
    c = EspnClient(config)
    c.http = httpx.Client(transport=httpx.MockTransport(handler), cookies=c.http.cookies)
    return c


def test_request_sends_views_cookies_and_filter():
    seen = {}

    def handler(req):
        seen["url"] = req.url
        seen["cookie"] = req.headers.get("cookie")
        seen["filter"] = req.headers.get("x-fantasy-filter")
        return httpx.Response(200, json={"players": []})

    c = make_client(handler, espn_s2="S2TOKEN", swid="ABC-123")
    c.free_agents(WEEK, 2, 10, ["FREEAGENT"])
    assert seen["url"].path == f"/apis/v3/games/ffl/seasons/{SEASON}/segments/0/leagues/123"
    assert seen["url"].params.get_list("view") == ["kona_player_info"]
    assert "espn_s2=S2TOKEN" in seen["cookie"] and "SWID={ABC-123}" in seen["cookie"]
    f = json.loads(seen["filter"])["players"]
    assert f["filterSlotIds"] == {"value": [2]} and f["limit"] == 10


def test_private_league_error():
    c = make_client(lambda req: httpx.Response(401))
    with pytest.raises(EspnError, match="ESPN_S2"):
        c.league("mTeam")


def test_resolve_my_team_by_swid():
    c = make_client(lambda req: httpx.Response(200), swid="{abc-123}")
    data = {"teams": [OTHER, TEAM]}
    assert c.resolve_team(data, None)["id"] == 1
    assert c.resolve_team(data, "rival")["id"] == 2
    assert c.resolve_team(data, "2")["id"] == 2
    with pytest.raises(EspnError):
        c.resolve_team(data, "nadie")


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("ESPN_LEAGUE_ID", "999")
    monkeypatch.setenv("ESPN_SEASON", "2025")
    monkeypatch.delenv("ESPN_TEAM_ID", raising=False)
    cfg = Config.from_env()
    assert cfg.league_id == 999 and cfg.season == 2025 and cfg.team_id is None
    monkeypatch.setenv("ESPN_LEAGUE_ID", "")
    with pytest.raises(EspnError):
        Config.from_env()


def test_optimal_lineup_assignment():
    roster = parse_roster(TEAM, week=WEEK, season=SEASON)
    res = optimal_lineup(roster, starting_slot_counts(SETTINGS))
    assert res["_assignment"] == {10: QB, 21: RB, 30: WR, 40: TE, 31: FLEX}


def test_plan_lineup_swaps_displaced_player():
    roster = parse_roster(TEAM, week=WEEK, season=SEASON)
    items = plan_lineup(roster, {21: RB}, starting_slot_counts(SETTINGS))
    assert sorted((i["playerId"], i["fromLineupSlotId"], i["toLineupSlotId"]) for i in items) == [
        (20, RB, BE), (21, BE, RB),
    ]
    assert all(i["type"] == "LINEUP" for i in items)


def test_plan_lineup_swaps_into_vacated_starting_slot():
    roster = parse_roster(TEAM, week=WEEK, season=SEASON)
    # WR Dos (FLEX) pasa a WR: WR Uno ocupa el FLEX que queda libre
    items = plan_lineup(roster, {31: WR}, starting_slot_counts(SETTINGS))
    assert sorted((i["playerId"], i["toLineupSlotId"]) for i in items) == [(30, FLEX), (31, WR)]


def test_plan_lineup_validates():
    roster = parse_roster(TEAM, week=WEEK, season=SEASON)
    counts = starting_slot_counts(SETTINGS)
    with pytest.raises(EspnError, match="no puede jugar"):
        plan_lineup(roster, {10: RB}, counts)
    with pytest.raises(EspnError, match="no tiene hueco"):
        plan_lineup(roster, {21: 3}, counts)
    assert plan_lineup(roster, {10: QB}, counts) == []


def test_submit_transaction_payload():
    seen = {}

    def handler(req):
        seen["url"] = req.url
        seen["method"] = req.method
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"id": "tx"})

    c = make_client(handler, espn_s2="S2", swid="ABC-123")
    items = [{"playerId": 1, "type": "ADD", "toTeamId": 2}]
    assert c.submit_transaction(2, WEEK, "FREEAGENT", items) == {"id": "tx"}
    assert seen["method"] == "POST" and seen["url"].host == "lm-api-writes.fantasy.espn.com"
    assert seen["url"].path == f"/apis/v3/games/ffl/seasons/{SEASON}/segments/0/leagues/123/transactions/"
    assert seen["body"] == {
        "isLeagueManager": False, "teamId": 2, "type": "FREEAGENT", "memberId": "{ABC-123}",
        "scoringPeriodId": WEEK, "executionType": "EXECUTE", "items": items,
    }


def test_submit_transaction_extra_fields():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={})

    c = make_client(handler, espn_s2="S2", swid="ABC-123")
    items = [{"playerId": 1, "type": "TRADE", "fromTeamId": 1, "toTeamId": 2}]
    c.submit_transaction(1, WEEK, "TRADE_PROPOSAL", items, extra={"comment": "hola", "expirationDate": 5})
    assert seen["body"]["type"] == "TRADE_PROPOSAL"
    assert seen["body"]["comment"] == "hola" and seen["body"]["expirationDate"] == 5


def test_submit_transaction_errors():
    c = make_client(lambda req: httpx.Response(200))
    with pytest.raises(EspnError, match="ESPN_S2"):
        c.submit_transaction(1, WEEK, "ROSTER", [])
    c = make_client(lambda req: httpx.Response(409, json={"details": [{"message": "Player is locked"}]}),
                    espn_s2="S2", swid="X")
    with pytest.raises(EspnError, match="Player is locked"):
        c.submit_transaction(1, WEEK, "ROSTER", [])


def test_load_env_file_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comentario\nESPN_LEAGUE_ID=111\nESPN_S2='abc'\nESPN_TEAM_ID=7\n")
    monkeypatch.setenv("ESPN_LEAGUE_ID", "")
    monkeypatch.delenv("ESPN_S2", raising=False)
    monkeypatch.setenv("ESPN_TEAM_ID", "3")
    load_env_file(env)
    import os
    assert os.environ["ESPN_LEAGUE_ID"] == "111" and os.environ["ESPN_S2"] == "abc"
    assert os.environ["ESPN_TEAM_ID"] == "3"
    load_env_file(tmp_path / "no-existe")
