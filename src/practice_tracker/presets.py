"""Load hobby presets (YAML) into an empty database."""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlmodel import Session, select

from .models import Area, Hobby, Item, Kind


def load_presets_if_empty(db: Session, presets_dir: Path) -> list[str]:
    if db.exec(select(Hobby)).first() is not None or not presets_dir.is_dir():
        return []
    loaded = []
    for path in sorted(presets_dir.glob("*.yaml")):
        load_preset(db, yaml.safe_load(path.read_text(encoding="utf-8")))
        loaded.append(path.stem)
    db.commit()
    return loaded


def load_preset(db: Session, spec: dict) -> Hobby:
    hobby = Hobby(name=spec["name"], color=spec.get("color", "#4f6bed"),
                  weekly_goal_min=spec.get("weekly_goal_min", 120))
    db.add(hobby)
    db.flush()

    kinds: dict[str, Kind] = {}
    for k in spec.get("kinds", []):
        kind = Kind(hobby_id=hobby.id, name=k["name"], item_fields=k.get("item_fields", []),
                    segment_fields=k.get("segment_fields", []), focus_options=k.get("focus_options", []),
                    review_enabled=k.get("review_enabled", False))
        db.add(kind)
        kinds[kind.name] = kind
    db.flush()

    areas: dict[str, Area] = {}
    for i, a in enumerate(spec.get("areas", [])):
        kind = kinds.get(a["kind"]) if a.get("kind") else None
        area = Area(hobby_id=hobby.id, name=a["name"], target_share=a.get("target", 0.0),
                    default_kind_id=kind.id if kind else None, is_warmup=a.get("warmup", False), sort=i)
        db.add(area)
        areas[area.name] = area
    db.flush()

    for it in spec.get("items", []):
        area = areas[it["area"]]
        kind = next((k for k in kinds.values() if k.id == area.default_kind_id), None)
        attrs = kind.check_item_attributes(it.get("attributes", {})) if kind else {}
        db.add(Item(area_id=area.id, kind_id=area.default_kind_id, title=it["title"],
                    attributes=attrs, status=it.get("status", "learning")))
    return hobby
