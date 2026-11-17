"""Станок DCM 920 (v1.7.89): программы — перевод тех же программ AEC
в формат поста DCM_Engraving_V1_23."""
import math
import os
import re
import tempfile

from camsys.tests.test_foreign_breaks import _two_rects, _params

_NUM = r'-?\d+(?:\.\d+)?'


def _moves(text, dcm):
    """(G, X, Y, R) всех строк с X и Y в теле программы."""
    out, g, body = [], None, dcm
    for ln in text.splitlines():
        s = re.sub(r'^\s*N?\d+\s?', '', ln).split(';')[0].split('%')[0]
        s = s.strip()
        if not dcm:
            if s.startswith('.SHAPE'):
                body = True
            if s.startswith('.PRGEND'):
                body = False
            if not body:
                continue
        gg = re.findall(r'\bG([0-3])\b', s)
        if gg:
            g = gg[-1]
        mx = re.search(r'X(' + _NUM + ')', s)
        my = re.search(r'Y(' + _NUM + ')', s)
        mr = re.search(r'\bR(' + _NUM + ')', s)
        if mx and my:
            out.append((g, mx.group(1), my.group(1),
                        mr.group(1) if mr else None))
    return out


def _programs():
    import camsys.post.mtx_anderson  # noqa: F401
    from camsys.post.package_export import PackageExporter
    p = _params()
    p.generate_corner = True
    return PackageExporter(_two_rects(1.2), p).generate()


def test_dcm_moves_are_the_aec_moves():
    """Каждое движение DCM — то же, что в программе AEC: порядок, G0–G3,
    X, Y, R. Меняется только обёртка."""
    from camsys.post.dcm920 import anc_to_dcm
    files = _programs()
    assert files
    for name, anc in files.items():
        dcm = anc_to_dcm(anc)
        assert _moves(anc, False) == _moves(dcm, True), name
        # проходов столько же, компенсация та же
        parts = len(re.findall(r'\(MSG, [^)]*PART\d+\)', anc))
        holes = len(re.findall(r';21[01],[34]', anc))
        assert dcm.count('BEGIN') == parts + holes, name
        n_dcm = len(re.findall(r'^N\d+ G42$', dcm, re.M))
        assert n_dcm == anc.count('G42 X'), name
        assert dcm.count('\nN') + 1 == len(dcm.splitlines())


def test_dcm_wrapper_follows_template():
    """Обёртка как в DCM_Engraving_V1_23: % вместо ;, %SHAPE, FF500,
    F в мм/с, M3 K один раз, ни следа AEC (SSDE, DLA, SCLN, метки)."""
    from camsys.post.dcm920 import anc_to_dcm
    anc = next(t for n, t in _programs().items() if n.endswith('_1_M.anc'))
    dcm = anc_to_dcm(anc)
    for bad in ('SSDE', 'DLA', 'SCLN', 'GOTO', '.SHAPE', ';', 'MSG',
                'PRESETTINGS'):
        assert bad not in dcm, bad
    assert dcm.count('M3 K') == 1
    assert 'G0 Z53 FF500' in dcm and dcm.rstrip().endswith('M30')
    assert 'G41/G42 radius' in dcm               # подсказка оператору
    lines = [re.sub(r'^N\d+ ', '', l) for l in dcm.splitlines()]
    i = lines.index('%SHAPE 1 BEGIN')
    # подвод, врезание, G42 отдельной строкой, заход с полным G1 и F
    assert lines[i + 1].startswith('G0 X') and lines[i + 1].endswith('FF500')
    assert re.match(r'G1 Z' + _NUM + r' F' + _NUM + '$', lines[i + 2])
    assert lines[i + 3] == 'G42'
    assert re.match(r'G1 X' + _NUM + r' Y' + _NUM + r' F' + _NUM + '$',
                    lines[i + 4])
    j = lines.index('%SHAPE 1 END')
    assert lines[j - 1].startswith('%PATH ') and lines[j - 1].endswith(' MM')
    assert lines[j - 2].startswith('G0 Z') and lines[j - 2].endswith('FF500')
    assert 'G40' in lines[i:j]
    # подача: мм/мин → мм/с, округление до 10 мм/мин
    from camsys.post.dcm920 import _feed
    assert _feed(1500) == '25' and _feed(800) == '13.33'


def test_dcm_fiducials_each_hole_own_shape():
    """Реперы — циклы $210/$211: каждый прокол в своём %SHAPE, подъём на
    Z10, подход на Z3, прокол G1."""
    from camsys.post.dcm920 import anc_to_dcm
    anc = ("N1 ;** NC-Program created on: 1 JAN 26 - 00:00 **\n"
           "N2 M3 S70000 ;20,1\nN3 G0 Z3 ;25,2\n96 .SHAPE1\n"
           "N97 M3 S70000 ;20,1\n"
           "N98 G0 X0 Y0 ;210,1\nN99 G1 Z10 F5000 ;210,2\n"
           "N100 G0 Z3 ;210,3\nN101 G1 Z0.1 F1500 ;211,4\n"
           "N102 Z10 F5000 ;211,1\nN103 G0 X400 Y0 ;211,2\n"
           "N104 G1 Z0.1 F1500 ;211,3\nN105 Z10 F5000 ;211,1\n"
           "456 .PRGEND\nN457 M30\n")
    dcm = [re.sub(r'^N\d+ ', '', l) for l in anc_to_dcm(anc).splitlines()]
    a = dcm.index('%SHAPE 1 BEGIN')
    assert dcm[a:a + 7] == ['%SHAPE 1 BEGIN', 'G0 Z10 FF500',
                            'G0 X0 Y0 FF500', 'G0 Z3 FF500', 'G1 Z0.1 F25',
                            'G0 Z10 FF500', '%SHAPE 1 END']
    b = dcm.index('%SHAPE 2 BEGIN')
    assert dcm[b:b + 6] == ['%SHAPE 2 BEGIN', 'G0 X400 Y0 FF500',
                            'G0 Z3 FF500', 'G1 Z0.1 F25', 'G0 Z10 FF500',
                            '%SHAPE 2 END']


def test_unknown_motion_is_an_error():
    """Непонятное движение нельзя молча выкинуть из программы станка."""
    from camsys.post.dcm920 import anc_to_dcm
    anc = "96 .SHAPE1\nN1 G0 X0 Y0\nN2 G1 Z0 F800\nN3 G5 X1 Y1\n"
    try:
        anc_to_dcm(anc)
    except ValueError:
        return
    raise AssertionError('непонятная строка прошла молча')


def test_export_writes_npg_for_dcm():
    """Выбран DCM 920 — в папку пишутся .npg вместо .anc, AEC — .anc."""
    import camsys.post.mtx_anderson  # noqa: F401
    from camsys.post.package_export import PackageExporter
    for machine, ext in (('AEC_GVM', '.anc'), ('DCM_920', '.npg')):
        p = _params()
        p.machine = machine
        ex = PackageExporter(_two_rects(5.0), p)
        files = ex.generate()
        with tempfile.TemporaryDirectory() as d:
            paths = ex.write_files(d, files)
            names = sorted(x.name for x in paths)
            assert names and all(n.endswith(ext) for n in names), names
