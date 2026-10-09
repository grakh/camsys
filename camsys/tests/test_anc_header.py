"""Разбор параметров прогона из заголовка .anc."""
import os
import sys
import math
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from camsys.geometry.anc_header import parse_anc_header, describe_anc

UP = '/mnt/user-data/uploads'


def test_reads_parameters_from_real_file():
    """Параметры берутся из файла, а не из умолчаний.

    Повод: при сверке с эталоном я взял угол и ABS из
    camsys_settings.json (70 / 0.19), а файл был сделан на 80 / 0.25.
    Эквидистанта вышла 0.533 вместо 0.6098, и три гипотезы подряд
    проверялись на неверных числах. Всё нужное лежало в первых тридцати
    строках самого файла.
    """
    path = os.path.join(UP, '124173_test.anc')
    if not os.path.exists(path):
        print('  SKIP — нет 124173_test.anc')
        return
    head = parse_anc_header(open(path, encoding='utf-8',
                                 errors='replace').read())
    print(f'  {head}')
    assert head.knife_angle == 80.0
    assert abs(head.bottom - 0.25) < 1e-9
    assert abs(head.die_height - 0.44) < 1e-9
    assert head.fiducial_distance == 700.0
    assert head.program_name == '124173_TEST.ANC'

    # Эквидистанта считается от пятки, которой в заголовке нет
    off = head.tool_offset(0.8)
    expect = (0.8 + 2 * 0.25 * math.tan(math.radians(40.0))) / 2
    assert abs(off - expect) < 1e-9
    print(f'  эквидистанта при пятке 0.8: {off:.4f}')


def test_angle_comes_from_cln_not_filename():
    """Угол читается из CLN(DLA…), а не из имени файла.

    Раньше тест был привязан к конкретному файлу, где в имени стояло 90,
    а в заголовке 80. Файл под тем же именем позже заменили другим — и
    тест упал, хотя разбор работал. Теперь проверяется само правило: что
    бы ни стояло в имени, угол берётся из CLN.
    """
    head = parse_anc_header(
        "N1 ;файл с «90» в имени\n"
        "N26 SSDE[SD.USR.ProcessData.ProgZDepth = ABS(0.19)]\n"
        "N44 CLN(DLA80) ;25.1\n")
    assert head.knife_angle == 80.0
    assert abs(head.bottom - 0.19) < 1e-9

    # И на реальном файле, каким бы он ни был: угол = то, что в CLN
    for name in ('124173_test.anc', '124173_90_all_R.anc'):
        path = os.path.join(UP, name)
        if not os.path.exists(path):
            continue
        text = open(path, encoding='utf-8', errors='replace').read()
        h = parse_anc_header(text)
        m = re.search(r'CLN\(\s*DLA(\d+)\s*\)', text)
        print(f'  {name}: в файле CLN(DLA{m.group(1)}), разобрано '
              f'{h.knife_angle}')
        assert h.knife_angle == float(m.group(1))


def test_missing_values_are_none_not_guesses():
    """Чего в файле нет — остаётся None, а не подставляется наугад."""
    head = parse_anc_header("N1 ;пустой заголовок\nN2 G0 X0 Y0\n")
    assert head.knife_angle is None
    assert head.bottom is None
    assert head.tool_equidistant(0.8) is None
    assert head.tool_offset(0.8) is None
    # describe_anc не должен падать на неполных данных
    assert 'угол ?' in describe_anc("N1 ;ничего\n")
