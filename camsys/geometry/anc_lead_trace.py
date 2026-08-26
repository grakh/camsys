"""
camsys.geometry.anc_lead_trace — извлечение точек касания лидов из
сгенерированного .anc.

Тактика: парсим modal G-code построчно, отслеживаем текущую (X, Y).
Для каждого ножа в .anc:

    N.. G0 X<ax> Y<ay> ;40,9              ← точка приближения (approach)
    N.. G1 Z0 F.. ;40,10                    ← плунж (в 2D не рисуем)
    N.. G1 G42 X<tx> Y<ty> F.. SCLN(2) ;40,7   ← лид-in ЛИНИЯ, endpoint = тангенс
    N.. G3 X<tx> Y<ty> R<r> SCLN(2) ;50,10     ← лид-in ДУГА, endpoint = тангенс
    (…тело контура…)

Тангенс (`tx, ty`) — это точка на контуре ножа, где лид-in касается
контура и начинается тело реза. Это как раз та точка, которую viewer
до этого пересчитывал сам (иногда с расхождением от эмиттера). Взяв её
напрямую из .anc, viewer гарантированно совпадёт со станочным .anc.

Возвращаем список записей в порядке появления в .anc:
    [
        {'approach': (ax, ay), 'tangent': (tx, ty), 'seq': 0},
        ...
    ]
"""
from __future__ import annotations
from typing import List, Dict, Any, Tuple, Optional
import re

# X<num> Y<num> — обе координаты вместе. Отдельно X<num> или Y<num> тоже
# бывает (модальный вариант), но нам важны только явные пары.
_XY_RE = re.compile(r'X(-?\d+(?:\.\d+)?)\s+Y(-?\d+(?:\.\d+)?)')

# Комментарий-тег в конце строки: `;<major>,<minor>` — маркеры от Alpha-
# постпроцессора, по ним понимаем какая роль у моторной строки.
_TAG_RE = re.compile(r';(\d+),(\d+)')


def parse_anc_leads(anc_text: str) -> List[Dict[str, Any]]:
    """Разбирает содержимое .anc-файла, возвращает список тангенс-точек.

    Args:
        anc_text: сырое содержимое .anc-файла (строка).

    Returns:
        Список dict'ов вида {'approach': (x, y), 'tangent': (x, y),
        'seq': int} — по одной записи на каждый лид-in найденный в .anc.
        Порядок — как в .anc.
    """
    entries: List[Dict[str, Any]] = []
    pending_approach: Optional[Tuple[float, float]] = None

    for line in anc_text.splitlines():
        m_xy = _XY_RE.search(line)
        if not m_xy:
            continue
        x = float(m_xy.group(1))
        y = float(m_xy.group(2))

        # Собираем все теги строки. Обычно один, но перестрахуемся.
        tags = [(m.group(1), m.group(2)) for m in _TAG_RE.finditer(line)]
        if not tags:
            continue

        for major, minor in tags:
            tag = (major, minor)
            if tag == ('40', '9'):
                # G0 rapid to approach — запоминаем
                pending_approach = (x, y)
            elif tag in (('40', '7'), ('50', '10'), ('60', '10')):
                # Лид-in линия (;40,7) или дуга (;50,10 = G3, ;60,10 = G2)
                # Endpoint = тангенс на контуре.
                if pending_approach is not None:
                    entries.append({
                        'approach': pending_approach,
                        'tangent': (x, y),
                        'seq': len(entries),
                    })
                    pending_approach = None

    return entries


def match_tangents_to_ops(tangents: List[Dict[str, Any]],
                          ops_with_bbox: List[Tuple[Any, Tuple[float, float,
                                                                float, float]]],
                          tolerance_mm: float = 2.0
                          ) -> Dict[Any, List[Tuple[float, float]]]:
    """Сопоставляет тангенсы с операциями по попаданию в bbox контура.

    Args:
        tangents: результат `parse_anc_leads`.
        ops_with_bbox: список (op_id_or_key, (x0, y0, x1, y1)) для каждой
            операции. Ключ произвольный — им же будем помечать результат.
        tolerance_mm: расширение bbox на eps для случаев, когда тангенс
            лежит на границе (например, ровно на top-edge Y=67).

    Returns:
        Dict {op_key: [(tx, ty), ...]} — ВСЕ тангенсы op'а в порядке
        следования проходов в .anc файле. Каждый нож обычно имеет
        2 прохода (INSIDE + OUTSIDE) с РАЗНЫМИ точками захода —
        например (51.07, 87.05) для первого и (173.40, 87.05) для
        второго (реальный случай 41000/121554). Раньше здесь бралось
        только первое значение — из-за этого второй проход в viewer'е
        рисовался с точкой первого (разбег с реальным .anc).
    """
    result: Dict[Any, List[Tuple[float, float]]] = {}
    for t in tangents:
        tx, ty = t['tangent']
        best_key = None
        for key, bbox in ops_with_bbox:
            x0, y0, x1, y1 = bbox
            if (x0 - tolerance_mm <= tx <= x1 + tolerance_mm
                    and y0 - tolerance_mm <= ty <= y1 + tolerance_mm):
                best_key = key
                break
        if best_key is not None:
            result.setdefault(best_key, []).append((tx, ty))
    return result
