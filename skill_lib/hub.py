"""Plugin/app boundary. Hermes' raw field names live here only — iOS sees canonical names.

Translations: `trust` → `trustLevel`, `skill_md_preview` → `skillMdPreview`.
A Hermes upstream rename is a one-line patch in the `_translate_*` helpers.
"""

from __future__ import annotations

from typing import Any, Optional


def _import_hub():
    try:
        from hermes_cli.skills_hub import browse_skills, inspect_skill
        return browse_skills, inspect_skill
    except ImportError:
        return None, None


def _translate_browse_item(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"name": "", "description": "", "source": "", "trustLevel": "community", "tags": []}
    tags_raw = raw.get("tags", [])
    if not isinstance(tags_raw, list):
        tags_raw = []
    return {
        "name": str(raw.get("name", "")),
        "description": str(raw.get("description", "")),
        "source": str(raw.get("source", "")),
        "trustLevel": str(raw.get("trust", "community")),
        "tags": [str(t) for t in tags_raw if isinstance(t, (str, int))],
    }


def _translate_inspect_skill(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    tags_raw = raw.get("tags", [])
    if not isinstance(tags_raw, list):
        tags_raw = []
    out: dict[str, Any] = {
        "name": str(raw.get("name", "")),
        "description": str(raw.get("description", "")),
        "source": str(raw.get("source", "")),
        "trustLevel": str(raw.get("trust", "community")),
        "identifier": str(raw.get("identifier", "")),
        "tags": [str(t) for t in tags_raw if isinstance(t, (str, int))],
    }
    preview = raw.get("skill_md_preview")
    if isinstance(preview, str) and preview:
        out["skillMdPreview"] = preview
    return out


def _matches_query(item: dict[str, Any], q_lower: str) -> bool:
    """Case-insensitive substring match across name + description + tags.

    The exact same predicate the iOS Local view uses for client-side
    filtering, lifted into the plugin so search behaves identically
    across Local / Marketplace.
    """
    if (item.get("name") or "").lower().find(q_lower) >= 0:
        return True
    if (item.get("description") or "").lower().find(q_lower) >= 0:
        return True
    for tag in item.get("tags") or []:
        if isinstance(tag, str) and tag.lower().find(q_lower) >= 0:
            return True
    return False


# Cap the number of upstream pages we fetch when a query is active.
# Hermes' `browse_skills` is paginated server-side and may hit network
# per registry; pulling 10 pages × 100 = 1000 skills is plenty for any
# realistic query and keeps the rate-limit footprint bounded.
# Upstream's own disk index cache (1h TTL) makes repeats free.
_MAX_AGGREGATE_PAGES = 10


def browse(
    plugin_version: str,
    page: int = 1,
    page_size: int = 100,
    source: str = "all",
    query: str = "",
) -> dict[str, Any]:
    browse_skills, _ = _import_hub()
    if browse_skills is None:
        return {
            "plugin_version": plugin_version,
            "items": [],
            "page": 1,
            "total_pages": 1,
            "total": 0,
            "error": "hermes_unavailable",
        }

    page = max(1, min(int(page), 1000))
    page_size = max(1, min(int(page_size), 100))
    if not isinstance(source, str) or len(source) > 32:
        source = "all"
    if not isinstance(query, str):
        query = ""
    query = query.strip()[:128]  # length clamp; argparse already capped via shellQuote

    # Query-less path: defer to upstream pagination unchanged. This is
    # the hot path (every cold marketplace open) so we keep it cheap.
    if not query:
        try:
            result = browse_skills(page=page, page_size=page_size, source=source)
        except Exception as e:
            return {
                "plugin_version": plugin_version,
                "items": [],
                "page": page,
                "total_pages": 1,
                "total": 0,
                "error": type(e).__name__,
            }

        if not isinstance(result, dict):
            return {
                "plugin_version": plugin_version,
                "items": [],
                "page": page,
                "total_pages": 1,
                "total": 0,
                "error": "unexpected_shape",
            }

        raw_items = result.get("items", [])
        if not isinstance(raw_items, list):
            raw_items = []
        return {
            "plugin_version": plugin_version,
            "items": [_translate_browse_item(it) for it in raw_items],
            "page": result.get("page", page),
            "total_pages": result.get("total_pages", 1),
            "total": result.get("total", 0),
        }

    # Query path: Hermes' upstream `browse_skills` doesn't accept a
    # free-text filter, so we fetch up to `_MAX_AGGREGATE_PAGES` pages,
    # post-filter by substring match, then paginate the filtered set.
    # This is intentionally bounded — a search hit beyond page 10 of
    # the federated catalog won't surface; users narrow further or
    # switch the registry-source filter to find rarer skills.
    q_lower = query.lower()
    aggregated: list[dict[str, Any]] = []
    upstream_total_pages = 1
    for upstream_page in range(1, _MAX_AGGREGATE_PAGES + 1):
        try:
            result = browse_skills(page=upstream_page, page_size=100, source=source)
        except Exception as e:
            return {
                "plugin_version": plugin_version,
                "items": [],
                "page": page,
                "total_pages": 1,
                "total": 0,
                "error": type(e).__name__,
            }
        if not isinstance(result, dict):
            return {
                "plugin_version": plugin_version,
                "items": [],
                "page": page,
                "total_pages": 1,
                "total": 0,
                "error": "unexpected_shape",
            }
        raw_items = result.get("items", [])
        if not isinstance(raw_items, list):
            raw_items = []
        aggregated.extend(_translate_browse_item(it) for it in raw_items)
        upstream_total_pages = result.get("total_pages", upstream_page)
        # Stop early when we've drained the upstream catalog — no point
        # asking for empty pages we already know don't exist.
        if upstream_page >= upstream_total_pages:
            break

    filtered = [it for it in aggregated if _matches_query(it, q_lower)]
    total = len(filtered)
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
    start = (page - 1) * page_size
    window = filtered[start : start + page_size]

    return {
        "plugin_version": plugin_version,
        "items": window,
        "page": page,
        "total_pages": total_pages,
        "total": total,
    }


def inspect(plugin_version: str, name: str) -> dict[str, Any]:
    _, inspect_skill = _import_hub()
    if inspect_skill is None:
        return {
            "plugin_version": plugin_version,
            "skill": None,
            "error": "hermes_unavailable",
        }

    if not isinstance(name, str) or not name:
        return {
            "plugin_version": plugin_version,
            "skill": None,
            "error": "invalid_name",
        }

    try:
        result = inspect_skill(name)
    except Exception as e:
        return {
            "plugin_version": plugin_version,
            "skill": None,
            "error": type(e).__name__,
        }

    if result is None:
        return {"plugin_version": plugin_version, "skill": None}

    if not isinstance(result, dict):
        return {
            "plugin_version": plugin_version,
            "skill": None,
            "error": "unexpected_shape",
        }

    return {"plugin_version": plugin_version, "skill": _translate_inspect_skill(result)}
