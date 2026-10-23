"""Discover packaged skills (directories containing a SKILL.md with YAML front matter)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

SKILLS_ROOT = Path(__file__).resolve().parent


class SkillRegistry:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or SKILLS_ROOT)

    def _parse(self, path: Path) -> Dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        meta: Dict[str, Any] = {}
        body = text
        if text.startswith("---"):
            _, fm, body = text.split("---", 2)
            meta = yaml.safe_load(fm) or {}
        return {"skill_id": meta.get("name", path.parent.name), "description": meta.get("description", ""),
                "version": meta.get("version"), "path": str(path), "body": body.strip()}

    def list_skills(self) -> List[Dict[str, Any]]:
        return [{k: v for k, v in self._parse(p).items() if k != "body"} for p in sorted(self.root.glob("*/SKILL.md"))]

    def load_skill(self, skill_id: str) -> Dict[str, Any]:
        for p in sorted(self.root.glob("*/SKILL.md")):
            info = self._parse(p)
            if info["skill_id"] == skill_id:
                refs = sorted(str(r.relative_to(p.parent)) for r in (p.parent / "references").glob("*") if r.is_dir()) \
                    if (p.parent / "references").exists() else []
                return {"skill_id": skill_id, "version": info["version"], "description": info["description"],
                        "trust": "packaged_instructions", "instructions": info["body"], "reference_categories": refs}
        raise KeyError(f"unknown skill {skill_id}")
