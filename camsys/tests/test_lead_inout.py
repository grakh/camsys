"""Тесты построения Lead-In / Lead-Out (тангенциальная дуга + прямая).

Геометрия захода/отхода в постпроцессоре MTX выводится через обычные
G2/G3 с явным указанием радиуса и направления, а не через специальные
G12/G13 (которые давали неоднозначное направление дуги)."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
from geometry.lead_inout import build_lead_in, build_lead_out, LeadGeometry
from geometry.primitives import Line, Arc, vec_dist, EPS


def approx(a, b, tol=1e-6):
    return abs(a - b) < tol


# ─────────────────────────────────────────────────────────────────────────
#  ВХОД: ДУГА КАСАЕТСЯ КОНТУРА В ТОЧКЕ СТЫКА
# ─────────────────────────────────────────────────────────────────────────

def test_lead_in_arc_touches_start():
    """Дуга захода должна заканчиваться ровно в start_point контура."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)  # контур идёт вправо
    
    lead = build_lead_in(start, tangent, side='left',
                          line_length=2.0, arc_radius=1.0,
                          approach_angle_deg=45.0)
    
    assert lead.arc is not None
    assert vec_dist(lead.arc.b, start) < 1e-9


def test_lead_in_arc_tangent_at_contour():
    """Касательная дуги в точке start_point должна совпадать с касательной 
    контура (т.е. это плавный заход, не излом)."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    
    lead = build_lead_in(start, tangent, side='left',
                          line_length=2.0, arc_radius=1.0,
                          approach_angle_deg=45.0)
    
    # Касательная дуги в её конце (b)
    arc = lead.arc
    arc_tang_at_end = arc.tangent_at_end()
    
    # Должна быть параллельна tangent с тем же направлением
    dot = arc_tang_at_end[0]*tangent[0] + arc_tang_at_end[1]*tangent[1]
    # cos угла ≈ 1 → касательные сонаправлены
    assert dot > 0.999, f"Касательная дуги {arc_tang_at_end} не совпадает " \
                        f"с касательной контура {tangent}, dot={dot:.6f}"


def test_lead_in_line_meets_arc():
    """Конец прямой должен совпадать с началом дуги (стык line→arc)."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    
    lead = build_lead_in(start, tangent, side='left',
                          line_length=2.0, arc_radius=1.0,
                          approach_angle_deg=45.0)
    
    assert vec_dist(lead.line.b, lead.arc.a) < 1e-9


def test_lead_in_line_length():
    """Длина прямой = заявленной."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    line_length = 3.5
    
    lead = build_lead_in(start, tangent, side='left',
                          line_length=line_length, arc_radius=1.0,
                          approach_angle_deg=45.0)
    
    actual_length = vec_dist(lead.line.a, lead.line.b)
    assert approx(actual_length, line_length, tol=1e-9)


def test_lead_in_arc_radius():
    """Радиус дуги = заявленному."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    arc_radius = 1.7
    
    lead = build_lead_in(start, tangent, side='left',
                          line_length=2.0, arc_radius=arc_radius,
                          approach_angle_deg=45.0)
    
    actual_radius = vec_dist(lead.arc.center, lead.arc.a)
    assert approx(actual_radius, arc_radius, tol=1e-9)
    # И до точки b тоже должно быть R
    actual_radius2 = vec_dist(lead.arc.center, lead.arc.b)
    assert approx(actual_radius2, arc_radius, tol=1e-9)


def test_lead_in_left_vs_right_side():
    """Lead-In с left и right должны быть зеркальны относительно касательной."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    
    lead_L = build_lead_in(start, tangent, side='left',
                            line_length=2.0, arc_radius=1.0,
                            approach_angle_deg=45.0)
    lead_R = build_lead_in(start, tangent, side='right',
                            line_length=2.0, arc_radius=1.0,
                            approach_angle_deg=45.0)
    
    # Точки старта прямых должны быть зеркальны по оси X (касательной)
    # Left → выше оси, Right → ниже
    Lstart = lead_L.line.a
    Rstart = lead_R.line.a
    
    assert approx(Lstart[0], Rstart[0], tol=1e-9), \
        "X-координаты должны совпадать при зеркале"
    assert approx(Lstart[1], -Rstart[1], tol=1e-9), \
        "Y-координаты должны быть противоположны"


# ─────────────────────────────────────────────────────────────────────────
#  ВЫХОД (LEAD-OUT) — ЗЕРКАЛО ВХОДА
# ─────────────────────────────────────────────────────────────────────────

def test_lead_out_arc_touches_end():
    """Дуга выхода должна начинаться ровно в end_point контура."""
    end = (5.0, 5.0)
    tangent = (1.0, 0.0)
    
    lead = build_lead_out(end, tangent, side='left',
                           line_length=2.0, arc_radius=1.0,
                           retract_angle_deg=45.0)
    
    assert vec_dist(lead.arc.a, end) < 1e-9


def test_lead_out_tangent_at_contour():
    """Касательная дуги выхода в точке end_point = касательная контура."""
    end = (5.0, 5.0)
    tangent = (1.0, 0.0)
    
    lead = build_lead_out(end, tangent, side='left',
                           line_length=2.0, arc_radius=1.0,
                           retract_angle_deg=45.0)
    
    # Касательная дуги в её начале
    arc = lead.arc
    arc_tang_at_start = arc.tangent_at_start()
    
    dot = arc_tang_at_start[0]*tangent[0] + arc_tang_at_start[1]*tangent[1]
    assert dot > 0.999, f"Касательная дуги выхода {arc_tang_at_start} " \
                        f"не совпадает с касательной {tangent}"


def test_lead_out_arc_meets_line():
    """Дуга → линия: end дуги = начало линии."""
    end = (5.0, 5.0)
    tangent = (1.0, 0.0)
    
    lead = build_lead_out(end, tangent, side='left',
                           line_length=2.0, arc_radius=1.0)
    
    assert vec_dist(lead.arc.b, lead.line.a) < 1e-9


# ─────────────────────────────────────────────────────────────────────────
#  ГРАНИЧНЫЕ СЛУЧАИ
# ─────────────────────────────────────────────────────────────────────────

def test_lead_in_zero_approach_angle():
    """Угол 0° = прямая параллельна касательной (вырожденный случай).
    Дуга должна быть бесконечно малой / контролируемо обрабатываться."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    
    # Угол 0 граничный случай — функция не должна падать
    lead = build_lead_in(start, tangent, side='left',
                          line_length=2.0, arc_radius=1.0,
                          approach_angle_deg=0.0)
    # На малом угле дуга почти не разворачивается, прямая почти касательна
    assert lead.arc is not None
    assert lead.line is not None


def test_lead_in_90deg():
    """Угол 90° — заход перпендикулярно касательной."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    
    lead = build_lead_in(start, tangent, side='left',
                          line_length=2.0, arc_radius=1.0,
                          approach_angle_deg=90.0)
    
    # Прямая должна быть перпендикулярна к tangent
    line_vec = (lead.line.b[0] - lead.line.a[0],
                lead.line.b[1] - lead.line.a[1])
    dot = line_vec[0]*tangent[0] + line_vec[1]*tangent[1]
    L = math.hypot(line_vec[0], line_vec[1])
    # cos(угла между line и tangent) ≈ 0 для 90°
    cos_angle = dot / L
    assert abs(cos_angle) < 1e-6


if __name__ == "__main__":
    import inspect
    tests = [(n,f) for n,f in inspect.getmembers(sys.modules[__name__])
             if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for n, f in tests:
        try:
            f()
            passed += 1
            print(f"  [OK] {n}")
        except Exception as e:
            failed.append((n, e))
            print(f"  [FAIL] {n}: {e!r}")
            import traceback; traceback.print_exc()
    print(f"\n{passed}/{len(tests)} тестов пройдено")
    sys.exit(0 if not failed else 1)
