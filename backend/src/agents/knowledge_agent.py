"""Knowledge Agent: searches the official documents (no LLM needed).

Returns cited passages with trust labels so the Response Agent knows which
text is binding (circular in force > current handbook > superseded > drafts).
"""

from src.services.knowledge_base import kb

TYPE_FILTER = {
    "lesson_doubt": ["lesson_notes"],
    "admissions_info": ["brochure", "handbook", "circular"],
}


class KnowledgeAgent:
    def run(self, query: str, intent: str, role: str, programme: str = "UG", k: int = 4) -> list[dict]:
        types = TYPE_FILTER.get(intent)
        if role == "visitor":
            types = [t for t in (types or ["brochure", "handbook", "circular"]) if t != "lesson_notes"] or ["brochure"]
        narrowed = kb.search(query, k=3, types=types, programme=programme)
        overall = kb.search(query, k=3, programme=programme) if types else []
        if role == "visitor":
            overall = [c for c in overall if c.type != "lesson_notes"]
        seen, hits = set(), []
        for c in narrowed + overall:  # blend: a wrong type guess never hides the answer
            if c.id not in seen:
                seen.add(c.id)
                hits.append(c)
        return [
            {
                "source": f"{c.title} v{c.version}, p.{c.page}" + (f", Rule {c.rule}" if c.rule else ""),
                "label": kb.label(c),
                "binding": not c.non_binding and c.status in ("current", "circular-in-force"),
                "text": c.text[:700],
            }
            for c in hits[:k]
        ]
