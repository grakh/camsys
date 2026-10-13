"""Экспорт геометрии проекта в PDF — для глаза, печати и Illustrator.

ВАЖНО ПРО ТОЧНОСТЬ. В PDF НЕТ дуги окружности: есть только отрезки и
кубические кривые Безье. Поэтому каждая наша дуга здесь приближается
Безье, и это приближение — единственное место во всей выгрузке, где
геометрия отличается от той, по которой считался рез.

Приближение делается адаптивно: дуга режется на куски, пока измеренное
отклонение не станет меньше `tol_mm` (по умолчанию 0.5 мкм — вчетверо
мельче допуска на высоту вершины). Отклонение МЕРЯЕТСЯ, а не берётся из
формулы: кривая сэмплируется и сравнивается с окружностью.

Если нужно перенести геометрию в другой CAM БЕЗ единого микрона потерь —
берите DXF (`dxf_export.py`), там дуга остаётся дугой.

Слои пишутся как OCG — той же конструкцией, какую читает наш импортёр
(`io_/ai_parser.py`). Поэтому свой же экспорт можно загрузить обратно и
сверить.
"""

from __future__ import annotations
from typing import Any, Dict, Iterable, List, Optional, Tuple
import math

from ..geometry.primitives import Line, Arc

PT_PER_MM = 72.0 / 25.4
_PREC = 5          # 1e-5 pt = 3.5e-6 мм — мельче допуска приближения


def _n(v: float) -> str:
    s = f"{v:.{_PREC}f}"
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s or "0"


def _arc_bezier_pieces(seg: Arc, tol_mm: float) -> List[Tuple]:
    """Режет дугу на куски и возвращает их как кубические Безье.

    Возвращает список (p1, p2, p3) — контрольные точки и конец; начало
    берётся из предыдущей точки пути.
    """
    cx, cy = seg.center
    r = seg.radius
    a0 = math.atan2(seg.a[1] - cy, seg.a[0] - cx)
    a1 = math.atan2(seg.b[1] - cy, seg.b[0] - cx)
    sweep = (a1 - a0) % (2 * math.pi)
    if not seg.ccw:
        sweep -= 2 * math.pi
    if abs(sweep) < 1e-12:
        return []

    def pieces_for(n: int) -> List[Tuple]:
        out = []
        step = sweep / n
        k = 4.0 / 3.0 * math.tan(step / 4.0)
        for i in range(n):
            t0 = a0 + step * i
            t1 = t0 + step
            p0 = (cx + r * math.cos(t0), cy + r * math.sin(t0))
            p3 = (cx + r * math.cos(t1), cy + r * math.sin(t1))
            p1 = (p0[0] - k * r * math.sin(t0), p0[1] + k * r * math.cos(t0))
            p2 = (p3[0] + k * r * math.sin(t1), p3[1] - k * r * math.cos(t1))
            out.append((p0, p1, p2, p3))
        return out

    def worst_error(ps: List[Tuple]) -> float:
        """Отклонение кривой от окружности — ЗАМЕР, а не формула."""
        worst = 0.0
        for p0, p1, p2, p3 in ps:
            for i in range(1, 8):
                t = i / 8.0
                mt = 1 - t
                x = (mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0]
                     + 3 * mt * t * t * p2[0] + t ** 3 * p3[0])
                y = (mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1]
                     + 3 * mt * t * t * p2[1] + t ** 3 * p3[1])
                worst = max(worst, abs(math.hypot(x - cx, y - cy) - r))
        return worst

    n = max(1, int(math.ceil(abs(sweep) / (math.pi / 2))))
    ps = pieces_for(n)
    while worst_error(ps) > tol_mm and n < 256:
        n *= 2
        ps = pieces_for(n)
    return ps


def _layer_content(layer, tol_mm: float) -> List[str]:
    ops: List[str] = []
    for geom in layer.geometries:
        pp = getattr(geom, 'polypath', None)
        if pp is None or not pp.segments:
            continue
        cur: Optional[Tuple[float, float]] = None
        for seg in pp.segments:
            if cur is None or (abs(seg.a[0] - cur[0]) > 1e-9
                               or abs(seg.a[1] - cur[1]) > 1e-9):
                if cur is not None:
                    ops.append("S")
                ops.append(f"{_n(seg.a[0] * PT_PER_MM)} "
                           f"{_n(seg.a[1] * PT_PER_MM)} m")
            if isinstance(seg, Line):
                ops.append(f"{_n(seg.b[0] * PT_PER_MM)} "
                           f"{_n(seg.b[1] * PT_PER_MM)} l")
            elif isinstance(seg, Arc) and seg.radius > 0:
                for _p0, p1, p2, p3 in _arc_bezier_pieces(seg, tol_mm):
                    ops.append(
                        f"{_n(p1[0] * PT_PER_MM)} {_n(p1[1] * PT_PER_MM)} "
                        f"{_n(p2[0] * PT_PER_MM)} {_n(p2[1] * PT_PER_MM)} "
                        f"{_n(p3[0] * PT_PER_MM)} {_n(p3[1] * PT_PER_MM)} c")
            else:
                continue
            cur = seg.b
        if cur is not None:
            if getattr(pp, 'closed', False):
                ops.append("h")
            ops.append("S")
            cur = None
    return ops


def _fiducial_content(fiducials, radius_mm: float, tol_mm: float) -> List[str]:
    ops: List[str] = []
    for fid in fiducials:
        circle = Arc(a=(fid.x + radius_mm, fid.y), b=(fid.x + radius_mm, fid.y),
                     center=(fid.x, fid.y), ccw=True)
        # Полный круг как две полудуги — одна дуга с совпадающими концами
        # имеет нулевой разворот.
        for half in (
            Arc(a=(fid.x + radius_mm, fid.y), b=(fid.x - radius_mm, fid.y),
                center=(fid.x, fid.y), ccw=True),
            Arc(a=(fid.x - radius_mm, fid.y), b=(fid.x + radius_mm, fid.y),
                center=(fid.x, fid.y), ccw=True),
        ):
            if half is not None and half.a == half.b:
                continue
            if not ops or ops[-1] == "S":
                ops.append(f"{_n(half.a[0] * PT_PER_MM)} "
                           f"{_n(half.a[1] * PT_PER_MM)} m")
            for _p0, p1, p2, p3 in _arc_bezier_pieces(half, tol_mm):
                ops.append(
                    f"{_n(p1[0] * PT_PER_MM)} {_n(p1[1] * PT_PER_MM)} "
                    f"{_n(p2[0] * PT_PER_MM)} {_n(p2[1] * PT_PER_MM)} "
                    f"{_n(p3[0] * PT_PER_MM)} {_n(p3[1] * PT_PER_MM)} c")
        ops.append("h")
        ops.append("S")
    _ = circle  # noqa: F841 — оставлено для читаемости намерения
    return ops


def build_pdf(project,
              layer_names: Optional[Iterable[str]] = None,
              tol_mm: float = 0.0005,
              margin_mm: float = 5.0,
              line_width_mm: float = 0.1,
              fiducial_radius_mm: float = 1.5) -> bytes:
    """Собирает PDF с геометрией проекта. Координаты АБСОЛЮТНЫЕ — те же,
    что в `.anc`, чтобы система координат не поехала."""
    from .dxf_export import project_bounds

    want = set(layer_names) if layer_names is not None else None
    bounds = project_bounds(project) or (0.0, 0.0, 100.0, 100.0)
    x0, y0, x1, y1 = bounds
    mb = (( x0 - margin_mm) * PT_PER_MM, (y0 - margin_mm) * PT_PER_MM,
          ( x1 + margin_mm) * PT_PER_MM, (y1 + margin_mm) * PT_PER_MM)

    # Цвета слоёв — лишь бы отличались на глаз.
    palette = [(0, 0.6, 0), (0.8, 0, 0.6), (0, 0.4, 0.9), (0.9, 0.5, 0),
               (0.5, 0.5, 0.5), (0.6, 0, 0.8)]

    blocks: List[Tuple[str, List[str]]] = []
    for layer in project.layers.values():
        if want is not None and layer.name not in want:
            continue
        ops = _layer_content(layer, tol_mm)
        if ops:
            blocks.append((layer.name, ops))
    fids = list(getattr(project, 'fiducials', ()) or ())
    if fids and fiducial_radius_mm > 0:
        ops = _fiducial_content(fids, fiducial_radius_mm, tol_mm)
        if ops:
            blocks.append(("FIDUCIALS", ops))
    if not blocks:
        blocks = [("EMPTY", [])]

    # ── Содержимое страницы ──
    content: List[str] = [f"{_n(line_width_mm * PT_PER_MM)} w", "1 J", "1 j"]
    for i, (name, ops) in enumerate(blocks):
        r, g, b = palette[i % len(palette)]
        content.append(f"/OC /MC{i} BDC")
        content.append(f"{_n(r)} {_n(g)} {_n(b)} RG")
        content.extend(ops)
        content.append("EMC")
    stream = "\n".join(content).encode('latin-1', 'replace')

    # ── Сборка объектов ──
    # 1 Catalog, 2 Pages, 3 Page, 4 Contents, 5.. OCG по одному на слой
    n_ocg = len(blocks)
    ocg_first = 5
    objs: Dict[int, bytes] = {}
    ocg_refs = " ".join(f"{ocg_first + i} 0 R" for i in range(n_ocg))
    props = " ".join(f"/MC{i} {ocg_first + i} 0 R" for i in range(n_ocg))

    objs[1] = (f"<< /Type /Catalog /Pages 2 0 R /OCProperties << "
               f"/OCGs [{ocg_refs}] /D << /ON [{ocg_refs}] /Order [{ocg_refs}] "
               f"/RBGroups [] >> >> >>").encode('latin-1')
    objs[2] = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    objs[3] = (f"<< /Type /Page /Parent 2 0 R /MediaBox "
               f"[{_n(mb[0])} {_n(mb[1])} {_n(mb[2])} {_n(mb[3])}] "
               f"/Resources << /Properties << {props} >> >> "
               f"/Contents 4 0 R >>").encode('latin-1')
    objs[4] = (f"<< /Length {len(stream)} >>\nstream\n".encode('latin-1')
               + stream + b"\nendstream")
    for i, (name, _ops) in enumerate(blocks):
        safe = name.replace('(', '_').replace(')', '_').replace('\\', '_')
        objs[ocg_first + i] = (
            f"<< /Type /OCG /Name ({safe}) >>").encode('latin-1')

    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    offsets: Dict[int, int] = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode('latin-1')
        out += objs[num]
        out += b"\nendobj\n"
    xref_at = len(out)
    total = max(objs) + 1
    out += f"xref\n0 {total}\n".encode('latin-1')
    out += b"0000000000 65535 f \n"
    for num in range(1, total):
        out += f"{offsets.get(num, 0):010d} 00000 n \n".encode('latin-1')
    out += (f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n"
            f"{xref_at}\n%%EOF\n").encode('latin-1')
    return bytes(out)


def export_project_pdf(project, path: str, **kwargs) -> dict:
    data = build_pdf(project, **kwargs)
    with open(path, 'wb') as fh:
        fh.write(data)
    return {'path': path, 'bytes': len(data)}
