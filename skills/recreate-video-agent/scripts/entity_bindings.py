"""Blueprint 7 identity bindings; structural checks only, no media/quality calls."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def roster(items: Any, key: str) -> dict:
    if not isinstance(items, list):
        raise ValueError(f"{key}清单必须是数组")
    result = {}
    for item in items:
        identifier = item.get(key) if isinstance(item, dict) else None
        if not isinstance(identifier, str) or not identifier.strip() or identifier in result:
            raise ValueError(f"{key}缺失或重复")
        result[identifier] = item
    return result


def ids(values: Any, known: dict, label: str) -> set:
    if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
        raise ValueError(f"{label}必须是ID数组")
    if len(values) != len(set(values)) or not set(values) <= set(known):
        raise ValueError(f"{label}重复或引用不存在的ID")
    return set(values)


def validate_blueprint(bp: dict, duration: float | None = None) -> None:
    if bp.get("schemaVersion") != "7.0":
        raise ValueError("人物绑定协议要求videoBlueprint.schemaVersion=7.0")
    elements = bp.get("视频元素", {})
    people = roster(elements.get("人物"), "characterId")
    products = roster(elements.get("产品"), "productId")
    voices = roster(bp.get("声音结构", {}).get("voiceProfiles"), "speakerId")
    for voice in voices.values():
        if voice.get("characterId") is not None and voice["characterId"] not in people:
            raise ValueError("声源关联了不存在的characterId")
    shots = bp.get("逐镜头拆解")
    shot_map = roster(shots, "shotId")
    if not shots:
        raise ValueError("缺少逐镜头拆解")
    cursor = 0.0
    utterances = set()
    seen_people, seen_products = set(), set()
    for shot in shots:
        a, b = shot.get("start"), shot.get("end")
        if not number(a) or not number(b) or b <= a or abs(a - cursor) > .05:
            raise ValueError("镜头时间无效或存在空档/重叠")
        cursor = b
        seen_people |= ids(shot.get("visibleCharacterIds"), people, "visibleCharacterIds")
        seen_products |= ids(shot.get("productIds"), products, "productIds")
        frames = shot.get("keyframes")
        if not isinstance(frames, list) or not frames:
            raise ValueError("镜头缺少keyframes")
        for frame in frames:
            if not number(frame.get("time")) or not a <= frame["time"] < b:
                raise ValueError("keyframe时间越界")
        lines = shot.get("声音", {}).get("utterances")
        if not isinstance(lines, list):
            raise ValueError("utterances必须是数组")
        for line in lines:
            uid, speaker = line.get("utteranceId"), line.get("speakerId")
            if not isinstance(uid, str) or not uid or uid in utterances or speaker not in voices:
                raise ValueError("utteranceId重复/缺失或speakerId不存在")
            utterances.add(uid)
            x, y = line.get("start"), line.get("end")
            if not number(x) or not number(y) or not a <= x < y <= b:
                raise ValueError("对白时间越界")
    if duration is not None and (not number(duration) or abs(cursor - duration) > .1):
        raise ValueError("蓝图未覆盖目标时长")
    if seen_people != set(people) or seen_products != set(products):
        raise ValueError("人物/产品清单与镜头引用不一致")
    for cid, person in people.items():
        t = person.get("referenceTime")
        if not number(t) or not any(s["start"] <= t < s["end"] and cid in s["visibleCharacterIds"] for s in shots):
            raise ValueError("人物referenceTime必须落在其出镜镜头内")
    cuts = bp.get("节奏结构", {}).get("cutPlan")
    if not isinstance(cuts, list) or len(cuts) != len(shots) - 1:
        raise ValueError("cutPlan未覆盖相邻镜头")
    for cut, before, after in zip(cuts, shots, shots[1:]):
        if (not number(cut.get("time")) or abs(cut["time"] - after["start"]) > .05
                or cut.get("fromShotId") != before["shotId"] or cut.get("toShotId") != after["shotId"]):
            raise ValueError("cutPlan边界引用错误")


def load_blueprint(manifest: dict) -> dict | None:
    record = manifest.get("videoBlueprint", {})
    file = record.get("file") if isinstance(record, dict) else None
    if not file:
        return None
    bp = json.loads(Path(file).read_text(encoding="utf-8"))["videoBlueprint"]
    if bp.get("schemaVersion") in {None, "6.0"}:
        return None
    validate_blueprint(bp)
    return bp


def validate_bindings(manifest: dict, bp: dict) -> list:
    values = manifest.get("replacementBindings")
    if not isinstance(values, list):
        raise ValueError("先用set-replacement-bindings登记来源人物/产品映射")
    known = {
        "character": roster(bp["视频元素"]["人物"], "characterId"),
        "product": roster(bp["视频元素"]["产品"], "productId"),
    }
    seen, target_people = set(), {}
    registered = manifest.get("product", {}).get("productImages", [])
    files = {str(Path(x if isinstance(x, str) else x.get("file", x.get("filePath", ""))).expanduser().resolve()) for x in registered}
    for value in values:
        kind, source = value.get("entityType"), value.get("sourceId")
        target, mode = value.get("targetId"), value.get("mode")
        if kind not in known or source not in known[kind] or (kind, source) in seen:
            raise ValueError("replacementBindings来源ID不存在或重复")
        seen.add((kind, source))
        if mode not in {"preserve", "replace"} or not isinstance(target, str) or not target.strip():
            raise ValueError("replacementBindings缺少有效mode/targetId")
        if mode == "preserve" and target != source:
            raise ValueError("preserve必须沿用来源ID")
        if kind == "character":
            previous = target_people.get(target)
            if previous and not (previous.get("mergeAuthorized") is True and value.get("mergeAuthorized") is True):
                raise ValueError("不同来源人物不得未经授权合并为同一目标身份")
            target_people[target] = value
        elif mode == "replace":
            refs = value.get("referenceImages")
            if manifest.get("product", {}).get("useBenchmarkProduct") is not False or not isinstance(refs, list) or not refs:
                raise ValueError("替换产品须先登记独立产品参考图")
            for ref in refs:
                path = Path(ref).expanduser().resolve()
                if str(path) not in files or not path.is_file() or not path.stat().st_size:
                    raise ValueError("替换产品引用未登记或文件为空")
    expected = {(kind, source) for kind, entries in known.items() for source in entries}
    if seen != expected:
        raise ValueError("replacementBindings必须覆盖全部来源人物和产品")
    return values


def segment_context(manifest: dict, segment: dict) -> dict | None:
    bp = load_blueprint(manifest)
    if bp is None:
        return None
    bindings = validate_bindings(manifest, bp)
    windows = manifest.get("benchmarkVideo", {}).get("analysis", {}).get("recommendedSegments", [])
    window = next((x for x in windows if int(x["segmentId"]) == int(segment["segmentId"])), segment)
    a, b = window.get("globalStart"), window.get("globalEnd")
    if not number(a) or not number(b) or a >= b:
        raise ValueError("Segment缺少有效时间窗")
    shots = [s for s in bp["逐镜头拆解"] if s["start"] < b and s["end"] > a]
    characters = {x for s in shots for x in s["visibleCharacterIds"]}
    products = {x for s in shots for x in s["productIds"]}
    selected = [x for x in bindings if x["sourceId"] in (characters if x["entityType"] == "character" else products)]
    return {
        "shots": shots, "sourceCharacterIds": sorted(characters), "sourceProductIds": sorted(products),
        "characters": [x for x in bp["视频元素"]["人物"] if x["characterId"] in characters],
        "products": [x for x in bp["视频元素"]["产品"] if x["productId"] in products],
        "voiceProfiles": bp["声音结构"]["voiceProfiles"],
        "creatorIds": sorted({x["targetId"] for x in selected if x["entityType"] == "character"}),
        "replacementBindings": selected,
        "productReferenceImages": sorted({str(Path(p).expanduser().resolve()) for x in selected if x["entityType"] == "product" and x["mode"] == "replace" for p in x["referenceImages"]}),
        "productPresent": bool(products),
    }
