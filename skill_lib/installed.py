"""Installed-skill enumeration. See SECURITY.md for invariants."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional


def _hermes_home() -> Path:
    raw = os.environ.get("HERMES_HOME", "")
    if raw:
        return Path(raw)
    return Path.home() / ".hermes"


def _profile_id_from_home(home: Path) -> Optional[str]:
    parts = home.resolve().parts
    if "profiles" in parts:
        i = parts.index("profiles")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


def _safe_load_yaml_disabled(config_path: Path) -> set[str]:
    if not config_path.exists():
        return set()
    try:
        import yaml
    except ImportError:
        return set()
    try:
        data = yaml.safe_load(config_path.read_text()) or {}
    except Exception:
        return set()
    skills_section = data.get("skills") or {}
    disabled = skills_section.get("disabled") or []
    if isinstance(disabled, list):
        return {str(x) for x in disabled if isinstance(x, str)}
    return set()


def _safe_load_lock(lock_path: Path) -> dict[str, str]:
    if not lock_path.exists():
        return {}
    try:
        data = json.loads(lock_path.read_text())
    except Exception:
        return {}
    installed = data.get("installed") or {}
    if not isinstance(installed, dict):
        return {}
    out: dict[str, str] = {}
    for name, entry in installed.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        source = entry.get("source")
        if isinstance(source, str) and source:
            out[name] = source
    return out


def _safe_scan_descriptions() -> dict[str, str]:
    try:
        from agent.skill_commands import scan_skill_commands
    except ImportError:
        return {}
    try:
        catalog = scan_skill_commands()
    except Exception:
        return {}
    out: dict[str, str] = {}
    if isinstance(catalog, dict):
        for slash_name, entry in catalog.items():
            if not isinstance(slash_name, str):
                continue
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or slash_name.lstrip("/")
            description = entry.get("description") or ""
            if isinstance(name, str) and isinstance(description, str):
                out[name] = description
    return out


def _walk_skill_dirs(home_wide: Path, profile_dir: Optional[Path]) -> dict[str, dict]:
    """Enumerate `SKILL.md` files under the host-wide and per-profile
    skill roots. Mirrors the legacy iOS heredoc's
    `find -mindepth 2 -maxdepth 4 -name SKILL.md` exactly so the plugin
    is a drop-in replacement.

    Two scan strategies, tried in order, results merged:

      1. ``Path.rglob`` — fast and idiomatic.
      2. ``os.walk`` — symlink-following, depth-limited fallback.
         Catches the cases where rglob silently skips (symlinked skill
         dirs, filesystems where pathlib's stat gating differs, etc.)
         that the heredoc's ``find`` picked up.

    Per-profile entries shadow host-wide entries with the same name —
    matches `HermesAdapter.listSkills`'s contract.
    """
    out: dict[str, dict] = {}
    candidates: list[tuple[Path, str]] = []
    if home_wide.is_dir():
        candidates.append((home_wide, "host"))
    if profile_dir and profile_dir.is_dir():
        candidates.append((profile_dir, "profile"))

    def _record(skill_md: Path, root: Path, scope: str) -> None:
        try:
            rel = skill_md.relative_to(root)
        except ValueError:
            return
        # Heredoc's `-mindepth 2 -maxdepth 4` ⇒ rel.parts in 2..4.
        if len(rel.parts) < 2 or len(rel.parts) > 4:
            return
        skill_dir = skill_md.parent
        name = skill_dir.name
        if not name or name.startswith("."):
            return
        if scope == "profile" or name not in out:
            out[name] = {"path": str(skill_dir), "scope": scope}

    for root, scope in candidates:
        # Strategy 1: pathlib rglob.
        try:
            for skill_md in root.rglob("SKILL.md"):
                _record(skill_md, root, scope)
        except OSError:
            pass

        # Strategy 2: os.walk fallback. Always runs, even when rglob
        # succeeded — merges into the same dict, so duplicates are a
        # no-op and missed-by-rglob skills are recovered.
        try:
            for dirpath, dirnames, filenames in os.walk(
                str(root), followlinks=True
            ):
                # Don't descend past depth 4 (skill_dir + SKILL.md = 4 parts).
                rel_dir = Path(dirpath).relative_to(root)
                depth = 0 if str(rel_dir) == "." else len(rel_dir.parts)
                if depth >= 4:
                    dirnames[:] = []
                # Prune hidden subdirs (`.hub/`, `.pytest_cache/`, …).
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                if "SKILL.md" in filenames:
                    _record(Path(dirpath) / "SKILL.md", root, scope)
        except OSError:
            continue
    return out


def collect_installed(plugin_version: str) -> dict[str, Any]:
    home = _hermes_home()
    profile_id = _profile_id_from_home(home)

    host_skills = Path.home() / ".hermes" / "skills"
    profile_skills = (
        Path.home() / ".hermes" / "profiles" / profile_id / "skills"
        if profile_id
        else None
    )
    by_name = _walk_skill_dirs(host_skills, profile_skills)

    config_path = Path.home() / ".hermes" / "config.yaml"
    disabled = _safe_load_yaml_disabled(config_path)

    lock_path = Path.home() / ".hermes" / "skills" / ".hub" / "lock.json"
    registry_sources = _safe_load_lock(lock_path)

    descriptions = _safe_scan_descriptions()

    skills_out: list[dict[str, Any]] = []
    for name in sorted(by_name.keys()):
        entry = by_name[name]
        is_disabled = name in disabled
        skills_out.append(
            {
                "name": name,
                "description": descriptions.get(name, "Hermes skill"),
                "scope": entry["scope"],
                "bundled": entry["scope"] == "host",
                "disabled": is_disabled,
                "hostDisabled": True if is_disabled else None,
                "locationPath": entry["path"],
                "registrySource": registry_sources.get(name),
            }
        )

    return {
        "plugin_version": plugin_version,
        "skills": skills_out,
        "count": len(skills_out),
    }
