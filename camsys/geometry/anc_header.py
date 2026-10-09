"""Разбор параметров прогона из заголовка .anc (v1.7.28).

Зачем. При сверке с эталонными файлами параметры фрезы приходится знать
точно: эквидистанта входит во все расчёты — проходимость, узкие места,
пороги углов. Брать их из настроек по умолчанию нельзя: эталонный файл
мог быть сделан на другой фрезе, и тогда все замеры уедут.

Именно так и вышло при разборе 124173_test.anc: параметры были взяты из
camsys_settings.json (угол 70, ABS 0.19), а файл сделан на угле 80 и
ABS 0.25. Эквидистанта 0.533 вместо 0.6098 — и три гипотезы подряд
проверялись на неверных числах. При этом всё нужное лежало в первых
тридцати строках самого файла.

Что читаем:
    N26 SSDE[SD.USR.ProcessData.ProgZDepth = ABS(0.25)]      → ABS
    N28 SSDE[SD.USR.ProcessData.ProgDieHeight = 0.44]        → высота
    N29 SSDE[SD.USR.Allign.DistC2C = 700]                    → 2-я реперная
    N44 CLN(DLA80)                                           → угол 80
"""

from __future__ import annotations
from typing import Optional
from dataclasses import dataclass
import math
import re

_NUM = r'[-+]?\d*\.?\d+'


@dataclass
class AncHeader:
    """Параметры прогона, вычитанные из заголовка .anc."""
    knife_angle: Optional[float] = None      # градусы, из CLN(DLA…)
    bottom: Optional[float] = None           # ABS, мм
    die_height: Optional[float] = None       # высота ножа, мм
    fiducial_distance: Optional[float] = None
    program_name: Optional[str] = None

    def tool_equidistant(self, tip_diameter: float) -> Optional[float]:
        """Эквидистанта (ширина реза) для заданной пятки.

        Пятка в заголовке не пишется — её задаёт оператор. Остальное
        берётся из файла.
        """
        if self.knife_angle is None or self.bottom is None:
            return None
        return tip_diameter + 2.0 * self.bottom * math.tan(
            math.radians(self.knife_angle / 2.0))

    def tool_offset(self, tip_diameter: float) -> Optional[float]:
        """Половина ширины реза — то, на что смещается компенсация."""
        eq = self.tool_equidistant(tip_diameter)
        return None if eq is None else eq / 2.0


def parse_anc_header(text: str, max_lines: int = 200) -> AncHeader:
    """Вычитывает параметры прогона из начала .anc.

    Args:
        text: содержимое файла
        max_lines: сколько строк от начала просматривать. Заголовок
            короткий, а дальше идут десятки тысяч строк движений —
            читать их незачем.
    """
    head = text.splitlines()[:max_lines]
    out = AncHeader()
    for line in head:
        if out.bottom is None:
            m = re.search(r'ProgZDepth\s*=\s*ABS\(\s*(' + _NUM + r')\s*\)', line)
            if m:
                out.bottom = float(m.group(1))
        if out.die_height is None:
            m = re.search(r'ProgDieHeight\s*=\s*(' + _NUM + r')', line)
            if m:
                out.die_height = float(m.group(1))
        if out.fiducial_distance is None:
            m = re.search(r'DistC2C\s*=\s*(' + _NUM + r')', line)
            if m:
                out.fiducial_distance = float(m.group(1))
        if out.knife_angle is None:
            m = re.search(r'CLN\(\s*DLA(\d+)\s*\)', line)
            if m:
                out.knife_angle = float(m.group(1))
        if out.program_name is None:
            m = re.search(r'PrgName\s*=\s*"([^"]+)"', line)
            if m:
                out.program_name = m.group(1)
    return out


def describe_anc(text: str, tip_diameter: float = 0.8) -> str:
    """Человекочитаемая сводка параметров — для логов и отладки."""
    h = parse_anc_header(text)
    eq = h.tool_equidistant(tip_diameter)
    off = h.tool_offset(tip_diameter)
    parts = [f"программа {h.program_name or '?'}"]
    parts.append(f"угол {h.knife_angle if h.knife_angle is not None else '?'}")
    parts.append(f"ABS {h.bottom if h.bottom is not None else '?'}")
    parts.append(f"высота {h.die_height if h.die_height is not None else '?'}")
    if eq is not None:
        parts.append(f"эквидистанта {off:.4f} (рез {eq:.4f}) "
                     f"при пятке {tip_diameter}")
    return ", ".join(parts)
