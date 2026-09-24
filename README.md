# Fantasy — servidor MCP de ESPN Fantasy Football

Servidor [MCP](https://modelcontextprotocol.io) para que Claude consulte y analice tu liga de
ESPN Fantasy Football (NFL). Usa la API web (no oficial) de ESPN. Además de consultar, puede
cambiar la alineación y fichar/soltar jugadores de **tu** equipo (nunca de otros).

## Herramientas

| Herramienta | Qué hace |
|---|---|
| `get_league_info` | Nombre, semana actual, tipo de puntuación (PPR/Half/Standard), alineación, playoffs |
| `get_standings` | Clasificación: récord, PF/PA, racha, seed |
| `get_roster` | Plantilla de un equipo (el tuyo por defecto) con lesiones, puntos y proyecciones |
| `get_matchups` | Enfrentamientos de la jornada con marcador en vivo y proyección |
| `suggest_lineup` | Alineación óptima según las proyecciones de ESPN y qué cambiar |
| `get_free_agents` | Mejores agentes libres / waivers por posición |
| `find_player` | Dónde está un jugador (qué equipo o si está libre) |
| `get_recent_transactions` | Fichajes, waivers y traspasos de la semana |
| `set_lineup` ✏️ | Cambia tu alineación (movimientos concretos o la óptima de `suggest_lineup`) |
| `add_drop` ✏️ | Ficha un agente libre o reclama un waiver y, opcionalmente, suelta a un jugador |

Las herramientas ✏️ necesitan `ESPN_S2` y `ESPN_SWID` (aunque la liga sea pública). Sin
`confirm=true` solo devuelven una vista previa; Claude debe enseñártela y esperar tu permiso antes
de enviar el cambio. ESPN rechaza mover a jugadores cuyo partido ya ha empezado.

## Configuración

1. **ID de la liga**: abre tu liga en fantasy.espn.com; es el número de `leagueId=` en la URL.
2. **Cookies (solo ligas privadas)**: con la sesión iniciada en espn.com abre las herramientas de
   desarrollador del navegador → *Application/Almacenamiento* → *Cookies* → `https://www.espn.com`
   y copia los valores de `espn_s2` y `SWID`. Hacen falta para ligas privadas y para hacer cambios.
   Son credenciales de tu cuenta: guárdalas solo en variables de entorno o en `.env` (ignorado por
   git), nunca en archivos del repo.
3. Exporta las variables (ver `.env.example`):

   ```bash
   export ESPN_LEAGUE_ID=123456
   export ESPN_S2='AEB...'
   export ESPN_SWID='{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}'
   # opcional: ESPN_SEASON=2026, ESPN_TEAM_ID=3 (si no, se detecta tu equipo con el SWID)
   ```

## Uso

**Claude Code** (desde este directorio, con [uv](https://docs.astral.sh/uv/) instalado): el
archivo `.mcp.json` ya registra el servidor. Arranca `claude`, aprueba el servidor
`espn-fantasy` y pregunta, por ejemplo: *"¿Qué alineación me recomiendas esta semana?"* o
*"¿Qué RB libres merece la pena fichar?"*.

**Claude Desktop**: añade a `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "espn-fantasy": {
      "command": "uv",
      "args": ["run", "--directory", "/ruta/a/Fantasy", "espn-fantasy-mcp"],
      "env": { "ESPN_LEAGUE_ID": "123456", "ESPN_S2": "...", "ESPN_SWID": "{...}" }
    }
  }
}
```

**Claude Code en la web**: el entorno necesita acceso de red a `fantasy.espn.com`
(política de red *Full* o un dominio permitido) y las variables anteriores definidas en el
entorno.

## Desarrollo

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest
```
