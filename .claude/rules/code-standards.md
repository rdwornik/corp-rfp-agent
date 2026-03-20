# Ecosystem Code Standards

- Python 3.11+, Windows-first (pathlib, `py -m`)
- pyproject.toml as single source of truth
- ruff for lint/format, pytest for testing
- Type hints everywhere, no bare except
- Logging not print (except CLI Rich output)
- Dataclasses or Pydantic for data, not raw dicts
- Config via YAML + .env, never hardcode paths
- Feature branches, never commit to main
- API keys via global Documents/.secrets/.env
- Comments explain WHY, not WHAT
- NEVER touch "OneDrive - Blue Yonder" paths
