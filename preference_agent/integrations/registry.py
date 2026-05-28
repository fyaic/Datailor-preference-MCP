from __future__ import annotations

from .models import ClientName, ClientProfile


PROFILES: dict[ClientName, ClientProfile] = {
    "codex": ClientProfile(
        name="codex",
        display_name="Codex",
        agent_arg="codex",
        config_kind="toml",
        default_scope="user",
        supports_plugin=True,
        docs_url="https://developers.openai.com/codex/config-reference",
    ),
    "claude": ClientProfile(
        name="claude",
        display_name="Claude Code",
        agent_arg="claude",
        config_kind="claude-json",
        default_scope="project",
        supports_plugin=True,
        docs_url="https://code.claude.com/docs/en/mcp",
    ),
    "kimi": ClientProfile(
        name="kimi",
        display_name="Kimi Code",
        agent_arg="kimi",
        config_kind="json",
        default_scope="user",
        supports_plugin=True,
        docs_url="https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/data-locations.html",
    ),
}


def get_profile(name: str) -> ClientProfile:
    key = name.strip().casefold()
    if key not in PROFILES:
        raise ValueError(f"unsupported client: {name}")
    return PROFILES[key]  # type: ignore[index]


def expand_clients(client: str) -> list[ClientName]:
    key = client.strip().casefold()
    if key == "all":
        return ["codex", "claude", "kimi"]
    return [get_profile(key).name]
