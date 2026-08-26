"""
camsys.geometry.anc_movement_parser — полный парсер сгенерированного .anc.

Отличие от `anc_lead_trace` (v1.5.18+): тот извлекает только точки касания
лидов с контуром. Этот же парсит КАЖДУЮ моторную строку с её ролью и
геометрией — для полной визуализации того, что уедет в станок.

Роли по тегам Alpha-постпроцессора:
    ;40,9   → approach (G0 XY подъезд к точке начала лида)
    ;40,10  → plunge (G1 Z вниз — не рисуем в 2D)
    ;25,2   → retract (G0 Z вверх — не рисуем в 2D)
    ;40,7   → lead-in линия (G1 с G41/G42)
    ;50,10  → lead-in дуга по часовой (G12 макро или G3)
    ;60,10  → lead-in дуга против часовой (G2 макро)
    ;40,18  → lead-out линия (G1 с G40)
    ;50,15  → lead-out дуга (G13 макро или G3)
    ;60,15  → lead-out дуга (G13 макро или G2)
    ;40,20  → body линия (G1 F..)
    ;50,21  → body дуга CW (G2 XY R)
    ;60,21  → body дуга CCW (G3 XY R)

Группировка: каждый ;40,9 approach отмечает начало нового «прохода
ножа» (blade block). Один физический нож с 2 проходами (INSIDE +
OUTSIDE) даёт 2 блока — они рисуются раздельно и накладываются.

Для G12/G13 (макросы лид-arc'ов) в .anc нет R — только endpoint.
Радиус реконструируем по геометрии: касательная в endpoint известна
из следующего body-сегмента, а approach даёт вторую точку. Через
эти три параметра однозначно определяется окружность лида.
"""
from __future__ import annotations
from typing import List, Dict, Any, Tuple, Optional, NamedTuple
from dataclasses import dataclass, field
import re
import math

Point = Tuple[float, float]

# Регекспы для парсинга моторной строки
_XY_RE = re.compile(r'X(-?\d+(?:\.\d+)?)')
_YY_RE = re.compile(r'\bY(-?\d+(?:\.\d+)?)')
_R_RE = re.compile(r'\bR(-?\d+(?:\.\d+)?)')
_G_RE = re.compile(r'\b(G\d+)\b')
_TAG_RE = re.compile(r';(\d+),(\d+)')

# Классификация тегов → роль движения
_TAG_ROLE = {
    ('40', '9'): 'approach',
    ('40', '10'): 'plunge',
    ('25', '2'): 'retract',
    ('40', '7'): 'lead_in',   # линия
    ('50', '10'): 'lead_in',  # дуга CW (G12)
    ('60', '10'): 'lead_in',  # дуга CCW (обратный G12)
    ('40', '18'): 'lead_out', # линия
    ('50', '15'): 'lead_out', # дуга
    ('60', '15'): 'lead_out', # дуга
    ('40', '20'): 'body',     # линия
    ('50', '21'): 'body',     # дуга CW
    ('60', '21'): 'body',     # дуга CCW
    ('40', '17'): 'body',     # редкий вариант
}


@dataclass
class Movement:
    """Одна моторная строка .anc с распарсенной геометрией."""
    role: str           # approach | plunge | retract | lead_in | lead_out | body | other
    kind: str           # rapid | line | arc_cw | arc_ccw
    start: Point        # (x, y) до движения (модальная XY)
    end: Point          # (x, y) после
    radius: Optional[float] = None    # для дуг с явным R (G2/G3)
    g_code: Optional[str] = None      # 'G0'/'G1'/'G2'/'G3'/'G12'/'G13'
    tag: Optional[str] = None         # исходный тег типа "40,20"


@dataclass
class BladeBlock:
    """Один проход ножа: последовательность движений от approach до retract."""
    movements: List[Movement] = field(default_factory=list)

    def approach_point(self) -> Optional[Point]:
        """Точка приземления (G0 ;40,9)."""
        for m in self.movements:
            if m.role == 'approach':
                return m.end
        return None

    def tangent_point(self) -> Optional[Point]:
        """Точка касания контура (endpoint последнего lead_in)."""
        last_lead_in = None
        for m in self.movements:
            if m.role == 'lead_in':
                last_lead_in = m
            elif m.role == 'body':
                break
        return last_lead_in.end if last_lead_in else None

    def bbox(self) -> Optional[Tuple[float, float, float, float]]:
        """Bounding box всех body-движений (для матчинга с ops)."""
        pts = []
        for m in self.movements:
            if m.role == 'body':
                pts.append(m.start)
                pts.append(m.end)
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))


def parse_anc_movements(anc_text: str) -> List[BladeBlock]:
    """Разбирает .anc-файл в список блоков движений по проходам ножей.

    Каждый ;40,9 (approach) начинает новый BladeBlock. Модальные G-коды
    и XY отслеживаются построчно.

    Args:
        anc_text: сырое содержимое .anc.

    Returns:
        Список BladeBlock — каждый содержит движения одного прохода
        ножа (approach → plunge → lead-in → body → lead-out → retract).
    """
    blocks: List[BladeBlock] = []
    current: Optional[BladeBlock] = None

    modal_g = 'G0'
    x, y = 0.0, 0.0

    for raw_line in anc_text.splitlines():
        line = raw_line.split(';')[0].strip()  # без комментария
        tags_all = _TAG_RE.findall(raw_line)   # но теги нужны

        # Строка вида ".COL1", ".PART1", "GOTO .PART1" — контроль потока,
        # не моторные. Пропускаем.
        if line.startswith('.') or line.startswith('GOTO'):
            continue

        # Проверяем есть ли явный G-код в этой строке
        g_match = _G_RE.search(line)
        # Обновляем modal_g только если явно указан
        line_g = g_match.group(1) if g_match else modal_g

        # Извлекаем X, Y, R
        m_x = _XY_RE.search(line)
        m_y = _YY_RE.search(line)
        m_r = _R_RE.search(line)
        has_xy = m_x is not None and m_y is not None

        # Если нет тега и нет XY — не моторная строка
        if not tags_all and not has_xy:
            continue

        # Определяем роль: первый распознанный тег
        role = 'other'
        used_tag = None
        for major, minor in tags_all:
            r = _TAG_ROLE.get((major, minor))
            if r:
                role = r
                used_tag = f'{major},{minor}'
                break

        # Approach начинает новый блок
        if role == 'approach':
            if current is not None and current.movements:
                blocks.append(current)
            current = BladeBlock()

        # Обновляем modal_g. Важно: G12/G13 в Anderson MTX это ОДНОРАЗОВЫЕ
        # макросы (lead-in/out arc'ов), не модальные — после них
        # следующая моторная строка без явного G работает как G1.
        # Аналогично G40/G41/G42 — компенсация one-shot.
        # Модал обновляют только «настоящие» модальные: G0/G1/G2/G3.
        if g_match and line_g in ('G0', 'G1', 'G2', 'G3'):
            modal_g = line_g

        # Строим движение (если есть XY)
        if has_xy and current is not None:
            new_x = float(m_x.group(1))
            new_y = float(m_y.group(1))
            radius = float(m_r.group(1)) if m_r else None
            # Классификация kind по G
            if line_g == 'G0':
                kind = 'rapid'
            elif line_g in ('G1', 'G41', 'G42', 'G40'):
                kind = 'line'
            elif line_g == 'G2':
                kind = 'arc_cw'
            elif line_g == 'G3':
                kind = 'arc_ccw'
            elif line_g == 'G12':
                kind = 'arc_cw'  # G12 = lead-in по часовой
            elif line_g == 'G13':
                kind = 'arc_ccw'  # G13 = lead-out против часовой
            else:
                kind = 'line'
            mv = Movement(
                role=role, kind=kind,
                start=(x, y), end=(new_x, new_y),
                radius=radius, g_code=line_g, tag=used_tag)
            current.movements.append(mv)
            x, y = new_x, new_y
        elif role == 'plunge' and current is not None:
            # G1 Z без XY — только меняем "текущий Z", XY не двигается
            mv = Movement(
                role='plunge', kind='line',
                start=(x, y), end=(x, y),
                g_code=line_g, tag=used_tag)
            current.movements.append(mv)
        elif role == 'retract' and current is not None:
            mv = Movement(
                role='retract', kind='rapid',
                start=(x, y), end=(x, y),
                g_code=line_g, tag=used_tag)
            current.movements.append(mv)

    # Финальный блок
    if current is not None and current.movements:
        blocks.append(current)

    return blocks


def reconstruct_lead_arc_radius(mv: Movement, next_body: Optional[Movement]
                                ) -> Optional[float]:
    """Реконструирует радиус G12/G13 арки из геометрии.

    G12/G13 в .anc не содержат R — макрос Anderson MTX подставляет его
    в станке. Для визуализации восстанавливаем через касательную в
    endpoint (направление первого body-сегмента после лида).

    Формула: R = |AB|² / (2·(A-B)·N), где N — нормаль к касательной T.
    Через 2 точки на окружности + касательную в одной из точек радиус
    определяется однозначно (модуль).
    """
    if mv.radius is not None:
        return mv.radius
    if next_body is None:
        return None
    dx = next_body.end[0] - next_body.start[0]
    dy = next_body.end[1] - next_body.start[1]
    tlen = math.hypot(dx, dy)
    if tlen < 1e-9:
        return None
    tx, ty = dx / tlen, dy / tlen
    # Нормаль к касательной (произвольная — знак не важен, берём модуль)
    nx, ny = ty, -tx
    ax, ay = mv.start
    bx, by = mv.end
    dax = ax - bx
    day = ay - by
    ab_sq = dax * dax + day * day
    dot = dax * nx + day * ny
    if abs(dot) < 1e-9:
        return None
    return abs(ab_sq / (2.0 * dot))
