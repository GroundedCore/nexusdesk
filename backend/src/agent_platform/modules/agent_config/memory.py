"""Phase 5 agent memory config resolution (C-3, C-5).

Pure, database-free helpers shared by the injection (worker), extraction
dispatch/execution, retrieval (platform_tools) and blacklist-handoff
(customer_service) paths. ``resolve_memory_config`` is the single source of
truth for how an agent's memory config combines with the platform-level
switches — the platform can only be tightened by the agent, never loosened
(G4 / FR-2).
"""

from dataclasses import dataclass

from agent_platform.modules.agent_config.service import AgentConfig
from agent_platform.settings import Settings

# Canonical order for the injectable/scribable types. ``flag`` is deliberately
# absent: it is a platform-governed marker and never an agent option (C-5).
MEMORY_TYPES = ("profile", "preference", "event")


@dataclass(frozen=True)
class EffectiveMemoryConfig:
    summary: bool
    read: bool
    write: bool
    types: frozenset
    search: bool
    write_mode: str
    inject_max_items: int
    search_limit: int
    blacklist_auto_handoff: bool


def resolve_memory_config(cfg: AgentConfig, s: Settings) -> EffectiveMemoryConfig:
    """Combine the agent config with the platform switches (C-3).

    Boolean gates are ANDed so a platform-off capability stays off regardless
    of the agent; numeric limits are ``min``-capped at the platform ceiling;
    the blacklist handoff is ORed because enabling it is the tightening
    direction.
    """
    return EffectiveMemoryConfig(
        summary=s.summary_enabled and cfg.summary_enabled,
        read=s.memory_inject_enabled and cfg.memory_enabled and cfg.memory_read_enabled,
        write=s.memory_extract_enabled and cfg.memory_enabled and cfg.memory_write,
        types=frozenset(cfg.memory_types),
        # memory_search is a read-side capability, so the platform read kill
        # switch gates it too (D1): platform-off means off for every agent
        # (AC-6 "platform closed => runtime fully closed").
        search=s.memory_inject_enabled
        and cfg.memory_enabled
        and cfg.memory_read_enabled
        and cfg.memory_search_enabled,
        write_mode=cfg.memory_write_mode,
        inject_max_items=min(
            cfg.memory_inject_max_items or s.memory_inject_max_items,
            s.memory_inject_max_items,
        ),
        # D2: settings.memory_search_limit is both the platform default and
        # the platform ceiling; the store's memory_search_limit_max stays as
        # a second-line clamp for direct calls.
        search_limit=min(cfg.memory_search_limit or s.memory_search_limit, s.memory_search_limit),
        blacklist_auto_handoff=s.memory_blacklist_auto_handoff
        or cfg.memory_blacklist_auto_handoff,
    )


def resolve_agent_memory(config, settings: Settings) -> EffectiveMemoryConfig:
    """Resolve the effective memory config from a stored agent config snapshot.

    ``config`` is the ``config`` dict inside a run's agent snapshot. It is the
    full :class:`AgentConfig` dump on every published run, but a legacy run
    may carry no snapshot (or a partial dict in unit tests); both fall back to
    the all-default config so pre-Phase-5 behaviour is preserved. ``system_prompt``
    is the only required field and is padded with a placeholder — the memory
    gates never read it.
    """
    cfg = AgentConfig.model_validate({"system_prompt": "x", **(config or {})})
    return resolve_memory_config(cfg, settings)