"""Product retrieval must retain explicitly requested identity information."""

import sqlite3

import pytest

from app.infrastructure.repository import InMemoryProductRepository, SQLiteProductRepository


@pytest.mark.parametrize('provider', ['memory', 'sqlite'])
def test_brand_is_not_lost_before_candidate_limit(provider, tmp_path):
    rows = [
        {'barcode': str(i), 'product_name': f'우유맛 캔디 {i}', 'manufacturer_name': '다른회사',
         'raw_materials': '우유 우유 단백질'} for i in range(70)
    ]
    rows.append({'barcode': 'target', 'product_name': '흰우유', 'manufacturer_name': '매일유업',
                 'raw_materials': '원유'})
    if provider == 'memory':
        repository = InMemoryProductRepository(seed_data={row['barcode']: row for row in rows})
    else:
        path = str(tmp_path / 'products.sqlite')
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE products (barcode TEXT, product_name TEXT, manufacturer_name TEXT, raw_materials TEXT)')
            connection.executemany('INSERT INTO products VALUES (:barcode, :product_name, :manufacturer_name, :raw_materials)', rows)
        repository = SQLiteProductRepository(path)
    candidates = repository.search_candidates('매일 우유')
    assert [p.barcode for p in candidates] == ['target']


def test_common_size_does_not_replace_requested_product_identity():
    repository = InMemoryProductRepository(seed_data={
        'wrong': {'product_name': '다른 수소수 500ml', 'barcode': 'wrong'},
        'correct': {'product_name': '제주삼다수 500ml', 'barcode': 'correct'},
    })
    assert [p.barcode for p in repository.search_candidates('삼다수 500ml')] == ['correct']


def test_unbranded_query_and_typo_can_still_find_candidates():
    repository = InMemoryProductRepository(seed_data={
        'one': {'product_name': '농심 새우깡 90g', 'barcode': 'one'},
        'two': {'product_name': '포카칩 오리지널', 'barcode': 'two'},
    })
    assert repository.search_candidates('새우깡')[0].barcode == 'one'
    assert repository.search_candidates('포카칩 오리지날')[0].barcode == 'two'


def test_exact_name_is_retained_among_many_same_brand_products(tmp_path):
    path = str(tmp_path / 'products.sqlite')
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE products (barcode TEXT, product_name TEXT, manufacturer_name TEXT, raw_materials TEXT)')
        connection.executemany('INSERT INTO products VALUES (?, ?, ?, ?)', [
            (str(i), f'우유속 딸기 {i}', '매일유업', '매일 우유 원유') for i in range(70)
        ] + [('target', '매일우유', '매일유업', None)])
    candidates = SQLiteProductRepository(path).search_candidates('매일 우유')
    assert candidates[0].barcode == 'target'


def test_explicit_size_excludes_conflicts_and_recognizes_equivalent_units():
    repository = InMemoryProductRepository(seed_data={
        'wrong': {'product_name': '검사생수 150ml', 'barcode': 'wrong'},
        'correct': {'product_name': '검사생수 0.5L', 'barcode': 'correct'},
    })
    assert [p.barcode for p in repository.search_candidates('검사생수 500ml')] == ['correct']


@pytest.mark.parametrize('query,target,others', [
    ('코카콜라 제로 355ml', '코카콜라 제로 355ml', ['코카콜라 355ml', '코카콜라음료 파워에이드355ml']),
    ('새우 깡', '새우깡', ['새우', '새우맛 우동']),
    ('서울 우유', '서울우유', ['서울식품 우유식빵']),
    ('검사바 딸기 50g', '검사바 딸기 50g', ['검사바 초코 50g', '다른바 딸기 50g']),
])
def test_all_explicit_name_qualifiers_survive_tokenization(query, target, others):
    rows = {'target': {'product_name': target, 'barcode': 'target'}}
    rows.update({str(i): {'product_name': name, 'barcode': str(i)} for i, name in enumerate(others)})
    candidates = InMemoryProductRepository(seed_data=rows).search_candidates(query)
    assert [p.barcode for p in candidates] == ['target']


def test_manufacturer_alone_does_not_establish_a_product_line():
    repository = InMemoryProductRepository(seed_data={
        'cola': {'product_name': '코카콜라 제로', 'manufacturer_name': '코카콜라음료', 'barcode': 'cola'},
        'other': {'product_name': '스프라이트 제로', 'manufacturer_name': '코카콜라음료', 'barcode': 'other'},
    })
    assert [p.barcode for p in repository.search_candidates('코카콜라 제로 355ml')] == ['cola']
