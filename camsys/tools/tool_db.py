"""
Tool DB — описание фрез для CAM-системы.

Геометрия конусной (V-bit) фрезы:
    d_tip — диаметр у кончика (0, если кончик идеально острый)
    α     — полный угол при вершине, градусы (30, 45, 60, 90...)
    
    Радиус режущей кромки на высоте z от кончика:
        r(z) = d_tip/2 + z * tan(α/2)

Координатная система детали (зафиксировано):
    Z = 0  — поверхность подложки (где низ листа касается стола)
    Z = +H — верх листа (H = толщина листа)
    Кончик фрезы при работе располагается на Z = 0 (касается подложки).

При обточке боковой кромки металла фреза должна "обнимать" стенку
от Z=0 до Z=H. Максимальный радиус фрезы, который окажется внутри
толщины листа, — это радиус на высоте Z=H:
    r_eff = d_tip/2 + H * tan(α/2)

Это и есть смещение оси фрезы от номинального контура (XY-эквидистанта).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
import math
from typing import Optional


class ToolType(IntEnum):
    """Соответствует TPD(3) в посте AEC MTX:
       0 = стандартная (цилиндрическая)
       1 = 3D (конусная / V-bit)
      99 = MicroPerf (поворотный нож, ось V)
    """
    STANDARD = 0
    CONE_3D = 1
    MICROPERF = 99


@dataclass
class Tool:
    """Описание одной фрезы.
    
    Поля совместимы с диалогом Tool Editor в Альфакаме (см. скриншот):
        Tool Number, Offset Number, Length, Diameter,
        Shank Diameter, Taper Angle, End Diameter,
        Cut Depths (Depth of Cut, Maximum Depth),
        Spindle Speed, Fixed Feed, Fixed Down Feed.

    Параметры, которые передаются в .anc через TPD(N) — контроллер уточнит
    их лазерным измерителем перед обработкой:
        TPD(1) = tip_diameter   (диаметр/радиус кончика)
        TPD(2) = open_angle     (полный угол при вершине)
        TPD(3) = tool_type      (0=Std, 1=3D, 99=MicroPerf)
    """

    # ── идентификация ──
    number: int                         # Tool Number
    name: str                           # 'STD_D0_6BASE90°'
    offset_number: int = 0              # Offset Number
    cassette_slot: Optional[int] = None # ячейка в кассете

    # ── паспортная геометрия ──
    length: float = 0.0                 # Length, мм (общая длина инструмента)
    cutting_diameter: float = 3.0       # Diameter, макс. режущая часть
    shank_diameter: float = 3.175       # Shank Diameter
    tip_diameter: float = 0.1           # End Diameter (диаметр у кончика)
    open_angle: float = 30.0            # Полный угол при вершине (TPD(2))
    cutting_length: float = 6.0         # длина режущей части
    
    # ── Taper Angle из формы Альфакама ──
    # Внимание: в Альфакаме Taper Angle — это половина open_angle для
    # некоторых типов инструментов; чтобы не путаться, у нас open_angle —
    # ведущее поле, а taper_angle хранится как информация из формы.
    taper_angle: float = 0.0

    # ── глубины реза ──
    depth_of_cut: float = 0.0           # Depth of Cut
    maximum_depth: float = 0.0          # Maximum Depth

    # ── классификация для контроллера ──
    tool_type: ToolType = ToolType.CONE_3D

    # ── режимы (Feeds and Speeds, секция Fixed) ──
    spindle_rpm: int = 40000            # Spindle Speed (S)
    feed_cut: int = 300                 # Fixed Feed (рабочая XY, мм/мин)
    feed_plunge: int = 100              # Fixed Down Feed (врезание Z)

    # ── комментарий ──
    note: str = ""

    # ─────────────────────────────────────────────────────────────────────
    #  МАТЕМАТИКА КОНУСА
    # ─────────────────────────────────────────────────────────────────────

    @property
    def half_angle_rad(self) -> float:
        """Половина угла при вершине, в радианах."""
        return math.radians(self.open_angle / 2.0)

    @property
    def tan_half(self) -> float:
        """tan(α/2) — основной коэффициент перевода Z ↔ ширина."""
        return math.tan(self.half_angle_rad)

    def radius_at_height(self, z_from_tip: float) -> float:
        """Радиус режущей кромки на высоте z от кончика.
        
        Для конусной фрезы:   r = d_tip/2 + z · tan(α/2)
        Для цилиндрической:   r = cutting_diameter / 2  (не зависит от z)
        """
        if self.tool_type == ToolType.STANDARD or self.open_angle <= 0:
            return self.cutting_diameter / 2.0
        return self.tip_diameter / 2.0 + z_from_tip * self.tan_half

    def offset_for_sheet(self, sheet_thickness: float) -> float:
        """Эквидистанта оси фрезы от номинального контура для листа
        заданной толщины, при кончике на подложке (Z=0).

        Это максимальный радиус инструмента в пределах толщины листа:
        режущая кромка касается верхнего ребра стенки металла.
        """
        if self.tool_type == ToolType.STANDARD or self.open_angle <= 0:
            return self.cutting_diameter / 2.0
        return self.radius_at_height(sheet_thickness)

    def z_for_target_width(self, target_width: float) -> float:
        """Глубина (точнее, высота кончика над подложкой) для получения
        реза заданной ширины — режим V-гравировки на ровной поверхности.

        ВНИМАНИЕ: эта функция для V-гравировки сверху, не для обточки
        кромки травлёного листа (в нашем основном сценарии Z=0 всегда).
        Оставлено для будущих режимов и для тестов.

        width = d_tip + 2 · z · tan(α/2)   →   z = (width − d_tip) / (2·tan(α/2))
        """
        if self.tool_type == ToolType.STANDARD or self.open_angle <= 0:
            raise ValueError("z_for_target_width применима только к конусным фрезам")
        if target_width < self.tip_diameter:
            raise ValueError(
                f"Целевая ширина {target_width} меньше диаметра кончика {self.tip_diameter}"
            )
        return (target_width - self.tip_diameter) / (2.0 * self.tan_half)

    def width_at_z(self, z_from_tip: float) -> float:
        """Ширина реза на высоте z (обратная к z_for_target_width)."""
        return 2.0 * self.radius_at_height(z_from_tip)

    # ─────────────────────────────────────────────────────────────────────
    #  СОВМЕСТИМОСТЬ С ПОСТОМ AEC MTX
    # ─────────────────────────────────────────────────────────────────────

    def as_post_parameters(self) -> dict:
        """Возвращает параметры в формате, который ожидает .amp пост.

        В .amp код:
            SD.USR.ToolAdjust.ToolType         = TPD(3)
            SD.USR.ToolData.ProgToolEquidistant = TD/2
            SD.WZRec.UD.Ed[1].Geo.Rad           = TPD(1)/2
            SD.WZRec.UD.Ed[1].Geo.Ang           = TPD(2)
        """
        return {
            "T":      self.number,
            "TD":     self.cutting_diameter,
            "TPD(1)": self.tip_diameter,
            "TPD(2)": self.open_angle,
            "TPD(3)": int(self.tool_type),
            "S":      self.spindle_rpm,
        }


# ─────────────────────────────────────────────────────────────────────────
#  БАЗА ИНСТРУМЕНТОВ
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class ToolDB:
    """Коллекция инструментов с поиском по номеру/имени и подбором
    подходящего инструмента под геометрию участка пути."""

    tools: dict[int, Tool] = field(default_factory=dict)

    def add(self, tool: Tool) -> None:
        if tool.number in self.tools:
            raise ValueError(f"Инструмент T{tool.number} уже есть в базе")
        self.tools[tool.number] = tool

    def get(self, number: int) -> Tool:
        return self.tools[number]

    def by_name(self, name: str) -> Tool:
        for t in self.tools.values():
            if t.name == name:
                return t
        raise KeyError(name)

    def find_for_channel(self, channel_width: float, sheet_thickness: float
                        ) -> Optional[Tool]:
        """Найти самую крупную (для производительности) фрезу,
        которая помещается в канал заданной ширины при данной толщине листа.

        channel_width — минимальная ширина канала между соседними контурами.
        Условие прохождения: 2 · offset_for_sheet(H) ≤ channel_width.
        """
        suitable = [
            t for t in self.tools.values()
            if 2.0 * t.offset_for_sheet(sheet_thickness) <= channel_width
        ]
        if not suitable:
            return None
        # выбираем с максимальным offset (= самую крупную, но ещё проходящую)
        return max(suitable, key=lambda t: t.offset_for_sheet(sheet_thickness))


# ─────────────────────────────────────────────────────────────────────────
#  ДЕМО-БАЗА (заменить реальной от пользователя)
# ─────────────────────────────────────────────────────────────────────────

def demo_db() -> ToolDB:
    """Учебная база с типовыми V-bit'ами; финальные значения подставит
    пользователь после лазерного измерения на станке."""
    db = ToolDB()
    db.add(Tool(number=1,  name="V30_d010", cassette_slot=1,
                tip_diameter=0.10, open_angle=30.0, tool_type=ToolType.CONE_3D,
                feed_cut=400, spindle_rpm=40000,
                note="основная грубая, широкие участки"))
    db.add(Tool(number=2,  name="V30_d005", cassette_slot=2,
                tip_diameter=0.05, open_angle=30.0, tool_type=ToolType.CONE_3D,
                feed_cut=300, spindle_rpm=45000,
                note="тонкая, узкие места"))
    db.add(Tool(number=3,  name="V60_d010", cassette_slot=3,
                tip_diameter=0.10, open_angle=60.0, tool_type=ToolType.CONE_3D,
                feed_cut=500, spindle_rpm=40000,
                note="широкая, для скоростной обточки"))
    db.add(Tool(number=4,  name="V30_d002", cassette_slot=4,
                tip_diameter=0.02, open_angle=30.0, tool_type=ToolType.CONE_3D,
                feed_cut=200, spindle_rpm=50000,
                note="финишная, особо узкие места"))
    return db


# ─────────────────────────────────────────────────────────────────────────
#  ПАРСЕР ИМЁН АЛЬФАКАМА
# ─────────────────────────────────────────────────────────────────────────

import re as _re

_NAME_RE = _re.compile(
    r"^(?P<type>STD|3D|MP)_D(?P<dtip>\d+(?:_\d+)?)BASE(?P<angle>\d+(?:_\d+)?)°?$",
    _re.IGNORECASE,
)


def parse_alphacam_tool_name(name: str) -> Optional[dict]:
    """Парсит имя инструмента по соглашению Альфакама.
    
    Примеры:
        STD_D0_6BASE90°  → type=STD, tip_diameter=0.6, open_angle=90
        STD_D1_2BASE70°  → type=STD, tip_diameter=1.2, open_angle=70
        3D_D0_1BASE30°   → type=3D,  tip_diameter=0.1, open_angle=30
    
    В именах подчёркивание после D — это десятичный разделитель.
    
    Returns:
        dict с полями {type, tip_diameter, open_angle} или None.
    """
    m = _NAME_RE.match(name.strip())
    if not m:
        return None
    
    def to_float(s: str) -> float:
        return float(s.replace('_', '.'))
    
    type_str = m.group('type').upper()
    type_map = {
        'STD': ToolType.STANDARD,
        '3D':  ToolType.CONE_3D,
        'MP':  ToolType.MICROPERF,
    }
    
    return {
        'tool_type':    type_map.get(type_str, ToolType.STANDARD),
        'tip_diameter': to_float(m.group('dtip')),
        'open_angle':   to_float(m.group('angle')),
    }


def make_tool_from_alphacam_name(number: int, name: str, **kwargs) -> Tool:
    """Создаёт Tool из имени в стиле Альфакама + дополнительных параметров.
    
    Парсит имя для tip_diameter, open_angle, tool_type. Остальное берётся
    из kwargs или из значений по умолчанию.
    """
    parsed = parse_alphacam_tool_name(name)
    if parsed is None:
        # Имя не распознано — создаём с дефолтами, имя сохраняем как есть
        return Tool(number=number, name=name, **kwargs)
    
    # Объединяем парсенные параметры с пользовательскими (kwargs приоритетнее)
    params = {**parsed, **kwargs}
    return Tool(number=number, name=name, **params)
