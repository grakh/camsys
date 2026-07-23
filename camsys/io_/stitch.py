"""Анализ сшивок — макетов с несколькими заказами на одной плите.

Работает так:
    1. Из имени файла берётся номер сшивки и список заказов:
       `40991_121532_121528_121533_121541_121543_.ai`
       → stitch=40991, orders=[121532, 121528, 121533, 121541, 121543]
    
    2. Из слоя `namber` (розовые линии рамок) детектятся регионы — 
       прямоугольные области, разделяющие ножи по заказам.
    
    3. Из текстовых объектов .ai (через pymupdf) берутся позиции 
       напечатанных номеров заказов.
    
    4. Автоматически сопоставляется «номер → регион» по попаданию 
       PDF-координат номера в регион (после конвертации PDF→.ai).
    
    5. Ножи и реперы распределяются по регионам: каждый нож / репер 
       принадлежит региону, в чей bbox попал центр.

Результат — `StitchInfo` со всеми группировками, который используется 
в UI (dropdown заказов) и при экспорте (фильтр по заказу).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .pdf_text import (
    TextItem, extract_text_items, get_page_size, 
    find_text_matching, parse_stitch_filename,
)


# Типы для аннотаций
BBox = Tuple[float, float, float, float]  # (x0, y0, x1, y1)


@dataclass
class OrderRegion:
    """Один регион на плите — обычно = один заказ.
    
    Определяется по прямоугольнику из линий слоя `namber`. Содержит 
    номер заказа (если удалось сопоставить), ID ножей и реперов 
    внутри области.
    """
    bbox: BBox                    # прямоугольник региона в .ai-координатах (мм)
    order_number: Optional[str] = None  # номер заказа (из имени файла)
    knife_ids: List[str] = field(default_factory=list)
    fiducial_ids: List[str] = field(default_factory=list)
    info_text: str = ""           # доп. инфа из PDF-текста (клиент, ротация)
    
    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2.0
    
    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2.0
    
    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]
    
    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]
    
    def contains(self, x: float, y: float) -> bool:
        return (self.bbox[0] <= x <= self.bbox[2] 
                and self.bbox[1] <= y <= self.bbox[3])


@dataclass
class StitchInfo:
    """Полная информация о сшивке."""
    stitch_number: str            # '40991'
    orders: List[str]             # ['121532', '121528', ...] из имени файла
    regions: List[OrderRegion] = field(default_factory=list)
    ai_path: Optional[Path] = None
    
    def get_region_by_order(self, order_key: str) -> Optional[OrderRegion]:
        """Найти регион по номеру заказа или композитному ключу «N#i».

        Ключ «N#i» — i-я копия (0-based) заказа N в сшивке. Нужен, когда
        один и тот же заказ размножен несколькими копиями и у нескольких
        регионов совпадает `order_number`. Ключ без «#» — обратная
        совместимость: возвращает первый регион с таким номером.
        """
        if order_key and '#' in order_key:
            base, suffix = order_key.split('#', 1)
            try:
                n = int(suffix)
            except ValueError:
                return None
            matches = [r for r in self.regions if r.order_number == base]
            if 0 <= n < len(matches):
                return matches[n]
            return None
        for r in self.regions:
            if r.order_number == order_key:
                return r
        return None
    
    def get_region_for_knife(self, knife_id: str) -> Optional[OrderRegion]:
        for r in self.regions:
            if knife_id in r.knife_ids:
                return r
        return None


def order_key_to_filename(order_key: str) -> str:
    """Преобразовать композитный ключ заказа в файловое имя.

    "121254"    → "121254"       (уникальный номер)
    "121254#0"  → "121254"       (первая копия — без суффикса, чтобы имена
                                  папок/файлов не менялись у уникальных)
    "121254#1"  → "121254_c2"    (2-я копия)
    "121254#2"  → "121254_c3"    (3-я копия)

    Символ «#» в путях местами работает, местами нет (некоторые
    контроллеры/скрипты обрезают всё после «#»), поэтому в файловых именах
    используем «_cN». Индексация в имени 1-based, чтобы оператору читалось
    как «копия 1/2/3».
    """
    if not order_key or '#' not in order_key:
        return order_key or ''
    base, suffix = order_key.split('#', 1)
    try:
        n = int(suffix)
    except ValueError:
        return base
    if n == 0:
        return base
    return f"{base}_c{n + 1}"


# ─────────────────────────────────────────────────────────────────────
#  ДЕТЕКЦИЯ РЕГИОНОВ
# ─────────────────────────────────────────────────────────────────────

def _detect_regions_from_namber(namber_layer) -> List[BBox]:
    """Извлекает прямоугольные регионы из линий слоя `namber`.
    
    На макетах сшивок регионы отделены розовыми линиями (обычно 
    вертикальные + горизонтальные). Собираем bbox'ы линий и группируем
    в прямоугольники.
    
    Алгоритм:
        - Каждый геометрический объект слоя = 1 линия (вертикальная 
          или горизонтальная).
        - Ищем пары противоположных линий (2 вертикальных с общим Y 
          диапазоном, или 2 горизонтальных с общим X) — они образуют 
          рамку.
        - Из всех пар выделяем прямоугольники (bbox из 2-4 линий).
    
    Возвращает список bbox'ов (может быть меньше числа заказов, если 
    рамки неполные — но каждый прямоугольник валиден).
    """
    if not namber_layer or not namber_layer.geometries:
        return []
    
    # Собираем линии
    v_lines = []  # вертикальные: (x, y_min, y_max)
    h_lines = []  # горизонтальные: (y, x_min, x_max)
    
    for g in namber_layer.geometries:
        if not g.polypath.segments:
            continue
        xs = [p[0] for s in g.polypath.segments for p in [s.a, s.b]]
        ys = [p[1] for s in g.polypath.segments for p in [s.a, s.b]]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        dx, dy = x_max - x_min, y_max - y_min
        
        # Вертикальная линия: dx << dy
        if dx < 1.0 and dy > 10.0:
            v_lines.append((x_min, y_min, y_max))
        # Горизонтальная: dy << dx
        elif dy < 1.0 and dx > 10.0:
            h_lines.append((y_min, x_min, x_max))
    
    # Ищем пары V-линий с общим Y-диапазоном (образуют вертикальную рамку)
    regions = []
    used_v = set()
    for i, (x1, y1a, y1b) in enumerate(v_lines):
        if i in used_v:
            continue
        for j, (x2, y2a, y2b) in enumerate(v_lines):
            if j <= i or j in used_v:
                continue
            # Пересечение по Y должно быть значительным
            y_lo = max(y1a, y2a)
            y_hi = min(y1b, y2b)
            if y_hi - y_lo < 10.0:
                continue
            # X-расстояние должно быть > 0
            if abs(x1 - x2) < 20.0:
                continue
            # Прямоугольник
            bbox = (min(x1, x2), y_lo, max(x1, x2), y_hi)
            regions.append(bbox)
            used_v.add(i)
            used_v.add(j)
            break
    
    # Ищем пары H-линий с общим X-диапазоном (образуют горизонтальную рамку)
    used_h = set()
    for i, (y1, x1a, x1b) in enumerate(h_lines):
        if i in used_h:
            continue
        for j, (y2, x2a, x2b) in enumerate(h_lines):
            if j <= i or j in used_h:
                continue
            x_lo = max(x1a, x2a)
            x_hi = min(x1b, x2b)
            if x_hi - x_lo < 10.0:
                continue
            if abs(y1 - y2) < 20.0:
                continue
            bbox = (x_lo, min(y1, y2), x_hi, max(y1, y2))
            regions.append(bbox)
            used_h.add(i)
            used_h.add(j)
            break
    
    return regions


# ─────────────────────────────────────────────────────────────────────
#  СОПОСТАВЛЕНИЕ НОМЕРОВ И РЕГИОНОВ
# ─────────────────────────────────────────────────────────────────────

def _compute_pdf_to_ai_scale(ai_path: Path, project) -> Optional[float]:
    """Вычисляет масштаб для конвертации PDF-координат в .ai-мм.
    
    Использует отношение размеров: max_x в ножах / max_x в PDF-странице.
    Работает если ножи покрывают всю ширину плиты.
    """
    size = get_page_size(ai_path)
    if not size:
        return None
    pdf_w = size[0]
    
    knife_layer = project.get_layer_by_name("Knife")
    if not knife_layer:
        return None
    
    max_x = 0.0
    for g in knife_layer.geometries:
        for s in g.polypath.segments:
            for pt in [s.a, s.b]:
                if pt[0] > max_x:
                    max_x = pt[0]
    if max_x <= 0:
        return None
    return pdf_w / max_x


def _match_orders_to_regions(regions: List[BBox], text_items: List[TextItem],
                             orders: List[str], scale: float, 
                             page_h: float) -> Dict[int, str]:
    """Сопоставляет каждый регион номеру заказа через PDF-текст.
    
    Args:
        regions: список bbox'ов регионов (в .ai-координатах)
        text_items: все текстовые фрагменты из PDF
        orders: список номеров заказов из имени файла
        scale: PDF-точек на .ai-мм
        page_h: высота PDF-страницы (для Y-flip)
    
    Returns:
        {region_index: order_number}
    """
    result: Dict[int, str] = {}
    order_items = find_text_matching(text_items, orders)
    
    # ЭТАП 1: точный point-in-bbox матчинг
    # ВАЖНО: НЕ блокируем повторные вхождения одного и того же номера.
    # Один заказ может лежать в сшивке несколькими КОПИЯМИ — тогда
    # номер отпечатан столько же раз, и каждая копия должна привязаться
    # к своему региону. Регионы, наоборот, каждый может быть занят только
    # одним номером (в списке `result` ключ = idx региона, уникален).
    unmatched_items = []
    for it in order_items:
        if not it.text.isdigit() or it.text not in orders:
            continue
        # PDF → .ai координаты
        x_ai = it.cx / scale
        y_ai = (page_h - it.cy) / scale
        # Ищем в какой регион попал центр
        matched = False
        for idx, bbox in enumerate(regions):
            if idx in result:
                continue
            x0, y0, x1, y1 = bbox
            if x0 <= x_ai <= x1 and y0 <= y_ai <= y1:
                result[idx] = it.text
                matched = True
                break
        if not matched:
            unmatched_items.append((it, x_ai, y_ai))

    # ЭТАП 2: fuzzy fallback — оставшиеся номера привязываем к
    # ближайшему НЕ сопоставленному региону по расстоянию до центра.
    # Работает если номер отпечатан РЯДОМ с регионом (за его bbox),
    # например над рамкой или сбоку. Тогда точный in-bbox не срабатывает,
    # но ближайший центр региона однозначно определяет привязку.
    for it, x_ai, y_ai in unmatched_items:
        best_idx = None
        best_dist = float('inf')
        for idx, bbox in enumerate(regions):
            if idx in result:
                continue  # регион уже занят
            cx = (bbox[0] + bbox[2]) / 2.0
            cy = (bbox[1] + bbox[3]) / 2.0
            dist = ((cx - x_ai)**2 + (cy - y_ai)**2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_idx = idx
        if best_idx is not None:
            result[best_idx] = it.text

    return result


# ─────────────────────────────────────────────────────────────────────
#  ОСНОВНАЯ ФУНКЦИЯ АНАЛИЗА
# ─────────────────────────────────────────────────────────────────────

def analyze_stitch(ai_path: Path, project) -> Optional[StitchInfo]:
    """Полный анализ сшивки: имя, регионы, привязка ножей/реперов.
    
    Возвращает StitchInfo или None если файл — не сшивка (одиночный 
    заказ по имени).
    
    project — уже загруженный Project с ножами и реперами.
    """
    ai_path = Path(ai_path)
    
    # 1. Разбор имени
    name_info = parse_stitch_filename(ai_path.name)
    if not name_info:
        return None  # одиночный заказ
    
    info = StitchInfo(
        stitch_number=name_info['stitch'],
        orders=name_info['orders'],
        ai_path=ai_path,
    )
    
    # 2. Регионы из слоя namber
    namber_layer = project.get_layer_by_name("namber")
    if not namber_layer:
        # Пробуем альтернативные имена
        for alt in ("number", "номер", "regions"):
            namber_layer = project.get_layer_by_name(alt)
            if namber_layer:
                break
    
    region_bboxes = _detect_regions_from_namber(namber_layer) if namber_layer else []
    
    # Создаём OrderRegion для каждой bbox
    for bbox in region_bboxes:
        info.regions.append(OrderRegion(bbox=bbox))
    
    # 3. Сопоставление номеров → регионы через PDF-текст
    scale = _compute_pdf_to_ai_scale(ai_path, project)
    page_size = get_page_size(ai_path)
    
    if scale and page_size and info.regions:
        text_items = extract_text_items(ai_path)
        matches = _match_orders_to_regions(
            region_bboxes, text_items, info.orders, scale, page_size[1]
        )
        for region_idx, order_num in matches.items():
            info.regions[region_idx].order_number = order_num
    
    # 4. Распределение ножей и реперов по регионам
    knife_layer = project.get_layer_by_name("Knife")
    if knife_layer:
        for g in knife_layer.geometries:
            if not g.polypath.segments:
                continue
            xs = [p[0] for s in g.polypath.segments for p in [s.a, s.b]]
            ys = [p[1] for s in g.polypath.segments for p in [s.a, s.b]]
            cx = sum(xs) / len(xs)
            cy = sum(ys) / len(ys)
            for r in info.regions:
                if r.contains(cx, cy):
                    r.knife_ids.append(g.id)
                    break
    
    for f in project.fiducials:
        best_r = None
        best_dist = float('inf')
        for r in info.regions:
            if r.contains(f.x, f.y):
                best_r = r
                break
            # Если репер вне региона — привязываем к ближайшему
            dx = max(r.bbox[0] - f.x, 0, f.x - r.bbox[2])
            dy = max(r.bbox[1] - f.y, 0, f.y - r.bbox[3])
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_r = r
        if best_r is not None:
            best_r.fiducial_ids.append(f.id)
    
    return info
