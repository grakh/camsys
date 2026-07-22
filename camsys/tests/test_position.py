"""
tests/test_position.py — POSITION-вариант экспорта: правило выбора пары
реперов, ориентация, применение трансформа к геометрии.
"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.position import (
    pick_alignment_pair, compute_position_transform,
    transform_polypath, transform_fiducial, PositionTransform,
)
from geometry.primitives import Line, Arc, Polypath
from core.project import Fiducial


def _approx(a, b, tol=1e-6):
    assert abs(a - b) <= tol, f"{a} != {b} (tol={tol})"


# ─────────────────────────────────────────────────────────────────────────
#  Выбор пары реперов
# ─────────────────────────────────────────────────────────────────────────

def test_pick_two_horizontal():
    # 120795 single-order: два репера на y=0
    f = [Fiducial(x=0, y=0, name='F1'),
         Fiducial(x=581, y=0, name='F2')]
    lb, other = pick_alignment_pair(f)
    assert lb.name == 'F1'
    assert other.name == 'F2'


def test_pick_two_vertical():
    # 121561 (в 41000): FID6/FID7 — вертикальная пара, x=722.69
    f = [Fiducial(x=722.69, y=467.38, name='F7'),
         Fiducial(x=722.69, y=7.00, name='F6')]
    lb, other = pick_alignment_pair(f)
    assert lb.name == 'F6'  # min Y
    assert other.name == 'F7'


def test_pick_three_fids_coaxial_max_span():
    # 121561 (реальные данные): FID5(700,0) — стрэй-репер листа,
    # FID6(722.69,7), FID7(722.69,467.38) — истинная пара.
    # Правило: коаксиальная пара с max span → FID6/FID7.
    f = [Fiducial(x=700, y=0, name='F5'),
         Fiducial(x=722.69, y=7.00, name='F6'),
         Fiducial(x=722.69, y=467.38, name='F7')]
    lb, other = pick_alignment_pair(f)
    assert {lb.name, other.name} == {'F6', 'F7'}
    assert lb.name == 'F6'  # min Y в паре


def test_pick_three_all_coaxial_pick_max_span():
    # 121541 (40991): FID5(469.41,0), FID9(700,0), FID11(717.06,0) —
    # ВСЕ три на y=0. Правило max span → FID5/FID11 (span=247.65).
    f = [Fiducial(x=469.41, y=0, name='F5'),
         Fiducial(x=700, y=0, name='F9'),
         Fiducial(x=717.06, y=0, name='F11')]
    lb, other = pick_alignment_pair(f)
    assert {lb.name, other.name} == {'F5', 'F11'}
    assert lb.name == 'F5'  # min X при равных Y


def test_pick_none_when_zero():
    assert pick_alignment_pair([]) is None


def test_pick_single_returns_lb_and_none():
    f = [Fiducial(x=42, y=17, name='F1')]
    pair = pick_alignment_pair(f)
    assert pair is not None
    lb, other = pair
    assert lb.name == 'F1'
    assert other is None


def test_transform_single_fid_shift_only():
    f = [Fiducial(x=100, y=50, name='F1')]
    t = compute_position_transform(f)
    assert t is not None
    assert not t.rotate_cw90
    assert t.dist is None  # пара неизвестна — dist не определён
    _approx(t.lb_x, 100); _approx(t.lb_y, 50)
    # LB → (0, 0)
    p = t.apply_point(100, 50)
    _approx(p[0], 0); _approx(p[1], 0)
    # Точка (150, 80) → (50, 30) (только сдвиг)
    p = t.apply_point(150, 80)
    _approx(p[0], 50); _approx(p[1], 30)


def test_transform_zero_fids_returns_none():
    assert compute_position_transform([]) is None


# ─────────────────────────────────────────────────────────────────────────
#  Расчёт трансформа
# ─────────────────────────────────────────────────────────────────────────

def test_transform_horizontal_no_rotation():
    # 120795: (0,0) и (581,0) — горизонтальная, поворота не нужно
    f = [Fiducial(x=0, y=0), Fiducial(x=581, y=0)]
    t = compute_position_transform(f)
    assert not t.rotate_cw90
    _approx(t.dist, 581, tol=1e-3)
    # LB(0,0) → (0,0)
    p = t.apply_point(0, 0)
    _approx(p[0], 0); _approx(p[1], 0)
    # (581,0) → (581,0)
    p = t.apply_point(581, 0)
    _approx(p[0], 581, tol=1e-3); _approx(p[1], 0, tol=1e-3)


def test_transform_vertical_rotates_minus_90():
    # 121561: FID6(722.69,7), FID7(722.69,467.38)
    f = [Fiducial(x=722.69, y=7.00, name='F6'),
         Fiducial(x=722.69, y=467.38, name='F7')]
    t = compute_position_transform(f)
    assert t.rotate_cw90
    _approx(t.dist, 460.38, tol=1e-2)
    # LB (F6) → (0,0)
    p = t.apply_point(722.69, 7.00)
    _approx(p[0], 0, tol=1e-3); _approx(p[1], 0, tol=1e-3)
    # F7 → (dist, 0)
    p = t.apply_point(722.69, 467.38)
    _approx(p[0], 460.38, tol=1e-2); _approx(p[1], 0, tol=1e-3)


# ─────────────────────────────────────────────────────────────────────────
#  Изометрия: сохранение расстояний, ccw, радиуса
# ─────────────────────────────────────────────────────────────────────────

def test_transform_distances_preserved():
    # После трансформа расстояния между точками не меняются.
    f = [Fiducial(x=722.69, y=7.00),
         Fiducial(x=722.69, y=467.38)]
    t = compute_position_transform(f)
    a, b = (100, 200), (400, 350)
    d_orig = math.hypot(a[0]-b[0], a[1]-b[1])
    a2 = t.apply_point(*a); b2 = t.apply_point(*b)
    d_new = math.hypot(a2[0]-b2[0], a2[1]-b2[1])
    _approx(d_orig, d_new)


def test_transform_arc_ccw_and_radius_unchanged():
    # Дуга должна сохранить ccw и радиус — это ключ, чтобы G2/G3 не
    # переставились после трансформа и радиус в .anc остался тем же.
    pp = Polypath(segments=[
        Arc(a=(1.0, 0.0), b=(0.0, 1.0), center=(0.0, 0.0), ccw=True),
    ], closed=False)
    t = PositionTransform(lb_x=0, lb_y=0, rotate_cw90=True, dist=1.0)
    pp2 = transform_polypath(pp, t)
    arc = pp2.segments[0]
    assert arc.ccw
    _approx(arc.radius, 1.0)


def test_transform_line_endpoints():
    t = PositionTransform(lb_x=10, lb_y=20, rotate_cw90=False, dist=100)
    pp = Polypath(segments=[Line(a=(10, 20), b=(15, 25))], closed=False)
    pp2 = transform_polypath(pp, t)
    _approx(pp2.segments[0].a[0], 0)
    _approx(pp2.segments[0].a[1], 0)
    _approx(pp2.segments[0].b[0], 5)
    _approx(pp2.segments[0].b[1], 5)


# ─────────────────────────────────────────────────────────────────────────
#  Трансформ репера сохраняет id/name
# ─────────────────────────────────────────────────────────────────────────

def test_fiducial_transform_preserves_metadata():
    t = PositionTransform(lb_x=0, lb_y=0, rotate_cw90=False, dist=1.0)
    f = Fiducial(x=5, y=7, name='FID3', id='abc123')
    f2 = transform_fiducial(f, t)
    assert f2.name == 'FID3'
    assert f2.id == 'abc123'
    _approx(f2.x, 5); _approx(f2.y, 7)


# ─────────────────────────────────────────────────────────────────────────
#  ЗАПУСК (в стиле остальных тестов проекта)
# ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import inspect
    tests = [(n, f) for n, f in inspect.getmembers(sys.modules[__name__])
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
            import traceback
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} тестов пройдено")
    sys.exit(0 if not failed else 1)
