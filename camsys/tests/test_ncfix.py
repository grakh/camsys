"""Fix G-code как в Anctor (v1.8.3): программы CAMSYS выходят уже
исправленными — Fix Anctor на них ничего не меняет."""
import os

AI = '/mnt/user-data/uploads/124173_test.ai'


def _programs(fix=True):
    import camsys.post.mtx_anderson  # noqa: F401
    from camsys.core.session import CamSession
    from camsys.post.package_export import PackageExporter
    s = CamSession()
    s.load_ai(AI)
    s.set_cutting_params_from_dict({'knife_angle': 90.0, 'tip_diameter': 0.8,
                                    'bottom': 0.25, 'generate_corner': True,
                                    'ncfix_enabled': fix})
    s.create_blade_operations()
    ex = PackageExporter(s.project, s.cutting_params)
    return ex.generate(), ex


def test_output_is_a_fix_fixed_point():
    """Fix Anctor (тот же код, все проходы) на готовых программах — ноль
    правок; без Fix в CAMSYS — правки есть (иначе тест ничего не ловит)."""
    if not os.path.exists(AI):
        print('  SKIP — нет 124173_test.ai')
        return
    from camsys.post.ncfix import fix_package
    from camsys.post import anctor_fix as F
    files, ex = _programs(True)
    again, _ = fix_package(files, max_passes=1)
    changed = [n for n in files if again[n] != files[n]]
    assert not changed, changed
    raw, _ = _programs(False)
    once, _ = fix_package(raw, max_passes=1)
    assert any(once[n] != raw[n] for n in raw), 'Fix ничего не правит'
    assert any('FIX G-CODE' in l for l in ex.machinability_report)


def test_fix_keeps_leads_clean():
    """После Fix лиды по-прежнему не врезаются (проверка Anctor)."""
    if not os.path.exists(AI):
        print('  SKIP — нет 124173_test.ai')
        return
    import tempfile
    from camsys.post import anctor_fix as F
    files, _ = _programs(True)
    with tempfile.TemporaryDirectory() as d:
        for n, t in files.items():
            if not n.endswith('_all_R.anc'):
                continue
            pth = os.path.join(d, n)
            open(pth, 'w', encoding='latin-1').write(t)
            parts, eq = F.parse_anc_parts(pth)
            assert not F.find_lead_gouges(parts, eq or 0.65)
            assert not F.find_contour_collisions(parts, eq or 0.65)


def test_dla_matches_frame_count_after_fix():
    """CLN(DLAn) каждого контура = кадров между дугой захода (G12) и
    выхода (G13) − 2, не больше общего DLA (v1.8.4). Маркер G12 у нового
    поста — в комментарии (`;50,10 G12`), nc_update_dla Anctor его не
    видит; recount_dla видит. После Fix DLA не должен остаться больше
    допустимого (127821 PART3: был 61 при 59 — стойка выбросила бы контур).
    """
    if not os.path.exists(AI):
        print('  SKIP — нет 124173_test.ai')
        return
    import re
    from camsys.post.ncfix import recount_dla
    files, _ = _programs(True)
    for n, t in files.items():
        _, k = recount_dla(t)
        assert k == 0, (n, k)
    # всем контурам DLA = общему (80) — пересчёт возвращает как было
    t = next(t for n, t in files.items() if n.endswith('_all_R.anc'))
    cap = int(re.search(r'CLN\(DLA(\d+)\)', t).group(1))
    spoiled = re.sub(r'CLN\(DLA(\d+)\) ;25\.1',
                     lambda m: 'CLN(DLA%d) ;25.1' % cap, t)
    assert spoiled != t
    back, k = recount_dla(spoiled)
    assert k > 0 and back == t
