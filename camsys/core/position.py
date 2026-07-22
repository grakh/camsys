"""
core/position.py — POSITION-вариант экспорта.

Заказ, вырезанный отдельно (не в сшивке), кладётся оператором на станок
в собственной локальной системе координат. Задача POSITION-варианта —
получить .anc, где координаты пересчитаны в систему «LB-репер в (0,0),
парный репер справа на оси X», с учётом ориентации заказа.

Правила выбора трансформа (по заметкам пользователя):

    - Реперы всегда внизу заказа (по конвенции макета).
    - Пара «LB + верхний/правый» определяет ось.
    - Если пара вертикальная (dy > dx) → заказ повёрнут на 90° (CW = -90°).
    - LB = min по Y (при равенстве — min по X) среди пары.
    - Верхний/правый становится (dist, 0).

Формулы:

    С поворотом -90° (пара вертикальная):
        new_x =  y - lb_y
        new_y = -(x - lb_x)

    Без поворота (пара горизонтальная):
        new_x = x - lb_x
        new_y = y - lb_y

Трансформ — жёсткая изометрия (det = +1), поэтому дуги сохраняют CCW и
радиус, локальные касательные корректно переносятся. G2/G3 не меняются
местами, эмиттер трогать не нужно.

Выбор пары реперов, когда в регион попало 3+:
    - Сначала ищем коаксиальную пару (dx < eps ИЛИ dy < eps) с максимальным
      расстоянием. Это соответствует «правильной» физической паре: на
      реальных стичках лишний репер (общий для листа) сдвинут по обеим осям
      относительно истинной пары.
    - Fallback: если ни одна пара не коаксиальна — берём максимально
      удалённую пару.
Это открытое решение, если найдётся контрпример на реальном файле —
поменять правило в pick_alignment_pair.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Any
import math

# ── Импорты primitives/project совместимы с двумя способами загрузки:
#    (а) как часть пакета camsys.core.position (обычный запуск)
#    (б) как flat-модуль core.position (main.py tests кладёт camsys/ в sys.path)
try:
    from ..geometry.primitives import Line, Arc, Polypath
    from .project import Fiducial
except (ImportError, ValueError):  # pragma: no cover
    from geometry.primitives import Line, Arc, Polypath  # type: ignore
    from core.project import Fiducial  # type: ignore


# eps в мм для определения коаксиальности реперов
COAX_EPS_MM = 1.0


@dataclass
class PositionTransform:
    """Аффинный трансформ POSITION: сначала сдвиг к LB, потом (опционально)
    поворот -90° CW. Всё в мм.
    
    `dist=None` → пары реперов нет (только LB), поворот не определён.
    Вызывающий код в этом случае НЕ должен перезаписывать DistC2C в шапке —
    оставляем дефолтное значение, чтобы ALIGN не сломался нулём.
    """
    lb_x: float
    lb_y: float
    rotate_cw90: bool
    dist: Optional[float]  # None если репер один, иначе |LB → other|

    def apply_point(self, x: float, y: float) -> Tuple[float, float]:
        if self.rotate_cw90:
            # (x - lb_x, y - lb_y) → повёрнутое -90° CW: (y', -x')
            return (y - self.lb_y, -(x - self.lb_x))
        else:
            return (x - self.lb_x, y - self.lb_y)


def _pair_span(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def pick_alignment_pair(fiducials: Sequence[Any],
                        eps: float = COAX_EPS_MM
                        ) -> Optional[Tuple[Any, Optional[Any]]]:
    """Выбрать пару реперов (LB, другой) из списка региона.

    Args:
        fiducials: список объектов с полями .x, .y (обычно Fiducial).
        eps: допуск (мм) для признания пары коаксиальной.

    Returns:
        (lb, other) — LB с min Y (при равенстве min X), other — парный по
        правилу «коаксиальный + max span», либо fallback = самый удалённый.
        Если реперов ровно 1 → (lb, None): POSITION применим только со
        сдвигом, поворот определить нельзя.
        Если 0 → None.
    """
    fids = list(fiducials)
    n = len(fids)
    if n == 0:
        return None
    if n == 1:
        return (fids[0], None)
    if n == 2:
        a, b = fids
    else:
        # Ищем коаксиальную пару с максимальным span
        best = None
        best_span = -1.0
        for i in range(n):
            for j in range(i + 1, n):
                fa, fb = fids[i], fids[j]
                dx = abs(fa.x - fb.x)
                dy = abs(fa.y - fb.y)
                if dx < eps or dy < eps:
                    span = max(dx, dy)
                    if span > best_span:
                        best_span = span
                        best = (fa, fb)
        if best is None:
            # Fallback: самая удалённая пара
            for i in range(n):
                for j in range(i + 1, n):
                    fa, fb = fids[i], fids[j]
                    d = _pair_span(fa, fb)
                    if d > best_span:
                        best_span = d
                        best = (fa, fb)
        a, b = best  # type: ignore

    # LB = min по Y, при равенстве — min по X
    if (a.y, a.x) > (b.y, b.x):
        a, b = b, a
    return (a, b)


def compute_position_transform(fiducials: Sequence[Any],
                               eps: float = COAX_EPS_MM
                               ) -> Optional[PositionTransform]:
    """Построить трансформ POSITION по реперам региона.

    Логика ориентации:
        Строим вектор LB → other. Если |dy| > |dx| — пара «вертикальная»,
        заказ повёрнут на 90° в исходных координатах, применяем -90° CW,
        чтобы «верхний» стал «правым». Иначе — только сдвиг.

    Один репер:
        Возвращаем трансформ только со сдвигом, dist=None. Поворот
        определить нельзя — оператор доводит вручную на станке.

    Ноль реперов: None.
    """
    pair = pick_alignment_pair(fiducials, eps=eps)
    if pair is None:
        return None
    lb, other = pair
    if other is None:
        # Один репер — только сдвиг
        return PositionTransform(lb_x=lb.x, lb_y=lb.y,
                                 rotate_cw90=False, dist=None)
    dx = other.x - lb.x
    dy = other.y - lb.y
    rotate = abs(dy) > abs(dx)
    dist = math.hypot(dx, dy)
    return PositionTransform(lb_x=lb.x, lb_y=lb.y,
                             rotate_cw90=rotate, dist=dist)


# ─────────────────────────────────────────────────────────────────────────
#  Применение к геометрии
# ─────────────────────────────────────────────────────────────────────────

def transform_polypath(pp, t: PositionTransform):
    """Возвращает НОВЫЙ Polypath с трансформированными сегментами.

    Дуги: центр и концы переносятся, ccw и радиус сохраняются (изометрия).
    """
    new_segs = []
    for seg in pp.segments:
        if isinstance(seg, Line):
            new_segs.append(Line(
                a=t.apply_point(*seg.a),
                b=t.apply_point(*seg.b),
            ))
        elif isinstance(seg, Arc):
            new_segs.append(Arc(
                a=t.apply_point(*seg.a),
                b=t.apply_point(*seg.b),
                center=t.apply_point(*seg.center),
                ccw=seg.ccw,
            ))
        else:
            # Неизвестный тип — просто копируем ссылку (не должно случаться:
            # после импорта все безье уже разложены в Line+Arc).
            new_segs.append(seg)
    return Polypath(segments=new_segs, closed=pp.closed)


def transform_fiducial(fid, t: PositionTransform):
    """Возвращает НОВЫЙ Fiducial с трансформированными координатами."""
    nx, ny = t.apply_point(fid.x, fid.y)
    return Fiducial(id=fid.id, x=nx, y=ny, name=fid.name)


# ─────────────────────────────────────────────────────────────────────────
#  Пост-процессинг сгенерированного .anc: X/Y → локальные координаты
# ─────────────────────────────────────────────────────────────────────────

import re as _re

# Пара X<num> Y<num> с необязательным знаком/десятичной частью.
_XY_RE = _re.compile(
    r'X(-?\d+(?:\.\d+)?)\s+Y(-?\d+(?:\.\d+)?)'
)


def _fmt5(v: float) -> str:
    """Совместимо с EmitterBase.format_coord: 5 знаков после точки,
    хвостовые нули срезаются."""
    s = f"{v:.5f}"
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    if not s or s == '-':
        return '0'
    return s


def _transform_anc_text(content: str, t: PositionTransform) -> str:
    """Пост-процессинг сгенерированного .anc: X/Y пары моторных строк
    переводятся в локальную СК заказа. Константы шапки и трейлера
    (парковка X785 Y600, CCD/MP-оффсеты через G52/G152) не трогаются.

    Правила:
        - После метки `.PRGEND` — трейлер, ничего не меняем.
        - Строки, содержащие `G52` или `G152` — задают оффсет CCD/MicroPerf,
          это машинные константы, не координаты движения.
        - Все остальные строки с парой `X<num> Y<num>` — моторные (G0/G1/
          G2/G3/G12/G13, включая модальные без явного G-кода в теле контура
          и точки drill-цикла `;210,x` / `;211,x`).

    Формат числа сохраняем совместимым с format_coord (5 dp, tail zeros
    срезаются).
    """
    lines = content.splitlines(keepends=True)
    out = []
    in_trailer = False
    # Лейбл трейлера: строка ВИДА `.PRGEND` (с необязательным префиксом
    # `N123 `), не `GOTO .PRGEND` из error-хендлеров в шапке.
    _prgend_label = _re.compile(r'^\s*(?:N?\d+\s+)?\.PRGEND\b')

    for line in lines:
        if _prgend_label.match(line):
            in_trailer = True

        if in_trailer:
            out.append(line)
            continue

        # G52/G152 — оффсет-команды станка, координаты в них не наши
        if 'G52' in line or 'G152' in line:
            out.append(line)
            continue

        m = _XY_RE.search(line)
        if not m:
            out.append(line)
            continue

        x = float(m.group(1))
        y = float(m.group(2))
        nx, ny = t.apply_point(x, y)
        new_xy = f"X{_fmt5(nx)} Y{_fmt5(ny)}"
        line = line[:m.start()] + new_xy + line[m.end():]
        out.append(line)

    return ''.join(out)
