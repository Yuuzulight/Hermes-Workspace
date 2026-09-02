# Hermes plugin contract (verified against local hermes-agent source)

Source: `~/.hermes/hermes-agent/`. Loader: `hermes_cli/plugins.py`. Dashboard mount:
`hermes_cli/web_server.py`. Desktop: `apps/desktop/src/contrib/runtime-loader.ts`.
Docs: `website/docs/developer-guide/plugins/index.md`, `.../desktop-plugin-sdk.md`.

## Three independent halves

| Half | Discovered by | Needs |
|---|---|---|
| Agent (Python tools/hooks) | `<plugins root>/<name>/plugin.yaml` + `__init__.py` | both files, `register(ctx)` in `__init__.py` |
| Dashboard (FastAPI) | `<plugins root>/<name>/dashboard/manifest.json` | `manifest.json` + `plugin_api.py` exporting module-level `router = APIRouter()` |
| Desktop (renderer) | `~/.hermes/plugins/<id>/desktop/plugin.js` (or `~/.hermes/desktop-plugins/<id>/plugin.js`) | ESM file, `export default { id, name, register(ctx) }` |

A dashboard+desktop-only plugin needs **no** `plugin.yaml` / `__init__.py` (confirmed by
bundled `plugins/kanban/`, `plugins/hermes-achievements/` — dashboard/ only).

## plugin.yaml (agent half)

- Only required key: `name` (falls back to dir name).
- `kind:` ∈ `{standalone, backend, exclusive, platform, model-provider}`, default `standalone`.
  **No `user`/`system` kind.** Unknown → coerced to `standalone` + warning.
- Other optional keys: `version`, `description`, `author`, `requires_env`, `provides_tools`,
  `provides_hooks`, `key`, `capabilities`, plus v2: `manifest_version`, `api_version`,
  `requires_plugins`, `python_dependencies`, `config_schema`, `license`, `homepage`, `tags`.
- `provides_tools` for `kind: standalone` = **documentation only** (introspection listing).
  Tools register because `register(ctx)` calls `ctx.register_tool`. It is an opt-in trigger
  only for `kind: platform` deferred plugins.

## Agent-side loading

- `__init__.py` loaded as package `hermes_plugins.<slug>` where `slug = key.replace("/","__").replace("-","_")`.
  Dir `hermes-workspace` → module `hermes_plugins.hermes_workspace`.
  `module.__path__` / `__package__` set → **`from . import cr_store` works.**
- Entry: module-level `def register(ctx): ...`, one positional arg. Call site
  `plugins.py:5282`: `register_fn(PluginContext(manifest, manager))`.
- `ctx.register_tool(name, toolset, schema, handler, check_fn=None, requires_env=None,
  is_async=False, description="", emoji="", override=False)`. Arg 2 is **`toolset`**, not "group".
  Docs example: `ctx.register_tool(name="calculate", toolset="calculator", schema=..., handler=...)`.
- Other `ctx`: `register_hook`, `register_command`, `register_cli_command`,
  `register_middleware(kind, cb)`, `dispatch_tool`, `get_config`/`set_config`, `ctx.state`
  (JSON KV under `plugin-data/<ns>/state.json`), `has_capability`, `has_plugin`, `call_mcp`, `ctx.llm`.

## Plugin storage

```python
from plugins.plugin_storage import plugin_data_dir, plugin_db
plugin_data_dir(name: str) -> Path          # <hermes home>/plugin-data/<name>/  (mkdir'd, profile-aware)
plugin_db(name, filename="data.db") -> sqlite3.Connection   # WAL, foreign_keys=ON
```
`name` regex `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`, no `..`. Resolves `get_hermes_home()` every
call — never cache the Path. `plugin_db` rejects nested/pathy filenames — open sqlite directly
for a custom filename. Never write runtime state into the install tree (`hermes plugins update`
git-pulls it).

## Dashboard

- `manifest.json`: `name` (defaults to dir), `label`, `description`, `icon` (default `"Puzzle"`),
  `version`, `tab: {path, position, override?, hidden?}`, `entry` (default `dist/index.js`),
  `css`, `api` (path to the FastAPI module, must stay inside `dashboard/`), `slots: [str]`.
- `plugin_api.py` imported as `hermes_dashboard_plugin_<name>`; must expose module-level
  `router` (FastAPI `APIRouter`). Mounted at **`/api/plugins/<manifest name>/`**.
- Backend Python imported only for `bundled` + `user` sources (never `project`). User source
  also gated: must be in `plugins.enabled`, not in `plugins.disabled`. Per-request runtime
  gate re-checks every `/api/plugins/...` call.

## Desktop (`desktop/plugin.js`)

- **Import whitelist (hard):** `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`,
  `react/jsx-dev-runtime`. Any other bare specifier → load error. Relative/URL imports pass through.
- Size cap **16 MiB** (new IPC) / **512 KiB** (old fallback, throws rather than eval partial).
- `export default { id, name, register(ctx) }`. Desktop `ctx` ≠ Python `ctx`:
  `ctx.register({id, area, order, render|data})`, `ctx.registerMany([...])`,
  `ctx.rest('/path', {method, body})` / `ctx.socket(...)` scoped to `/api/plugins/<id>/`.
- Evaluated in the renderer realm with full app authority — error isolation only, **not** a
  sandbox.
- Hot reload: `plugin.js` fs-watched (debounced) + 5s visible-tab poll fallback. Re-load
  disposes prior registrations.
- `@hermes/plugin-sdk` exports: `host` (`host.state.*`, `host.request`), `atom`, `computed`,
  `useValue`, `useQuery`, `useMutation`, `useI18n`, `useTheme`, area constants
  (`STATUSBAR_AREAS`, `PALETTE_AREA`, `ROUTES_AREA`, `SIDEBAR_NAV_AREA`, `KEYBINDS_AREA`,
  `THEMES_AREA`, `TRANSCRIPT_DIRECTIVE_AREA`), UI (`Button`, `Input`, `Checkbox`, `Codicon`,
  `Badge`, `EmptyState`, `ErrorState`, `Loader`, `LogView`, `ConfirmDialog`, `StatusDot`, …),
  types (`HermesPlugin`, …), `Contribute`. React is the app singleton (a 2nd copy breaks hooks).

## Install layout

```
~/.hermes/plugins/<id>/          # flat; category nesting allowed ONE level (<cat>/<name>/)
├── plugin.yaml                  # agent half only
├── __init__.py                  # register(ctx)
├── dashboard/{manifest.json, plugin_api.py}
└── desktop/plugin.js
```
Discovery precedence: bundled `<repo>/plugins/` → user `~/.hermes/plugins/` →
project `./.hermes/plugins/` (needs `HERMES_ENABLE_PROJECT_PLUGINS=1`) → pip entry points.
Scanner depth capped at 2.

## config.yaml gates

- `plugins.disabled` = deny-list, wins over `enabled`.
- `plugins.enabled` = opt-in allow-list. `kind: standalone` + user `backend` + pip plugins
  load **only if listed**. Bundled `backend`/`platform` auto-load.
- Both the path key (`cat/name`) and bare `name` are accepted.
- Desktop half: independent Settings → Plugins toggle; unified `plugins/<id>/desktop/` ships
  `defaultEnabled: false`.
- Per-plugin: `plugins.entries.<key>.{allow_tool_override, granted_capabilities, settings}`.
