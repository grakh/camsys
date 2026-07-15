"""Извлечение текстовых объектов из .ai (совместимо с PDF).

Adobe Illustrator сохраняет файлы как PDF-совместимые (`Create PDF-compatible 
file` при сохранении). Это позволяет читать из них текстовые объекты 
напрямую — без OCR по outlines.

Используется для СШИВОК (несколько заказов на одной плите): в макете 
рядом с каждым заказом напечатан его номер + метаданные (клиент, ротация,
диаметр вала). Модуль читает эти тексты вместе с их координатами, что 
позволяет автоматически связать «номер заказа → регион ножей на плите».

Зависимость: pymupdf (fitz). Устанавливается через `pip install pymupdf`.
Если библиотека отсутствует — функции возвращают пустые результаты, но не 
падают.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class TextItem:
    """Один текстовый фрагмент из .ai со всеми PDF-координатами.
    
    ВАЖНО: PDF использует Y-down (0 наверху страницы), а Illustrator/наш 
    проект — Y-up (0 внизу). Для сопоставления с координатами ножей нужно 
    переворачивать Y через: y_ai = page_height - y_pdf.
    """
    text: str
    x0: float  # PDF-координаты, единицы = points (1/72 дюйма)
    y0: float
    x1: float
    y1: float
    page_num: int = 0
    
    @property
    def cx(self) -> float:
        """Центр bbox по X."""
        return (self.x0 + self.x1) / 2.0
    
    @property
    def cy(self) -> float:
        """Центр bbox по Y (в PDF-координатах, Y-down)."""
        return (self.y0 + self.y1) / 2.0
    
    @property
    def width(self) -> float:
        return self.x1 - self.x0
    
    @property
    def height(self) -> float:
        return self.y1 - self.y0


def extract_text_items(ai_path: Path) -> List[TextItem]:
    """Извлекает все текстовые фрагменты из .ai с их bbox'ами.
    
    Возвращает список TextItem или пустой список если:
        - pymupdf не установлен
        - файл не читается как PDF
        - в файле нет текстовых объектов (например, весь текст 
          convert-to-outlines в Illustrator)
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        return []
    
    try:
        doc = fitz.open(str(ai_path))
    except Exception:
        return []
    
    items: List[TextItem] = []
    try:
        for page_num, page in enumerate(doc):
            # get_text('words') возвращает список кортежей:
            # (x0, y0, x1, y1, text, block_no, line_no, word_no)
            for w in page.get_text('words'):
                if len(w) < 5:
                    continue
                x0, y0, x1, y1, txt = w[0], w[1], w[2], w[3], w[4]
                if not txt or not txt.strip():
                    continue
                items.append(TextItem(
                    text=txt.strip(),
                    x0=float(x0), y0=float(y0),
                    x1=float(x1), y1=float(y1),
                    page_num=page_num,
                ))
    finally:
        doc.close()
    return items


def get_page_size(ai_path: Path) -> Optional[tuple]:
    """Возвращает (width, height) первой страницы в PDF-точках.
    
    Нужно для конвертации PDF-Y-down в .ai-Y-up:
        y_ai = page_height - y_pdf
    Возвращает None если pymupdf отсутствует или файл не читается.
    """
    try:
        import fitz
        doc = fitz.open(str(ai_path))
        try:
            page = doc[0]
            return (page.rect.width, page.rect.height)
        finally:
            doc.close()
    except Exception:
        return None


def find_text_matching(items: List[TextItem], patterns: List[str]) -> List[TextItem]:
    """Фильтрует список TextItem'ов по вхождению любого из шаблонов.
    
    Например, для номеров заказов из имени сшивки 
    `41000_121554_121561_121555_` → передать patterns=['121554', '121561', 
    '121555'] и получить фрагменты только с этими номерами.
    """
    if not patterns:
        return []
    result = []
    for it in items:
        for pat in patterns:
            if pat in it.text:
                result.append(it)
                break
    return result


def parse_stitch_filename(filename: str) -> Optional[dict]:
    """Разбирает имя файла сшивки `<стичка>_<заказ1>_<заказ2>_..._.ai`.
    
    Args:
        filename: имя файла (с расширением или без), например 
                  '41000_121554_121561_121555_.ai'
    
    Returns:
        dict со структурой:
            {'stitch': '41000', 'orders': ['121554', '121561', '121555']}
        или None если имя не подходит под шаблон (одиночный заказ).
    """
    stem = Path(filename).stem  # без расширения
    # Убираем trailing underscore если есть  
    stem = stem.rstrip('_')
    parts = stem.split('_')
    # Все части должны быть числовыми (номера)
    numeric = [p for p in parts if p.isdigit()]
    if len(numeric) < 2:
        return None  # это одиночный заказ, не сшивка
    return {
        'stitch': numeric[0],
        'orders': numeric[1:],
    }
