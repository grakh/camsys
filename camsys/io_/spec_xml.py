"""Чтение specification_*.xml из папки заказа.

Структура папки заказа:
    <заказ>/
        maket/                  ← сюда кладётся .ai
        XML/
            specification_<номер>.xml   ← читаем отсюда
        -NC/                    ← сюда пишем .anc
        и т.д.

Из XML вытягиваем параметры ножа для авто-заполнения полей UI:
    <УголЗаточкиКромки>70</УголЗаточкиКромки>       → knife_angle (Угол)
    <ВысотаНожа>0.443</ВысотаНожа>                  → knife_height (Высота)

При успешном чтении соответствующие поля в UI подсвечиваются розовым, 
чтобы юзер видел откуда пришли значения. При загрузке следующего файла 
подсветка сбрасывается.
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET


def find_spec_xml(ai_path: Path) -> Optional[Path]:
    """Ищет specification_*.xml по структуре папки заказа.
    
    ai_path обычно лежит в `<заказ>/maket/<file>.ai`. Поднимаемся на 1 
    уровень (в папку заказа), ищем подпапку `XML/`, в ней первый 
    подходящий `specification_*.xml`. Если не найден — возвращаем None.
    """
    ai_path = Path(ai_path).resolve()
    if not ai_path.exists():
        return None
    
    # Поднимаемся к папке заказа: <заказ>/maket/<file>.ai → <заказ>
    order_dir = ai_path.parent.parent
    xml_dir = order_dir / "XML"
    if not xml_dir.is_dir():
        return None
    
    # Ищем specification_*.xml (case-insensitive)
    for candidate in xml_dir.iterdir():
        name_lower = candidate.name.lower()
        if (name_lower.startswith("specification_") 
                and name_lower.endswith(".xml")):
            return candidate
    return None


def read_spec_xml(xml_path: Path) -> dict:
    """Парсит XML спецификации, возвращает dict с найденными параметрами.
    
    Возвращаемый dict может содержать (если найдены в XML):
        knife_angle: str  — «60»/«70»/«80»/«90» (для QComboBox)
        knife_height: float — высота ножа в мм
        order_number: str — номер заказа
    
    При ошибках парсинга возвращает пустой dict. НЕ выбрасывает исключения.
    """
    result: dict = {}
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except Exception:
        return result
    
    # Номер заказа — атрибут корня
    order_num = root.get("НомерЗаказа") or root.get("НомерЗаказа".lower())
    if order_num:
        result['order_number'] = order_num
    
    # <УголЗаточкиКромки>70</УголЗаточкиКромки>
    angle_el = root.find("УголЗаточкиКромки")
    if angle_el is not None and angle_el.text:
        try:
            # Приводим к int, потом к строке — на случай если в XML "70.0"
            angle_int = int(float(angle_el.text.strip()))
            result['knife_angle'] = str(angle_int)
        except ValueError:
            pass
    
    # <ВысотаНожа>0.443</ВысотаНожа>
    height_el = root.find("ВысотаНожа")
    if height_el is not None and height_el.text:
        try:
            result['knife_height'] = float(height_el.text.strip())
        except ValueError:
            pass
    
    return result


def read_spec_for_ai(ai_path: Path) -> dict:
    """Комбинированный вызов: находит XML для .ai и парсит его.
    
    Возвращает пустой dict если XML не найден или не парсится.
    """
    xml_path = find_spec_xml(Path(ai_path))
    if xml_path is None:
        return {}
    return read_spec_xml(xml_path)
