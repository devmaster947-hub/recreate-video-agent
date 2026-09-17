"""Object-region storyboard compositing using only stdlib and FFmpeg.

Polygons use normalized ORIGINAL cell visual coordinates (labels excluded).
Geometry checks are conservative gates, not semantic segmentation or identity QA.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

RATIOS = ((1, 1), (3, 4), (4, 3), (9, 16), (16, 9))
MAX_CANVAS_ASPECT_RATIO_DRIFT = 0.15


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def polygons(value, role):
    if not isinstance(value, list):
        raise ValueError(f"{role} 必须是多边形数组。")
    for poly in value:
        if not isinstance(poly, list) or len(poly) < 3:
            raise ValueError(f"{role} 每个多边形至少3点。")
        for point in poly:
            if not isinstance(point, list) or len(point) != 2 or any(
                isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or not 0 <= v <= 1
                for v in point
            ):
                raise ValueError(f"{role} 坐标必须为0～1的有限数字。")
        area = abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))) / 2
        if area < .00001:
            raise ValueError(f"{role} 多边形面积为零或过小。")
    return value


def validate_regions(plan):
    mode = plan.get('compositionMode')
    if mode not in (None, 'region-lock-v1'):
        raise ValueError('未知compositionMode。')
    if mode and (not isinstance(plan.get('editRule'), str) or not plan['editRule'].strip()):
        raise ValueError('局部替换必须提供明确的editRule。')
    if plan.get("mergeMode") != "object-regions-v1":
        raise ValueError("新图片任务必须使用 mergeMode=object-regions-v1。")
    if plan.get("coordinateSpace") != "original-cell-visual-normalized":
        raise ValueError("区域坐标必须以原始单格画面区为准，排除标签。")
    label = plan.get("labelHeight", 38)
    if isinstance(label, bool) or not isinstance(label, int) or label < 1:
        raise ValueError("labelHeight 必须为正整数。")
    for cell in plan["cells"]:
        regions = cell.get("regions")
        if not isinstance(regions, dict) or set(regions) != {"product", "creator", "protect"}:
            raise ValueError("每格regions必须含product、creator、protect。")
        for kind in ("product", "creator", "protect"):
            polygons(regions[kind], kind)
            if kind != "protect" and bool(regions[kind]) != bool(cell[kind]["replace"]):
                raise ValueError(f"第{cell['index']}格 {kind}区域必须与replace选择一致。")
    return plan


def validate_anchors(plan, anchors):
    if not isinstance(anchors, list) or len(anchors) != 9:
        raise ValueError("区域编辑前需要9个完整原始anchors。")
    # v4.0 visual-anchor fast path chooses timestamps algorithmically and intentionally does
    # not infer product/person semantics from dense candidate frames. In that
    # mode the final rendered 3x3 board plus explicit Step 2 annotations are
    # the semantic source of truth, so legacy semantic equality checks do not apply.
    if all(str(anchor.get("semanticSource", "observed")) == "unobserved" for anchor in anchors):
        return
    for cell, anchor in zip(plan["cells"], anchors):
        for kind, present, extent, count in (("product", "productPresent", "productVisibility", "productCount"), ("creator", "personPresent", "personExtent", "personCount")):
            if any(key not in anchor for key in (present, extent, count)):
                raise ValueError("原始anchors缺少存在、可见范围或数量字段。")
            if cell[kind]["state"] != anchor[extent] or cell[kind]["count"] != anchor[count] or bool(anchor[present]) != (cell[kind]["state"] != "none"):
                raise ValueError(f"第{cell['index']}格 {kind} 与原始anchor不一致。")
        if cell["interactionState"] != anchor.get("interactionState", ""):
            raise ValueError("interactionState必须逐格原样保留，不能用通用描述覆盖。")


def geometry(width, height):
    a, b = min(RATIOS, key=lambda r: abs(math.log((width / height) / (r[0] / r[1]))))
    scale = max(math.ceil(width / a), math.ceil(height / b))
    w, h = a * scale, b * scale
    return {"width": w, "height": h, "x": (w - width) // 2, "y": (h - height) // 2, "aspectRatio": f"{a}:{b}"}


def validate_canvas_aspect_ratio(edited_width, edited_height, canvas_width, canvas_height):
    if min(edited_width, edited_height, canvas_width, canvas_height) <= 0:
        raise ValueError("画布宽高必须为正数。")
    drift = abs((edited_width / edited_height) / (canvas_width / canvas_height) - 1)
    if drift > MAX_CANVAS_ASPECT_RATIO_DRIFT:
        raise ValueError("生成画布比例偏移超过15%；停止合成，不做中心裁切补救。")
    return drift


def prepare_canvas(ffmpeg, original, output, width, height):
    g = geometry(width, height)
    output.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg, "-v", "error", "-y", "-i", str(original), "-vf", f"format=rgb24,pad={g['width']}:{g['height']}:{g['x']}:{g['y']}:0x808080", "-frames:v", "1", str(output)])
    return g


def _run(command, data=None):
    r = subprocess.run(command, input=data, capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr.decode(errors="replace")[-2000:])
    return r.stdout


def rgb(ffmpeg, path, filters="format=rgb24"):
    return _run([ffmpeg, "-v", "error", "-i", str(path), "-vf", filters, "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])


def write_rgb(ffmpeg, path, pixels, width, height):
    path.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-i", "-", "-frames:v", "1", str(path)], bytes(pixels))


def raster(polys, width, height):
    """Scanline polygon union; multiple polygons support disconnected components."""
    mask = bytearray(width * height)
    for poly in polys:
        points = [(x * width, y * height) for x, y in poly]
        for y in range(height):
            scan = y + .5
            xs = []
            for i, (x1, y1) in enumerate(points):
                x2, y2 = points[(i + 1) % len(points)]
                if (y1 <= scan < y2) or (y2 <= scan < y1):
                    xs.append(x1 + (scan - y1) * (x2 - x1) / (y2 - y1))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                left = max(0, math.ceil(xs[i] - .5))
                right = min(width, math.ceil(xs[i + 1] - .5))
                if right > left:
                    mask[y * width + left:y * width + right] = b'\xff' * (right - left)
    return mask


def board_mask(plan, width, height):
    validate_regions(plan)
    if width % 3 or height % 3:
        raise ValueError("原板尺寸必须能被3整除。")
    cw, ch = width // 3, height // 3
    vh = ch - plan.get("labelHeight", 38)
    if vh < 2:
        raise ValueError("标签高度超出单格。")
    mask = bytearray(width * height)
    for cell in plan["cells"]:
        r = cell["regions"]
        edit = raster(r["product"] + r["creator"], cw, vh)
        protect = raster(r["protect"], cw, vh)
        count = 0
        row, col = divmod(cell["index"] - 1, 3)
        for y in range(vh):
            for x in range(cw):
                pos = y * cw + x
                # Original grid edges and protected foreground always win.
                if edit[pos] and not protect[pos] and 0 < x < cw - 1 and y > 0:
                    mask[(row * ch + y) * width + col * cw + x] = 255
                    count += 1
        if (cell["product"]["replace"] or cell["creator"]["replace"]) and not count:
            raise ValueError(f"第{cell['index']}格有效编辑区域为空。")
        if count >= (cw - 2) * (vh - 1):
            raise ValueError("不能用整格蒙版绕过对象区域保护。")
    return mask


def merge(ffmpeg, ffprobe, original, edited, plan_path, output):
    try:
        from scripts import storyboard_cells as sc
    except ModuleNotFoundError:
        import storyboard_cells as sc
    plan = sc.normalize_replacement_map(json.loads(plan_path.read_text(encoding="utf-8")))
    width, height = sc.dimensions(ffprobe, original, ffmpeg)
    mask = board_mask(plan, width, height)
    ew, eh = sc.dimensions(ffprobe, edited, ffmpeg)
    canvas = geometry(width, height)
    canvas_aspect_ratio_drift = validate_canvas_aspect_ratio(
        ew, eh, canvas['width'], canvas['height'],
    )
    original_pixels = rgb(ffmpeg, original)
    normalized = rgb(ffmpeg, edited, f"format=rgb24,scale={canvas['width']}:{canvas['height']},crop={width}:{height}:{canvas['x']}:{canvas['y']}")
    if len(original_pixels) != width * height * 3 or len(normalized) != len(original_pixels):
        raise ValueError("像素解码尺寸错误。")
    # Restore deterministic layout, then retain generated pixels only inside
    # the explicitly authorized object regions.
    layout = sc.restore_board_layout(
        ffmpeg, ffprobe, edited, original, output,
        label_height=plan.get('labelHeight', 38),
    )
    actual = rgb(ffmpeg, output)
    locked = plan.get('compositionMode') == 'region-lock-v1'
    mask_file = output.with_suffix('.mask.png')
    outside_changed = None
    if locked:
        # Use source pixels everywhere except the explicit effective edit mask.
        # The mask excludes foreground protect polygons, labels and grid edges.
        composite = bytearray(original_pixels)
        for i, allowed in enumerate(mask):
            if allowed:
                composite[3*i:3*i+3] = actual[3*i:3*i+3]
        write_rgb(ffmpeg, output, composite, width, height)
        write_rgb(ffmpeg, mask_file, bytes(v for v in mask for _ in range(3)), width, height)
        actual = rgb(ffmpeg, output)
        outside_changed = sum(
            1 for i, allowed in enumerate(mask)
            if not allowed and actual[3*i:3*i+3] != original_pixels[3*i:3*i+3]
        )
        if outside_changed:
            raise RuntimeError('区域外像素发生变化，禁止登记为合格结果。')
    if len(actual) != len(original_pixels):
        raise RuntimeError("最终Storyboard像素解码尺寸错误。")
    result = {'method': 'whole-board-lock-merge' if locked else 'whole-board-user-review', 'mergeMode': 'object-regions-v1', 'mapValid': True, 'layoutRestored': True, 'labelsRestored': True, 'alignmentMode': layout['alignmentMode'], 'canvas': canvas, 'canvasAspectRatioDrift': round(canvas_aspect_ratio_drift, 6), 'maxCanvasAspectRatioDrift': MAX_CANVAS_ASPECT_RATIO_DRIFT, 'layoutRestore': layout, 'originalFile': str(original.resolve()), 'originalSha256': sha(original), 'editedSha256': sha(edited), 'mapSha256': sha(plan_path), 'finalSha256': sha(output), 'width': width, 'height': height, 'output': str(output), 'editableCells': plan['editableCells'], 'frozenCells': plan['frozenCells']}
    if locked:
        result.update(method='whole-board-lock-merge', compositionMode='region-lock-v1',
                      editRule=plan['editRule'], lockMergeSucceeded=True,
                      frozenCellsRestored=True, protectedPixelsRestored=True,
                      outsideMaskChangedPixels=outside_changed,
                      maskFile=str(mask_file.resolve()), maskSha256=sha(mask_file))
    else:
        result.update(fullBoardUserReviewRequired=True)
    output.with_suffix('.lock.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return result


def map_template(board, replace_product=False, replace_creator=False):
    anchors = board.get('anchors', [])
    if len(anchors) != 9:
        raise ValueError('需要9个原始anchors。')
    cells = []
    for index, anchor in enumerate(anchors, 1):
        cells.append({'index': index, 'product': {'state': anchor['productVisibility'], 'count': anchor['productCount'], 'replace': replace_product and anchor['productPresent']}, 'creator': {'state': anchor['personExtent'], 'count': anchor['personCount'], 'replace': replace_creator and anchor['personPresent']}, 'interactionState': anchor.get('interactionState', ''), 'regions': {'product': [], 'creator': [], 'protect': []}})
    return {'segmentId': board.get('segmentId', board.get('storyboardId')), 'mergeMode': 'object-regions-v1', 'coordinateSpace': 'original-cell-visual-normalized', 'labelHeight': 38, 'replaceProduct': replace_product, 'replaceCreator': replace_creator, 'cells': cells}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    template = sub.add_parser('map-template')
    template.add_argument('--metadata', required=True)
    template.add_argument('--segment-id', type=int, required=True)
    template.add_argument('--replace-product', action='store_true')
    template.add_argument('--replace-creator', action='store_true')
    template.add_argument('--output', required=True)
    validate = sub.add_parser('validate-map')
    validate.add_argument('--original', required=True)
    validate.add_argument('--plan', required=True)
    validate.add_argument('--metadata')
    validate.add_argument('--segment-id', type=int)
    preview = sub.add_parser('preview-mask')
    preview.add_argument('--original', required=True)
    preview.add_argument('--plan', required=True)
    preview.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        from scripts import storyboard_cells as sc
    except ModuleNotFoundError:
        import storyboard_cells as sc
    output = Path(args.output).resolve() if hasattr(args, 'output') else None
    if output is not None and output.exists():
        raise ValueError('输出已存在，请选择新文件，避免覆盖已确认输入。')
    if args.command == 'map-template':
        value = json.loads(Path(args.metadata).read_text(encoding='utf-8'))
        board = next(b for b in value['boards'] if int(b.get('segmentId', b.get('storyboardId'))) == args.segment_id)
        result = map_template(board, args.replace_product, args.replace_creator)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print('已原样复制anchors。请标注对象及遮挡保护多边形；空区域模板不能提交生成。')
    elif args.command == 'validate-map':
        ff = sc.executable('ffmpeg', None)
        original = Path(args.original).resolve()
        w, h = sc.dimensions(None, original, ff)
        plan_path = Path(args.plan).resolve()
        p = sc.normalize_replacement_map(json.loads(plan_path.read_text(encoding='utf-8')))
        if args.metadata:
            metadata = json.loads(Path(args.metadata).read_text(encoding='utf-8'))
            segment_id = args.segment_id if args.segment_id is not None else int(p.get('segmentId', 0))
            board = next((b for b in metadata.get('boards', []) if int(b.get('segmentId', b.get('storyboardId', -1))) == segment_id), None)
            if board is None:
                raise ValueError(f'metadata中找不到Segment {segment_id}。')
            validate_anchors(p, board.get('anchors', []))
        mask = board_mask(p, w, h)
        editable = sum(1 for value in mask if value)
        if editable <= 0 and (p.get('replaceProduct') or p.get('replaceCreator')):
            raise ValueError('Replacement Map没有有效可编辑像素。')
        print(json.dumps({
            'ok': True, 'mapValid': True, 'segmentId': p.get('segmentId'),
            'editablePixels': editable, 'editableRatio': round(editable / max(1, w * h), 6),
            'editableCells': p.get('editableCells', []), 'frozenCells': p.get('frozenCells', []),
            'previewRequired': False,
        }, ensure_ascii=False))
    else:
        ff = sc.executable('ffmpeg', None)
        original = Path(args.original).resolve()
        w, h = sc.dimensions(None, original, ff)
        p = sc.normalize_replacement_map(json.loads(Path(args.plan).read_text(encoding='utf-8')))
        mask = board_mask(p, w, h)
        pixels = bytearray(rgb(ff, original))
        for i, alpha in enumerate(mask):
            if alpha:
                pixels[i*3] = (pixels[i*3] + 255) // 2
                pixels[i*3+1] //= 2
                pixels[i*3+2] //= 2
        write_rgb(ff, output, pixels, w, h)
        print(json.dumps({'output': str(output), 'redArea': 'editable; all other pixels protected'}))


if __name__ == '__main__':
    main()
