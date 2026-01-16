from __future__ import annotations

from .models import ProjectInput


def build_query_pack(p: ProjectInput) -> list[str]:
    """
    Query pack tuned for stalled-project discovery.
    RERA ID is optional. If present, include RERA-specific queries.
    """
    name = p.project_name.strip()
    city = p.city.strip()
    rera = (p.rera_id or "").strip()

    queries: list[str] = []

    # Core identity queries (always)
    queries += [
        f'"{name}" "{city}"',
        f'"{name}" "{city}" rera',
        f'"{name}" "{city}" possession',
        f'"{name}" "{city}" construction update',
        f'"{name}" delayed update',
        f'"{name}" stalled project',
        f'"{name}" "{city}" complaint',
        f'"{name}" "{city}" litigation',
    ]

    # RERA-specific (only if rera_id present)
    if rera:
        queries = [
            f'"{name}" "{city}" "{rera}"',
            f'"{rera}" project status',
            f'"{rera}" extension',
            f'"{rera}" rera order',
        ] + queries

    # De-dupe while preserving order
    seen = set()
    out = []
    for q in queries:
        qn = " ".join(q.split())
        key = qn.lower()
        if key not in seen:
            seen.add(key)
            out.append(qn)
    return out
