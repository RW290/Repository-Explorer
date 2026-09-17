"""Merge: attach mined PR annotations to the parsed graph nodes they touched."""

from app.miner import RawPR
from app.parser import ParsedNode


def merge(nodes: list[ParsedNode], extractions: list[dict], overview: str = "") -> dict:
    node_by_id = {n.id: n for n in nodes}
    node_ids = set(node_by_id)

    annotations = []
    node_annotation_ids: dict[str, list[tuple[str, str]]] = {}  # node_id -> [(date, ann_id)]
    counter = 1

    for ext in extractions:
        pr: RawPR = ext["pr"]
        touched_nodes = [f for f in pr.files if f in node_ids]
        if not touched_nodes:
            continue
        ann_id = f"ann_{counter:03d}"
        counter += 1
        annotations.append(
            {
                "id": ann_id,
                "node_id": touched_nodes[0],
                "source": "pr",
                "source_ref": f"PR #{pr.number}",
                "date": (pr.merged_at or "")[:10],
                "rationale_stated": ext["rationale_stated"],
                "rationale_inferred": ext["rationale_inferred"],
                "confidence": ext["confidence"],
                "diff_summary": ext["diff_summary"],
            }
        )
        for node_id in touched_nodes:
            node_annotation_ids.setdefault(node_id, []).append((pr.merged_at or "", ann_id))

    node_list = []
    for n in nodes:
        anns = sorted(node_annotation_ids.get(n.id, []), key=lambda t: t[0])
        node_list.append(
            {
                "id": n.id,
                "type": n.type,
                "parent": n.parent,
                "summary": n.summary,
                "dependencies": n.dependencies,
                "annotations": [ann_id for _, ann_id in anns],
            }
        )

    return {"nodes": node_list, "annotations": annotations, "overview": overview}
