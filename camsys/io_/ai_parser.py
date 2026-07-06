"""
io_/ai_parser.py — извлечение слоёв и путей из Adobe Illustrator .ai (PDF-формат).

Поддерживается:
- именованные слои через /OCG (OCProperties)
- операторы пути: m, l, c, v, y, h, re
- save/restore state: q, Q
- трансформация: cm
- завершители пути: S, s, f, F, f*, B, B*, b, b*, n

Координаты возвращаются в МИЛЛИМЕТРАХ (PDF points × 25.4/72).
Y-ось PDF идёт ВВЕРХ (как в Illustrator) — это совпадает с CNC (Y вверх).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
import re
import pikepdf

PT_TO_MM = 25.4 / 72.0


# ─────────────────────────────────────────────────────────────────────────
#  ГЕОМЕТРИЯ
# ─────────────────────────────────────────────────────────────────────────

Point = Tuple[float, float]


@dataclass
class Matrix:
    """Аффинная матрица 2D: [a b 0; c d 0; e f 1]."""
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def mul(self, o: "Matrix") -> "Matrix":
        """self ∘ o → self применяется ПОСЛЕ o (PDF convention для cm)."""
        return Matrix(
            self.a*o.a + self.b*o.c,
            self.a*o.b + self.b*o.d,
            self.c*o.a + self.d*o.c,
            self.c*o.b + self.d*o.d,
            self.e*o.a + self.f*o.c + o.e,
            self.e*o.b + self.f*o.d + o.f,
        )

    def xform(self, x: float, y: float) -> Point:
        return (self.a*x + self.c*y + self.e,
                self.b*x + self.d*y + self.f)


@dataclass
class PathSegment:
    """Один сегмент пути: 'M' (moveto), 'L' (lineto), 'C' (curveto: 3 контр.точки), 'Z' (close)."""
    op: str
    points: Tuple[Point, ...] = ()


@dataclass
class Path:
    """Один путь — список сегментов."""
    segments: List[PathSegment] = field(default_factory=list)

    def is_closed(self) -> bool:
        return any(s.op == 'Z' for s in self.segments)

    def start_point(self) -> Optional[Point]:
        for s in self.segments:
            if s.op == 'M':
                return s.points[0]
        return None

    def all_anchors(self) -> List[Point]:
        """Только опорные точки (без контрольных точек Безье)."""
        result = []
        for s in self.segments:
            if s.op in ('M', 'L'):
                result.append(s.points[0])
            elif s.op == 'C':
                result.append(s.points[2])
        return result

    def bbox(self) -> Tuple[float, float, float, float]:
        """Грубый bbox по опорным и контрольным точкам Безье (без точного решения)."""
        pts: List[Point] = []
        for s in self.segments:
            pts.extend(s.points)
        if not pts:
            return (0, 0, 0, 0)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))


# ─────────────────────────────────────────────────────────────────────────
#  ПАРСЕР PDF CONTENT STREAM
# ─────────────────────────────────────────────────────────────────────────

# Токены: число, имя (/Name), оператор (буквы + опц. * или *)
_TOK_RE = re.compile(
    r'(-?\d+\.\d+|-?\.\d+|-?\d+\.?|/[A-Za-z0-9_]+|[A-Za-z]+[*]?|\*)'
)


def _tokenize(stream: str) -> List[str]:
    """Делит PDF content stream на токены. Пропускает строки/массивы 
    (для слоёв с путями не встречаются)."""
    return _TOK_RE.findall(stream)


def parse_paths(stream: str, units_to_mm: float = PT_TO_MM) -> List[Path]:
    """Парсит PDF content stream → список замкнутых/открытых путей.
    
    Все координаты приводятся к мм (через units_to_mm).
    Учитывает стек графического состояния (q/Q) и трансформации (cm).
    """
    tokens = _tokenize(stream)
    paths: List[Path] = []
    
    ctm_stack: List[Matrix] = [Matrix()]
    cur_path: Optional[List[PathSegment]] = None
    cur_x, cur_y = 0.0, 0.0  # текущая точка в user-space (до cm)
    operands: List[float] = []

    def ctm() -> Matrix:
        return ctm_stack[-1]

    def to_global(x: float, y: float) -> Point:
        gx, gy = ctm().xform(x, y)
        return (gx * units_to_mm, gy * units_to_mm)

    for tk in tokens:
        # число?
        if (tk[0].isdigit() or tk[0] == '-' or tk[0] == '.'):
            try:
                operands.append(float(tk))
                continue
            except ValueError:
                pass
        
        # имена (/Name) — просто игнорируем как операнд-метки
        if tk.startswith('/'):
            continue

        op = tk

        # ── состояние графики ──
        if op == 'q':
            ctm_stack.append(Matrix(ctm().a, ctm().b, ctm().c, ctm().d, ctm().e, ctm().f))
            operands.clear()
        elif op == 'Q':
            if len(ctm_stack) > 1:
                ctm_stack.pop()
            operands.clear()
        elif op == 'cm':
            if len(operands) >= 6:
                a, b, c, d, e, f = operands[-6:]
                # PDF semantics: новая CTM = (this cm) ∘ (CTM)
                ctm_stack[-1] = Matrix(a, b, c, d, e, f).mul(ctm_stack[-1])
            operands.clear()
        
        # ── пути ──
        elif op == 'm':
            if len(operands) >= 2:
                x, y = operands[-2:]
                cur_x, cur_y = x, y
                cur_path = [PathSegment('M', (to_global(x, y),))]
            operands.clear()
        elif op == 'l':
            if len(operands) >= 2 and cur_path is not None:
                x, y = operands[-2:]
                cur_path.append(PathSegment('L', (to_global(x, y),)))
                cur_x, cur_y = x, y
            operands.clear()
        elif op == 'c':
            if len(operands) >= 6 and cur_path is not None:
                x1, y1, x2, y2, x3, y3 = operands[-6:]
                cur_path.append(PathSegment('C', (
                    to_global(x1, y1),
                    to_global(x2, y2),
                    to_global(x3, y3),
                )))
                cur_x, cur_y = x3, y3
            operands.clear()
        elif op == 'v':
            # v: x2 y2 x3 y3 → c с x1=current, y1=current
            if len(operands) >= 4 and cur_path is not None:
                x2, y2, x3, y3 = operands[-4:]
                cur_path.append(PathSegment('C', (
                    to_global(cur_x, cur_y),
                    to_global(x2, y2),
                    to_global(x3, y3),
                )))
                cur_x, cur_y = x3, y3
            operands.clear()
        elif op == 'y':
            # y: x1 y1 x3 y3 → c с x2=x3, y2=y3
            if len(operands) >= 4 and cur_path is not None:
                x1, y1, x3, y3 = operands[-4:]
                cur_path.append(PathSegment('C', (
                    to_global(x1, y1),
                    to_global(x3, y3),
                    to_global(x3, y3),
                )))
                cur_x, cur_y = x3, y3
            operands.clear()
        elif op == 'h':
            if cur_path is not None:
                cur_path.append(PathSegment('Z'))
            operands.clear()
        elif op == 're':
            # rectangle: x y w h
            if len(operands) >= 4:
                x, y, w, h = operands[-4:]
                cur_path = [
                    PathSegment('M', (to_global(x, y),)),
                    PathSegment('L', (to_global(x+w, y),)),
                    PathSegment('L', (to_global(x+w, y+h),)),
                    PathSegment('L', (to_global(x, y+h),)),
                    PathSegment('Z'),
                ]
                cur_x, cur_y = x, y+h
            operands.clear()
        
        # ── завершители пути ──
        elif op in ('S', 's', 'f', 'F', 'f*', 'B', 'B*', 'b', 'b*', 'n'):
            if cur_path is not None and len(cur_path) > 1:
                paths.append(Path(cur_path))
            cur_path = None
            operands.clear()
        
        # ── всё остальное — операнды сбрасываем ──
        else:
            operands.clear()

    return paths


# ─────────────────────────────────────────────────────────────────────────
#  ИЗВЛЕЧЕНИЕ СЛОЁВ
# ─────────────────────────────────────────────────────────────────────────

def _extract_oc_block(text: str, mc_ref: str) -> Optional[str]:
    """Извлекает блок /OC /MCx BDC ... EMC с учётом вложенности."""
    pat = re.compile(r'/OC\s+' + re.escape(mc_ref) + r'\s+BDC\b')
    m = pat.search(text)
    if not m:
        return None
    start = m.end()
    depth = 1
    i = start
    bdc_re = re.compile(r'\bBDC\b')
    emc_re = re.compile(r'\bEMC\b')
    while i < len(text) and depth > 0:
        bdc_m = bdc_re.search(text, i)
        emc_m = emc_re.search(text, i)
        if emc_m is None:
            return None
        if bdc_m is not None and bdc_m.start() < emc_m.start():
            depth += 1
            i = bdc_m.end()
        else:
            depth -= 1
            i = emc_m.end()
    return text[start:i-3]


def list_layers(ai_path: str) -> List[str]:
    """Возвращает имена всех слоёв (OCG) в .ai файле."""
    pdf = pikepdf.open(ai_path)
    root = pdf.Root
    if '/OCProperties' not in root:
        return []
    ocp = root['/OCProperties']
    if '/OCGs' not in ocp:
        return []
    return [str(ocg.get('/Name', '')) for ocg in ocp['/OCGs']]


def get_layer_paths(ai_path: str, layer_name: str) -> List[Path]:
    """Возвращает все пути слоя с указанным именем, в миллиметрах."""
    pdf = pikepdf.open(ai_path)
    page = pdf.pages[0]
    
    # Найти короткое имя /MCx, соответствующее слою
    res = page['/Resources']
    if '/Properties' not in res:
        raise ValueError("В Resources нет /Properties — слои не определены")
    
    mc_ref = None
    for short, obj in res['/Properties'].items():
        try:
            if str(obj.get('/Name', '')) == layer_name:
                mc_ref = short
                break
        except Exception:
            continue
    
    if mc_ref is None:
        available = list_layers(ai_path)
        raise ValueError(f"Слой '{layer_name}' не найден. Доступные: {available}")
    
    # Извлечь содержимое контента и распарсить
    raw = page['/Contents'].read_bytes().decode('latin-1')
    block = _extract_oc_block(raw, mc_ref)
    if block is None:
        return []
    
    return parse_paths(block)
