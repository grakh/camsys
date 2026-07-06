"""
core/macros.py — макро-операции над проектом.

Это аналог макросов в Альфакаме (.amb), но реализован как чистые функции
Python, работающие с моделью Project. Каждая функция меняет порядок 
operations или их атрибуты, не трогая саму геометрию.

Известные паттерны из практики (по работе с reversNC.amb):
    - group_pairs:     пары внешний+внутренний пути одного контура
    - sort_by_grid:    обход листа по сетке (LR/RL + TB/BT)
    - renumber:        перенумерация sequence_number в порядке списка
    - assign_to_group: явное назначение группы операциям

Все функции возвращают модифицированный Project (мутируют на месте).
"""

from __future__ import annotations
from typing import List, Tuple, Callable
from enum import Enum
from .project import Project, Operation, OperationKind


# ─────────────────────────────────────────────────────────────────────────
#  НАПРАВЛЕНИЯ ОБХОДА (как в reversNC.amb: group=True/False + revers)
# ─────────────────────────────────────────────────────────────────────────

class GridDirection(Enum):
    """Направление обхода листа.
    
    Двухбуквенный код:
       X-направление (L=влево/R=вправо) +
       Y-направление (T=сверху-вниз, B=снизу-вверх)
    
    LB = слева-направо, снизу-вверх (стандартный)
    LT = слева-направо, сверху-вниз
    RB = справа-налево, снизу-вверх (реверс по X)
    RT = справа-налево, сверху-вниз (полный реверс)
    """
    LB = "LB"  # left-to-right, bottom-to-top
    LT = "LT"  # left-to-right, top-to-bottom
    RB = "RB"  # right-to-left, bottom-to-top  
    RT = "RT"  # right-to-left, top-to-bottom


class GridGrouping(Enum):
    """Как группировать операции в сетку: по столбцам или строкам."""
    COLUMNS = "columns"  # сначала по X, потом по Y (вертикальная сетка)
    ROWS = "rows"        # сначала по Y, потом по X (горизонтальная сетка)


# ─────────────────────────────────────────────────────────────────────────
#  ВЫЧИСЛЕНИЕ ЦЕНТРА ОПЕРАЦИИ
# ─────────────────────────────────────────────────────────────────────────

def operation_center(op: Operation, project: Project) -> Tuple[float, float]:
    """Центр bounding box всей геометрии операции."""
    xs, ys = [], []
    for gid in op.geometry_ids:
        geom = project.get_geometry(gid)
        if geom is None or geom.polypath is None:
            continue
        for seg in geom.polypath.segments:
            # для Line: a, b
            if hasattr(seg, 'a'):
                xs.append(seg.a[0]); ys.append(seg.a[1])
            if hasattr(seg, 'b'):
                xs.append(seg.b[0]); ys.append(seg.b[1])
    if not xs:
        return (0.0, 0.0)
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


# ─────────────────────────────────────────────────────────────────────────
#  СОРТИРОВКА ПО СЕТКЕ (главный макрос — переупорядочивание обхода)
# ─────────────────────────────────────────────────────────────────────────

def sort_operations_by_grid(
        project: Project,
        direction: GridDirection = GridDirection.LB,
        grouping: GridGrouping = GridGrouping.COLUMNS,
        col_tolerance: float = 5.0,
        row_tolerance: float = 5.0,
        ) -> None:
    """Сортирует project.operations по сетке листа.
    
    Это аналог SortPaths_Final_HolesFirst_AutoUpdate из reversNC.amb,
    но работает на уровне Operations (а не отдельных ToolPath).
    
    Алгоритм:
        1. Для каждой операции считаем центр (X, Y) геометрии.
        2. Группируем по столбцам или строкам (с допуском tolerance).
        3. Сортируем столбцы/строки по направлению direction.
        4. Внутри столбца/строки сортируем по второй оси.
        5. Применяем порядок через перенумерацию операций в списке.
    
    Args:
        project: проект
        direction: куда идём (LB / LT / RB / RT)
        grouping: что важнее — столбцы или строки
        col_tolerance: допуск группировки в столбец (мм)
        row_tolerance: допуск группировки в строку (мм)
    """
    if not project.operations:
        return
    
    # 1. Считаем центры
    op_centers = [(op, operation_center(op, project)) for op in project.operations]
    
    # 2. Группируем
    if grouping == GridGrouping.COLUMNS:
        primary_key = lambda c: c[0]  # X
        secondary_key = lambda c: c[1]  # Y
        primary_tol = col_tolerance
    else:
        primary_key = lambda c: c[1]
        secondary_key = lambda c: c[0]
        primary_tol = row_tolerance
    
    # Знаки сортировки по направлению
    if direction in (GridDirection.LB, GridDirection.LT):
        primary_sign = +1  # L → возрастание X (или Y если ROWS)
    else:
        primary_sign = -1  # R → убывание
    
    if direction in (GridDirection.LB, GridDirection.RB):
        secondary_sign = +1  # B → возрастание Y (или X если ROWS)
    else:
        secondary_sign = -1  # T → убывание
    
    # 3. Сортируем по первичному ключу, потом группируем близкие
    op_centers.sort(key=lambda oc: primary_sign * primary_key(oc[1]))
    
    # Группы — последовательности операций с близким первичным значением
    groups: List[List[Tuple[Operation, Tuple[float, float]]]] = []
    current_group: List = []
    last_primary = None
    
    for op, center in op_centers:
        p = primary_key(center)
        if last_primary is None or abs(p - last_primary) <= primary_tol:
            current_group.append((op, center))
        else:
            if current_group:
                groups.append(current_group)
            current_group = [(op, center)]
        last_primary = p
    
    if current_group:
        groups.append(current_group)
    
    # 4. Внутри каждой группы сортируем по вторичной оси
    sorted_ops: List[Operation] = []
    for group in groups:
        group.sort(key=lambda oc: secondary_sign * secondary_key(oc[1]))
        sorted_ops.extend(op for op, _ in group)
    
    # 5. Применяем порядок
    project.operations = sorted_ops
    # Перенумеровываем sequence_number
    renumber_operations(project)


# ─────────────────────────────────────────────────────────────────────────
#  ПЕРЕНУМЕРАЦИЯ
# ─────────────────────────────────────────────────────────────────────────

def renumber_operations(project: Project, start: int = 1) -> None:
    """Назначает sequence_number операциям в текущем порядке списка."""
    for i, op in enumerate(project.operations):
        op.sequence_number = start + i


# ─────────────────────────────────────────────────────────────────────────
#  ГРУППИРОВКА В ЧИСТОВЫЕ ПРОГРАММЫ ПО ДЛИНЕ / КОРИДОРАМ (MODE_LENGTH/GROUPS)
# ─────────────────────────────────────────────────────────────────────────

def operation_geom_length(op: Operation, project: Project) -> float:
    """Длина геометрии операции (периметр контура) в мм.
    
    Это длина ОДНОГО обхода контура. В группировке по длине умножается
    на число проходов (обычно 2: внутренний + внешний).
    
    Для CORNER_REWORK возвращаем 0: corner-операции физически идут вместе 
    со своим blade (при экспорте это corner-фрагмент того же контура), 
    поэтому их длина уже посчитана как часть blade. Иначе corner-ops со 
    своим отдельным geometry_id считались бы дважды и раздували корридор 
    в 100+ раз → лишние программы вместо 5.
    """
    if op.kind == OperationKind.CORNER_REWORK:
        return 0.0
    total = 0.0
    for gid in op.geometry_ids:
        geom = project.get_geometry(gid)
        if geom is None or geom.polypath is None:
            continue
        total += sum(s.length() for s in geom.polypath.segments)
    return total


def assign_program_numbers(project: Project,
                           max_geom_len: float = 3000.0,
                           direction: str = "horizontal",
                           corridor_tolerance: float = 5.0,
                           passes_per_part: int = 2,
                           ) -> int:
    """Назначает операциям program_number группировкой по длине путей.
    
    Детали обходятся в порядке, согласованном с direction:
        horizontal → строками (слева-направо внутри строки, строки сверху вниз)
        vertical   → столбцами (снизу-вверх внутри столбца, столбцы слева направо)
    
    Накапливается длина путей (geom_len × passes_per_part). Когда накопленная
    длина достигает лимита — начинается новая программа. Это автоматически:
        - делит длинную строку/столбец на несколько программ
        - объединяет короткие строки/столбцы в одну программу
    
    Записывает op.attributes['program_number'] (нумерация с 1).
    Также ПЕРЕУПОРЯДОЧИВАЕТ project.operations в порядок обхода, чтобы
    постпроцессор выводил детали в правильной последовательности.
    
    Args:
        max_geom_len: лимит длины ПУТЕЙ фрезы на программу (мм). Учитываются
            все проходы (INSIDE+OUTSIDE), не только геометрия контура.
            Например: 3000мм путей при двух проходах = 1500мм геометрии 
            контура на программу.
        direction: "horizontal" | "vertical"
        corridor_tolerance: допуск группировки в строку/столбец (мм)
        passes_per_part: число проходов на деталь (2 = внутр.+внешн.)
    
    Returns:
        Число созданных программ.
    """
    ops = project.operations
    if not ops:
        return 0
    
    # Лимит — уже в путях, не умножаем
    max_path_len = max_geom_len
    
    # ── Упорядочивание в порядок обхода согласно direction ──
    # Правило: программы всегда идут "слева направо + снизу вверх" (от LB угла).
    #   horizontal → строки снизу вверх (Y возр.), внутри строки X возр.
    #   vertical   → столбцы слева направо (X возр.), внутри столбца Y возр.
    centers = {op.id: operation_center(op, project) for op in ops}
    
    if direction == "horizontal":
        # Коридор = строка (по Y). Строки снизу вверх (Y возр.),
        # внутри строки слева направо (X возр.).
        corridor_key = lambda op: centers[op.id][1]   # Y
        within_key = lambda op: centers[op.id][0]      # X
        corridor_desc = False  # строки снизу вверх
        within_desc = False    # слева направо
    else:  # vertical
        # Коридор = столбец (по X). Столбцы слева направо (X возр.),
        # внутри столбца снизу вверх (Y возр.).
        corridor_key = lambda op: centers[op.id][0]    # X
        within_key = lambda op: centers[op.id][1]      # Y
        corridor_desc = False
        within_desc = False    # снизу вверх
    
    # Группируем в коридоры по близости координаты
    ops_by_corridor_coord = sorted(
        ops, key=corridor_key, reverse=corridor_desc)
    
    corridors = []  # список списков операций
    cur_group = []
    last_coord = None
    for op in ops_by_corridor_coord:
        c = corridor_key(op)
        if last_coord is None or abs(c - last_coord) <= corridor_tolerance:
            cur_group.append(op)
        else:
            corridors.append(cur_group)
            cur_group = [op]
        last_coord = c
    if cur_group:
        corridors.append(cur_group)
    
    # Внутри каждого коридора сортируем по within_key
    ordered_ops = []
    for group in corridors:
        group.sort(key=within_key, reverse=within_desc)
        ordered_ops.extend(group)
    
    # Переупорядочиваем операции проекта в порядок обхода
    project.operations = ordered_ops
    
    # ── Группировка по целым коридорам ──
    # Правило: коридор (строка/столбец) — атомарная единица. Программа 
    # состоит из ЦЕЛЫХ коридоров, распределённых РАВНОМЕРНО по числу программ.
    # Делим коридор на части ТОЛЬКО если он сам по себе длиннее лимита.
    #
    # Алгоритм:
    #   1. Сначала разбираемся с "слишком длинными" коридорами (clen > limit):
    #      каждый такой делится на свои части (по ceil(clen/limit)).
    #   2. Оставшиеся целые коридоры распределяем по N программам, где
    #      N = ceil(total_normal_len / limit). Распределение равномерное:
    #      коридор k идёт в программу floor(cumulative_pos / total * N).
    #      Это даёт 2+3 а не 4+1 для 5 столбцов в 2 программах.
    
    # Длина путей каждого коридора (×passes_per_part)
    corridor_lengths = [
        sum(operation_geom_length(op, project) * passes_per_part for op in group)
        for group in corridors
    ]
    
    # Классификация коридоров:
    #   - oversized: clen > limit И len(group) > 1 → делим на части
    #   - normal: всё остальное (включая coridors > limit с 1 деталью, они 
    #     атомарны — у одной детали нечего делить, она просто занимает свою программу)
    oversized = [ci for ci, clen in enumerate(corridor_lengths)
                 if clen > max_path_len and len(corridors[ci]) > 1]
    
    # Для нормальной упаковки считаем общую длину "не-oversized" коридоров
    normal_total = sum(clen for ci, clen in enumerate(corridor_lengths)
                       if ci not in oversized)
    
    # Каждый коридор > лимита с 1 деталью занимает 1 программу (минимум).
    # Остальные распределяем по ceil(normal_total / limit) программам.
    normal_corridor_count = len([ci for ci in range(len(corridors)) 
                                  if ci not in oversized])
    if normal_corridor_count > 0:
        import math
        # Минимум: если самый длинный нормальный коридор > limit, нужно
        # столько программ, чтобы каждый «большой одиночный» был отдельно
        big_singles = sum(1 for ci, clen in enumerate(corridor_lengths)
                          if clen > max_path_len and len(corridors[ci]) == 1
                          and ci not in oversized)
        n_normal_programs = max(
            math.ceil(normal_total / max_path_len),
            big_singles  # каждый большой одиночный = своя программа
        )
        n_normal_programs = max(1, n_normal_programs)
    else:
        n_normal_programs = 0
    
    # Обработаем коридоры в порядке обхода
    prog_no = 0
    normal_acc = 0.0  # накопленная длина обработанных "нормальных" коридоров
    cur_normal_prog = 0  # текущая программа для нормальных коридоров (1-based)
    
    for ci, group in enumerate(corridors):
        clen = corridor_lengths[ci]
        
        if clen > max_path_len and len(group) > 1:
            # Этот коридор сам слишком длинный → делим на равные части.
            # ВАЖНО: делим по «деталям», а не по отдельным операциям. Одна 
            # деталь = blade + её corners (связаны по parent_geom_id). Иначе 
            # blade может попасть в одну программу, а её corners — в другую, 
            # и станок посещает деталь дважды.
            import math
            n_parts = min(math.ceil(clen / max_path_len), len(group))
            base_prog = prog_no  # текущий номер до этого коридора
            
            # Группируем ops в атомарные «детали».
            # Ключ: geometry_id (для blade) или parent_geom_id (для corner).
            detail_groups = {}  # geom_id → list of ops
            detail_order = []   # порядок вставки
            for op in group:
                if op.kind == OperationKind.CORNER_REWORK:
                    key = op.attributes.get('parent_geom_id', op.id)
                else:  # BLADE
                    key = op.geometry_ids[0] if op.geometry_ids else op.id
                if key not in detail_groups:
                    detail_groups[key] = []
                    detail_order.append(key)
                detail_groups[key].append(op)
            
            # Считаем длину каждой детали как СУММУ её ops
            detail_lengths = {
                k: sum(operation_geom_length(o, project) * passes_per_part 
                       for o in ops)
                for k, ops in detail_groups.items()
            }
            
            # Проходим детали в порядке (сохраняющем X-сортировку) и 
            # присваиваем part_idx по центру каждой ДЕТАЛИ, не отдельных ops.
            acc = 0.0
            for key in detail_order:
                dops = detail_groups[key]
                dlen = detail_lengths[key]
                center_pos = acc + dlen / 2.0
                part_idx = int(center_pos / clen * n_parts)
                part_idx = max(0, min(part_idx, n_parts - 1))
                # Все ops этой детали в одну программу
                for op in dops:
                    op.attributes['program_number'] = base_prog + 1 + part_idx
                acc += dlen
            
            prog_no = base_prog + n_parts
            cur_normal_prog = 0
            normal_acc = 0.0
        else:
            # Нормальный коридор: распределяем по программам равномерно.
            # Программа определяется по позиции середины коридора в нормальном
            # потоке: prog = floor((normal_acc + clen/2) / normal_total * N) + 1
            if n_normal_programs > 0 and normal_total > 0:
                center_pos = normal_acc + clen / 2.0
                local_prog = int(center_pos / normal_total * n_normal_programs) + 1
                local_prog = max(1, min(local_prog, n_normal_programs))
            else:
                local_prog = 1
            
            if local_prog != cur_normal_prog:
                # Новая программа
                cur_normal_prog = local_prog
                prog_no += 1
            
            for op in group:
                op.attributes['program_number'] = prog_no
            normal_acc += clen
    
    if prog_no == 0:
        prog_no = 1
    
    return prog_no
    
    return n_programs


# ─────────────────────────────────────────────────────────────────────────
#  ГРУППИРОВКА ПАР ВНЕШНИЙ+ВНУТРЕННИЙ
# ─────────────────────────────────────────────────────────────────────────

def group_blade_pairs(project: Project,
                      proximity: float = 5.0,
                      ) -> List[Tuple[Operation, ...]]:
    """Находит пары blade-операций (внешний и внутренний обход одного 
    контура) по близости центров. 
    
    Возвращает список кортежей. Если для операции нет пары — она идёт 
    в кортеж одна.
    
    Не модифицирует проект, только возвращает группы. Это полезно для
    UI ("показать парные пути") или для отдельной сортировки.
    """
    blade_ops = [op for op in project.operations
                 if op.kind == OperationKind.BLADE_FORMING]
    
    used = set()
    groups: List[Tuple[Operation, ...]] = []
    
    centers = {op.id: operation_center(op, project) for op in blade_ops}
    
    for i, op_a in enumerate(blade_ops):
        if op_a.id in used:
            continue
        partner = None
        ca = centers[op_a.id]
        for op_b in blade_ops[i+1:]:
            if op_b.id in used:
                continue
            cb = centers[op_b.id]
            if abs(ca[0] - cb[0]) <= proximity and abs(ca[1] - cb[1]) <= proximity:
                partner = op_b
                break
        used.add(op_a.id)
        if partner is not None:
            used.add(partner.id)
            groups.append((op_a, partner))
        else:
            groups.append((op_a,))
    
    return groups


# ─────────────────────────────────────────────────────────────────────────
#  АТРИБУТЫ-ПОМЕТКИ
# ─────────────────────────────────────────────────────────────────────────

def mark_operations(project: Project, key: str, value: object,
                    predicate: Callable[[Operation], bool] = lambda op: True) -> int:
    """Ставит атрибут key=value на все операции, удовлетворяющие predicate.
    
    Возвращает число изменённых операций.
    Аналог ATTR_REVERS=1 в макросах Альфакама.
    """
    n = 0
    for op in project.operations:
        if predicate(op):
            op.attributes[key] = value
            n += 1
    return n


def filter_by_attribute(project: Project, key: str, value: object) -> List[Operation]:
    """Возвращает операции с заданным значением атрибута."""
    return [op for op in project.operations if op.attributes.get(key) == value]
