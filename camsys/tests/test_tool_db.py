"""Тесты математики конусной фрезы — без них в микронах легко ошибиться."""

import math
import sys
import os

# Добавим путь к проекту
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tool_db import Tool, ToolType, ToolDB, demo_db


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) < tol


# ─────────────────────────────────────────────────────────────────────────
#  1. Геометрия конуса: радиус на высоте
# ─────────────────────────────────────────────────────────────────────────

def test_radius_at_tip():
    """В точке z=0 (на самом кончике) радиус = tip_diameter/2."""
    t = Tool(number=1, name="t", tip_diameter=0.10, open_angle=30,
             tool_type=ToolType.CONE_3D)
    assert approx(t.radius_at_height(0.0), 0.05)


def test_radius_at_1mm_30deg():
    """V30, d_tip=0.1, на высоте 1 мм:
       r = 0.05 + 1.0 · tan(15°) = 0.05 + 0.26794919... = 0.31794919..."""
    t = Tool(number=1, name="t", tip_diameter=0.10, open_angle=30,
             tool_type=ToolType.CONE_3D)
    expected = 0.05 + math.tan(math.radians(15))
    assert approx(t.radius_at_height(1.0), expected)
    # дополнительно: точное численное значение для документации
    assert abs(t.radius_at_height(1.0) - 0.31794919243112270) < 1e-12


def test_radius_at_1mm_60deg():
    """V60, d_tip=0.1, на высоте 1 мм:
       r = 0.05 + tan(30°) = 0.05 + 0.5773502691... = 0.6273502691..."""
    t = Tool(number=2, name="t", tip_diameter=0.10, open_angle=60,
             tool_type=ToolType.CONE_3D)
    assert abs(t.radius_at_height(1.0) - 0.6273502691896258) < 1e-12


def test_radius_at_1mm_90deg():
    """V90: tan(45°) = 1, r = d_tip/2 + 1·z."""
    t = Tool(number=3, name="t", tip_diameter=0.10, open_angle=90,
             tool_type=ToolType.CONE_3D)
    assert approx(t.radius_at_height(1.0), 1.05)


def test_radius_cylindrical():
    """Цилиндрическая фреза — радиус не зависит от z."""
    t = Tool(number=4, name="t", cutting_diameter=2.0, open_angle=0,
             tool_type=ToolType.STANDARD)
    assert approx(t.radius_at_height(0.0), 1.0)
    assert approx(t.radius_at_height(0.5), 1.0)
    assert approx(t.radius_at_height(1.0), 1.0)


# ─────────────────────────────────────────────────────────────────────────
#  2. Эквидистанта для обточки кромки листа
# ─────────────────────────────────────────────────────────────────────────

def test_offset_v30_sheet_1mm():
    """Лист 1 мм + V30 d_tip=0.1: offset = 0.318 мм (округлённо)."""
    t = Tool(number=1, name="t", tip_diameter=0.10, open_angle=30,
             tool_type=ToolType.CONE_3D)
    offset = t.offset_for_sheet(sheet_thickness=1.0)
    assert abs(offset - 0.31794919243112270) < 1e-12


def test_offset_sharp_tip():
    """Идеально острая фреза (d_tip=0): offset = H · tan(α/2)."""
    t = Tool(number=1, name="t", tip_diameter=0.0, open_angle=60,
             tool_type=ToolType.CONE_3D)
    assert abs(t.offset_for_sheet(1.0) - math.tan(math.radians(30))) < 1e-12


# ─────────────────────────────────────────────────────────────────────────
#  3. Обратная задача: ширина -> Z (V-гравировка, сверху)
# ─────────────────────────────────────────────────────────────────────────

def test_z_for_width_30deg():
    """V30, d_tip=0.1, нужна ширина 0.5 мм:
       z = (0.5 − 0.1) / (2·tan(15°)) = 0.4 / 0.5358... = 0.7465..."""
    t = Tool(number=1, name="t", tip_diameter=0.10, open_angle=30,
             tool_type=ToolType.CONE_3D)
    z = t.z_for_target_width(0.5)
    # проверка обратимости
    w = t.width_at_z(z)
    assert abs(w - 0.5) < 1e-12


def test_z_for_width_too_narrow():
    """Если запрошена ширина < d_tip — должно бросить исключение."""
    t = Tool(number=1, name="t", tip_diameter=0.10, open_angle=30,
             tool_type=ToolType.CONE_3D)
    try:
        t.z_for_target_width(0.05)
    except ValueError:
        return
    raise AssertionError("Должно было бросить ValueError")


# ─────────────────────────────────────────────────────────────────────────
#  4. ToolDB и подбор фрезы под канал
# ─────────────────────────────────────────────────────────────────────────

def test_find_for_channel_picks_largest():
    """Из подходящих фрез берём самую крупную (для производительности)."""
    db = demo_db()
    # Канал 1.0 мм при листе 1 мм:
    #   V30 d010 offset = 0.318  -> 2·offset = 0.636 ≤ 1.0  [OK]
    #   V30 d005 offset = 0.293  -> 2·offset = 0.586 ≤ 1.0  [OK]
    #   V60 d010 offset = 0.627  -> 2·offset = 1.254 > 1.0  [FAIL]
    #   V30 d002 offset = 0.278  -> 2·offset = 0.556 ≤ 1.0  [OK]
    # Из подходящих макс. offset у V30_d010 -> её и выбираем.
    tool = db.find_for_channel(channel_width=1.0, sheet_thickness=1.0)
    assert tool is not None
    assert tool.name == "V30_d010"


def test_find_for_channel_narrow():
    """Очень узкий канал -> подойдёт только тонкая фреза."""
    db = demo_db()
    # Канал 0.58 мм:
    #   V30 d010: 2·0.318 = 0.636 > 0.58  [FAIL]
    #   V30 d005: 2·0.293 = 0.586 > 0.58  [FAIL]  
    #   V30 d002: 2·0.288... = ? проверим:
    #     tan(15°)=0.26794919...  -> 0.01 + 0.26794919 = 0.27794919
    #     2·0.27794919 = 0.55589839 ≤ 0.58 [OK]
    tool = db.find_for_channel(channel_width=0.58, sheet_thickness=1.0)
    assert tool is not None
    assert tool.name == "V30_d002"


def test_find_for_channel_impossible():
    """Канал уже самой тонкой фрезы -> None."""
    db = demo_db()
    tool = db.find_for_channel(channel_width=0.001, sheet_thickness=1.0)
    assert tool is None


# ─────────────────────────────────────────────────────────────────────────
#  ПАРСЕР ИМЁН АЛЬФАКАМА
# ─────────────────────────────────────────────────────────────────────────

def test_parse_alphacam_name_std():
    """STD_D0_6BASE90° -> tip=0.6, angle=90, STANDARD."""
    from tools.tool_db import parse_alphacam_tool_name, ToolType
    p = parse_alphacam_tool_name("STD_D0_6BASE90°")
    assert p is not None
    assert p['tool_type'] == ToolType.STANDARD
    assert abs(p['tip_diameter'] - 0.6) < 1e-9
    assert abs(p['open_angle'] - 90.0) < 1e-9


def test_parse_alphacam_name_3d():
    """3D_D1_2BASE70° -> tip=1.2, angle=70, CONE_3D."""
    from tools.tool_db import parse_alphacam_tool_name, ToolType
    p = parse_alphacam_tool_name("3D_D1_2BASE70°")
    assert p is not None
    assert p['tool_type'] == ToolType.CONE_3D
    assert abs(p['tip_diameter'] - 1.2) < 1e-9
    assert abs(p['open_angle'] - 70.0) < 1e-9


def test_parse_alphacam_name_without_degree_sign():
    """Без знака градуса тоже должно работать."""
    from tools.tool_db import parse_alphacam_tool_name
    p = parse_alphacam_tool_name("STD_D0_6BASE90")
    assert p is not None


def test_parse_alphacam_name_invalid():
    """Произвольное имя возвращает None."""
    from tools.tool_db import parse_alphacam_tool_name
    assert parse_alphacam_tool_name("V30_d010") is None
    assert parse_alphacam_tool_name("my_tool") is None


def test_make_tool_from_alphacam_name():
    """Создание Tool из имени Альфакама + переопределение полей."""
    from tools.tool_db import make_tool_from_alphacam_name, ToolType
    t = make_tool_from_alphacam_name(
        number=1, name="STD_D0_6BASE90°",
        feed_cut=2500, spindle_rpm=70000,
    )
    assert t.number == 1
    assert t.name == "STD_D0_6BASE90°"
    assert abs(t.tip_diameter - 0.6) < 1e-9
    assert abs(t.open_angle - 90.0) < 1e-9
    assert t.tool_type == ToolType.STANDARD
    assert t.feed_cut == 2500
    assert t.spindle_rpm == 70000


# ─────────────────────────────────────────────────────────────────────────
#  ЗАПУСК (в самом конце файла, чтобы видеть все тесты выше)
# ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import inspect
    
    tests = [
        (name, fn) for name, fn in inspect.getmembers(sys.modules[__name__])
        if name.startswith("test_") and callable(fn)
    ]
    
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  [OK] {name}")
        except Exception as e:
            failed.append((name, e))
            print(f"  [FAIL] {name}: {e}")
    
    print(f"\n{passed}/{len(tests)} тестов пройдено")
    if failed:
        sys.exit(1)
