from __future__ import annotations

from .models import ProjectInput


def build_query_pack(p: ProjectInput) -> list[str]:
    """
    Query pack tuned for stalled-project discovery.
    RERA ID is optional. If present, include RERA-specific queries.
    Also includes site-restricted queries for known official sources when city suggests it.
    """
    name = p.project_name.strip()
    city = p.city.strip()
    city_l = city.lower()
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
            f'"{rera}" registration',
        ] + queries

        # Heuristic: Haryana RERA for Gurgaon/Gurugram/Haryana-related cities
        if any(k in city_l for k in ["gurgaon", "gurugram", "haryana", "faridabad", "panipat", "sonipat", "rohtak"]):
            queries = [
                f'site:haryanarera.gov.in "{rera}"',
                f'site:haryanarera.gov.in "{name}"',
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
