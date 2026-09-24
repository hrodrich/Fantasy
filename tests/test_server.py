import asyncio

from espn_fantasy_mcp.server import mcp


def test_tools_registered():
    tools = {t.name for t in asyncio.run(mcp.list_tools())}
    assert tools == {
        "get_league_info", "get_standings", "get_roster", "get_matchups",
        "suggest_lineup", "get_free_agents", "find_player", "get_recent_transactions",
        "set_lineup", "add_drop",
    }
