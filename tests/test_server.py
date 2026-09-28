import asyncio

from espn_fantasy_mcp.server import mcp


def test_tools_registered():
    tools = {t.name for t in asyncio.run(mcp.list_tools())}
    assert tools == {
        "get_league_info", "get_standings", "get_roster", "get_matchups",
        "suggest_lineup", "get_free_agents", "find_player", "get_recent_transactions",
        "set_lineup", "add_drop", "propose_trade",
    }


def test_free_agents_sort_by_recent(monkeypatch):
    from espn_fantasy_mcp import server

    def fa(pid, owned, recent):
        stats = [{"scoringPeriodId": 2, "statSourceId": 0, "statSplitTypeId": 1, "seasonId": 2026, "appliedTotal": recent}]
        return {"player": {"id": pid, "fullName": f"P{pid}", "ownership": {"percentOwned": owned}, "stats": stats},
                "status": "FREEAGENT"}

    class Fake:
        config = type("C", (), {"season": 2026})()
        def current_week(self): return 3
        def free_agents(self, week, slot, limit, statuses):
            self.limit = limit
            return [fa(1, 60, 3.0), fa(2, 5, 22.0), fa(3, 30, 11.0)]

    fake = Fake()
    monkeypatch.setattr(server, "client", lambda: fake)
    res = server.get_free_agents(position="WR", limit=2, sort_by="recent")
    assert [p["name"] for p in res["players"]] == ["P2", "P3"] and fake.limit == 250
    assert [p["name"] for p in server.get_free_agents(limit=3)["players"]] == ["P1", "P2", "P3"]
