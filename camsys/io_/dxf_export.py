"""Экспорт геометрии проекта в DXF (ASCII R12).

ЗАЧЕМ. Если на станке что-то пошло не так, деталь надо доделать отдельно
— в альфе или другом CAM. Для этого нужна ТА ЖЕ геометрия, по которой
считался рез, а не исходный `.ai`: при импорте мы раскладываем кривые
Безье в биарки и потом сливаем мелкие цепочки в дуги
(`merge_segments_to_arcs`). Альфа при своём импорте делает это иначе,
поэтому по исходному файлу в размер не попасть.

ПОЧЕМУ DXF, А НЕ PDF. В PDF нет дуги окружности: только отрезки и кривые
Безье. Экспорт в PDF означает приблизить наши дуги Безье, а чужой
импортёр потом приблизит их обратно дугами — то есть ровно то
расхождение, от которого мы и уходим. В DXF дуга — отдельная сущность
`ARC` с центром, радиусом и углами, и переносится она точно, бит в бит.
PDF рядом остаётся как картинка для глаза и печати (`pdf_export.py`).

ФОРМАТ. ASCII DXF R12 — самый переносимый из всех: его читают все CAM и
все редакторы, и писать его можно без внешних библиотек (у нас в
зависимостях нет ни ezdxf, ни чего-либо подобного).
"""

from __future__ import annotations
from typing import Any, Iterable, List, Optional, Tuple
import math

from ..geometry.primitives import Line, Arc


# Точность записи координат. Шесть знаков после запятой — это нанометры
# при наших миллиметрах, на три порядка мельче допуска биарк-фита (1 мкм)
# и на четыре — допуска на высоту вершины (2 мкм).
_PREC = 6


def _f(value: float) -> str:
    return f"{value:.{_PREC}f}"


def _pair(code: int, value: Any) -> List[str]:
    return [str(code), str(value)]


def _angle_deg(center: Tuple[float, float], p: Tuple[float, float]) -> float:
    a = math.degrees(math.atan2(p[1] - center[1], p[0] - center[0]))
    return a + 360.0 if a < 0 else a


def _arc_entity(seg: Arc, layer: str) -> List[str]:
    """Дуга DXF. В DXF дуга ВСЕГДА идёт против часовой от начального угла
    к конечному, поэтому для нашей дуги по часовой углы меняются местами.
    Сама геометрия при этом та же — меняется только способ записи.
    """
    cx, cy = seg.center
    r = seg.radius
    if seg.ccw:
        a0, a1 = _angle_deg(seg.center, seg.a), _angle_deg(seg.center, seg.b)
    else:
        a0, a1 = _angle_deg(seg.center, seg.b), _angle_deg(seg.center, seg.a)
    out: List[str] = []
    out += _pair(0, "ARC")
    out += _pair(8, layer)
    out += _pair(10, _f(cx))
    out += _pair(20, _f(cy))
    out += _pair(30, _f(0.0))
    out += _pair(40, _f(r))
    out += _pair(50, _f(a0))
    out += _pair(51, _f(a1))
    return out


def _line_entity(seg: Line, layer: str) -> List[str]:
    out: List[str] = []
    out += _pair(0, "LINE")
    out += _pair(8, layer)
    out += _pair(10, _f(seg.a[0]))
    out += _pair(20, _f(seg.a[1]))
    out += _pair(30, _f(0.0))
    out += _pair(11, _f(seg.b[0]))
    out += _pair(21, _f(seg.b[1]))
    out += _pair(31, _f(0.0))
    return out


def _circle_entity(x: float, y: float, r: float, layer: str) -> List[str]:
    out: List[str] = []
    out += _pair(0, "CIRCLE")
    out += _pair(8, layer)
    out += _pair(10, _f(x))
    out += _pair(20, _f(y))
    out += _pair(30, _f(0.0))
    out += _pair(40, _f(r))
    return out


def _safe_layer_name(name: str) -> str:
    """Имя слоя DXF R12: без пробелов и спецсимволов."""
    out = []
    for ch in (name or "0"):
        out.append(ch if (ch.isalnum() or ch in "_-$") else "_")
    s = "".join(out).strip("_")
    return s or "0"


def project_bounds(project) -> Optional[Tuple[float, float, float, float]]:
    xs: List[float] = []
    ys: List[float] = []
    for layer in project.layers.values():
        for geom in layer.geometries:
            pp = getattr(geom, 'polypath', None)
            if pp is None or not pp.segments:
                continue
            for seg in pp.segments:
                xs += [seg.a[0], seg.b[0]]
                ys += [seg.a[1], seg.b[1]]
                if isinstance(seg, Arc):
                    # Дуга может выходить за свои концы — берём центр ± R.
                    xs += [seg.center[0] - seg.radius, seg.center[0] + seg.radius]
                    ys += [seg.center[1] - seg.radius, seg.center[1] + seg.radius]
    for fid in getattr(project, 'fiducials', ()) or ():
        xs.append(fid.x)
        ys.append(fid.y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def build_dxf(project,
              layer_names: Optional[Iterable[str]] = None,
              fiducial_radius_mm: float = 1.5,
              fiducial_layer: str = "FIDUCIALS") -> str:
    """Собирает DXF-текст по геометрии проекта.

    Args:
        project: проект; берётся `geom.polypath` КАК ЕСТЬ, то есть уже
            после биарк-фита и `merge_segments_to_arcs` — ровно та
            геометрия, по которой считался рез.
        layer_names: какие слои выгружать (None = все непустые).
        fiducial_radius_mm: радиус окружностей реперов. Реперы нужны,
            чтобы в чужом CAM привязаться к той же системе координат,
            иначе доделать деталь не выйдет.
        fiducial_layer: имя слоя для реперов.
    """
    want = set(layer_names) if layer_names is not None else None

    used_layers: List[str] = []
    entities: List[str] = []
    counts = {'LINE': 0, 'ARC': 0, 'CIRCLE': 0}

    for layer in project.layers.values():
        if want is not None and layer.name not in want:
            continue
        lname = _safe_layer_name(layer.name)
        emitted = False
        for geom in layer.geometries:
            pp = getattr(geom, 'polypath', None)
            if pp is None or not pp.segments:
                continue
            for seg in pp.segments:
                if isinstance(seg, Arc):
                    # Вырожденная дуга (радиус в ноль) в DXF недопустима.
                    if seg.radius <= 0:
                        continue
                    entities += _arc_entity(seg, lname)
                    counts['ARC'] += 1
                elif isinstance(seg, Line):
                    entities += _line_entity(seg, lname)
                    counts['LINE'] += 1
                else:
                    continue
                emitted = True
        if emitted and lname not in used_layers:
            used_layers.append(lname)

    fids = list(getattr(project, 'fiducials', ()) or ())
    if fids and fiducial_radius_mm > 0:
        flayer = _safe_layer_name(fiducial_layer)
        for fid in fids:
            entities += _circle_entity(fid.x, fid.y, fiducial_radius_mm, flayer)
            counts['CIRCLE'] += 1
        if flayer not in used_layers:
            used_layers.append(flayer)

    bounds = project_bounds(project)

    out: List[str] = []
    # ── HEADER: единицы миллиметры и габариты ──
    out += _pair(0, "SECTION")
    out += _pair(2, "HEADER")
    out += _pair(9, "$ACADVER")
    out += _pair(1, "AC1009")          # R12
    out += _pair(9, "$INSUNITS")
    out += _pair(70, 4)                # 4 = миллиметры
    if bounds is not None:
        x0, y0, x1, y1 = bounds
        out += _pair(9, "$EXTMIN")
        out += _pair(10, _f(x0))
        out += _pair(20, _f(y0))
        out += _pair(30, _f(0.0))
        out += _pair(9, "$EXTMAX")
        out += _pair(10, _f(x1))
        out += _pair(20, _f(y1))
        out += _pair(30, _f(0.0))
    out += _pair(0, "ENDSEC")

    # ── TABLES: объявление слоёв ──
    out += _pair(0, "SECTION")
    out += _pair(2, "TABLES")
    out += _pair(0, "TABLE")
    out += _pair(2, "LAYER")
    out += _pair(70, max(1, len(used_layers)))
    for i, lname in enumerate(used_layers or ["0"]):
        out += _pair(0, "LAYER")
        out += _pair(2, lname)
        out += _pair(70, 0)
        out += _pair(62, (i % 7) + 1)   # цвет, лишь бы слои различались
        out += _pair(6, "CONTINUOUS")
    out += _pair(0, "ENDTAB")
    out += _pair(0, "ENDSEC")

    # ── ENTITIES ──
    out += _pair(0, "SECTION")
    out += _pair(2, "ENTITIES")
    out += entities
    out += _pair(0, "ENDSEC")
    out += _pair(0, "EOF")

    return "\r\n".join(out) + "\r\n"


def export_project_dxf(project, path: str, **kwargs) -> dict:
    """Пишет DXF на диск. Возвращает сводку (что и сколько выгружено)."""
    text = build_dxf(project, **kwargs)
    with open(path, 'w', encoding='ascii', newline='') as fh:
        fh.write(text)
    return {
        'path': path,
        'lines': text.count("\r\nLINE\r\n"),
        'arcs': text.count("\r\nARC\r\n"),
        'circles': text.count("\r\nCIRCLE\r\n"),
        'bytes': len(text),
    }


# ─────────────────────────────────────────────────────────────────────────
#  ЧТЕНИЕ ОБРАТНО — для проверки, что выгрузка точная
# ─────────────────────────────────────────────────────────────────────────

def parse_dxf_entities(text: str) -> List[dict]:
    """Разбирает ENTITIES нашего же DXF. Нужен тестам: выгрузили —
    прочитали — сверили с исходной геометрией до нанометра.

    Это не универсальный читатель DXF, а ровно обратная операция к
    `build_dxf`: он понимает LINE, ARC и CIRCLE в том виде, в каком их
    пишем мы.
    """
    rows = text.splitlines()
    out: List[dict] = []
    i = 0
    in_entities = False
    while i + 1 < len(rows):
        code, value = rows[i].strip(), rows[i + 1].strip()
        if code == '0' and value == 'SECTION':
            if i + 3 < len(rows) and rows[i + 3].strip() == 'ENTITIES':
                in_entities = True
        elif code == '0' and value == 'ENDSEC':
            in_entities = False
        elif in_entities and code == '0' and value in ('LINE', 'ARC', 'CIRCLE'):
            ent: dict = {'type': value}
            j = i + 2
            while j + 1 < len(rows) and rows[j].strip() != '0':
                c, v = rows[j].strip(), rows[j + 1].strip()
                if c == '8':
                    ent['layer'] = v
                else:
                    try:
                        ent[c] = float(v)
                    except ValueError:
                        pass
                j += 2
            out.append(ent)
            i = j
            continue
        i += 2
    return out
