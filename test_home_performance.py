"""Execute homepage SQL against a small relational fixture (no external services)."""
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask
from routes import views


class Cursor:
    def __init__(self, db, statements):
        self.cursor = db.cursor()
        self.statements = statements

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        sql = sql.replace('%s', '?').replace('LEFT(short_summary, 101)', 'substr(short_summary, 1, 101)')
        self.cursor.execute(sql, params)

    def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]

    def close(self):
        self.cursor.close()


@pytest.fixture
def home():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE news (id INTEGER PRIMARY KEY, keyword TEXT, title TEXT, link TEXT, source TEXT, fetchdate TEXT, sourceapi TEXT, score INTEGER, short_summary TEXT)')
    for i in range(1, 122):
        db.execute('INSERT INTO news VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   (i, '主题甲', f'新闻-{i:03}', 'https://example.com', '来源甲', '2026-09-08', 'api-a', 5, '摘' * 10000))
    db.execute("INSERT INTO news VALUES (200, '主题乙', '乙新闻', '', '来源乙', '2026-09-08', 'api-b', 3, NULL)")
    db.execute("INSERT INTO news VALUES (201, '已删除', '不应显示', '', '', '2026-09-08', 'legacy-api', 1, '')")
    statements = []
    class Connection:
        def cursor(self):
            return Cursor(db, statements)
        def close(self):
            pass
    app = Flask(__name__, template_folder=str(Path(__file__).parent / 'templates'))
    app.register_blueprint(views.views_bp)
    # base.html references other blueprint endpoints only through literal paths.
    app.config['TESTING'] = True
    with patch.object(views, 'get_connection', return_value=Connection()), patch.object(views, 'get_table_name', return_value='news'), patch.object(views, 'value', return_value=['主题甲', '主题乙', '空主题']):
        yield app.test_client(), statements
    db.close()


def get(client, extra=''):
    return client.get('/?date_from=2026-09-08&date_to=2026-09-08' + extra)


def test_pages_cover_all_news_without_duplicates(home):
    client, statements = home
    import re
    titles = []
    for page in (1, 2, 3):
        response = get(client, f'&page={page}')
        assert response.status_code == 200
        titles.extend(re.findall(r'新闻-\d{3}', response.get_data(as_text=True)))
    assert titles == [f'新闻-{i:03}' for i in range(121, 0, -1)]
    assert len(set(titles)) == 121
    assert all('LIMIT %s OFFSET %s' in sql for sql, _ in statements if 'SELECT id,' in sql)


def test_statistics_and_tabs_cover_all_topics(home):
    client, _ = home
    text = get(client).get_data(as_text=True)
    assert '122' in text and '121' in text
    assert '主题乙' in text and '空主题' in text
    assert '乙新闻' not in text and '不应显示' not in text
    assert 'legacy-api' in text  # Preserve date-wide API options.
    assert '摘' * 101 not in text
    assert '摘' * 100 in text
    assert len(text.encode()) < 120000


def test_topic_switch_and_page_clamping(home):
    client, _ = home
    text = get(client, '&keyword=主题乙&page=999').get_data(as_text=True)
    assert '乙新闻' in text and '新闻-121' not in text
    assert '第 1 / 1 页' in text


def test_empty_topic_skips_list_query(home):
    client, statements = home
    assert get(client, '&keyword=空主题').status_code == 200
    assert not any('SELECT id,' in sql for sql, _ in statements)


def test_filters_apply_to_counts_and_list_and_links(home):
    client, _ = home
    text = get(client, '&score_min=4&source=来源甲&sourceapi=api-a').get_data(as_text=True)
    assert '新闻-121' in text and '共 121 条' in text
    assert 'score_min=4' in text and 'sourceapi=api-a' in text
    text = get(client, '&source=不存在').get_data(as_text=True)
    assert '共 0 条' in text and '新闻-121' not in text


@pytest.mark.parametrize('extra', ['&page=0', '&page=-1', '&page=abc', '&page=1000001', '&score_min=x', '&score_max=6', '&score_min=4&score_max=2', '&keyword=已删除', '&date_from=bad'])
def test_bad_parameters_do_not_query(home, extra):
    client, statements = home
    if 'date_from=bad' in extra:
        response = client.get('/?date_from=bad')
    else:
        response = get(client, extra)
    assert response.status_code == 400
    assert not statements


def test_empty_config(home):
    client, _ = home
    with patch.object(views, 'value', return_value=[]):
        response = get(client)
    assert response.status_code == 200
    assert '该日期范围内无数据' in response.get_data(as_text=True)


@pytest.mark.parametrize('kind,fragment', [('date', '(`fetchdate`)'), ('datetime(6)', '(`fetchdate`)'), ('varchar(10)', '(`fetchdate`)'), ('varchar(255)', '(`fetchdate`(10))'), ('text', '(`fetchdate`(10))')])
def test_index_plan_types(kind, fragment):
    from home_index_cli import index_plan
    sql = index_plan('news', [{'Field': 'fetchdate', 'Type': kind}], [])
    assert fragment in sql and 'LOCK=NONE' in sql and 'ALGORITHM=INPLACE' in sql
    assert 'DROP' not in sql


def test_index_plan_is_idempotent_and_checks_visibility():
    from home_index_cli import index_plan
    columns = [{'Field': 'fetchdate', 'Type': 'date'}]
    index = dict(Key_name='other_name', Column_name='fetchdate', Seq_in_index=1,
                 Index_type='BTREE', Sub_part=None, Visible='YES')
    assert index_plan('news', columns, [index]) is None
    assert index_plan('news', columns, [{**index, 'Visible': 'NO'}]) is not None
    assert index_plan('news', columns, [{**index, 'Seq_in_index': 2}]) is not None
    assert index_plan('news', columns, [{**index, 'Sub_part': 5}]) is not None


@pytest.mark.parametrize('table,columns,indexes', [
    ('news;DROP', [{'Field': 'fetchdate', 'Type': 'date'}], []),
    ('news', [], []),
    ('news', [{'Field': 'fetchdate', 'Type': 'int'}], []),
    ('news', [{'Field': 'fetchdate', 'Type': 'varchar(5)'}], []),
    ('news', [{'Field': 'fetchdate', 'Type': 'date'}], [{'Key_name': 'idx_scored_news_fetchdate', 'Column_name': 'id'}]),
])
def test_unsafe_index_plan_refused(table, columns, indexes):
    from home_index_cli import index_plan
    with pytest.raises(ValueError):
        index_plan(table, columns, indexes)


@pytest.mark.parametrize('apply,existing', [(False, False), (True, False), (False, True), (True, True)])
def test_index_cli_only_writes_when_explicitly_needed(apply, existing):
    from unittest.mock import MagicMock
    import home_index_cli
    columns = [{'Field': 'fetchdate', 'Type': 'date'}]
    index = dict(Key_name='idx_scored_news_fetchdate', Column_name='fetchdate', Seq_in_index=1,
                 Index_type='BTREE', Sub_part=None)
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {'version': '8.0'}
    cursor.fetchall.side_effect = [columns, [index] if existing else [], [index]]
    with patch('sys.argv', ['home_index_cli.py'] + (['--apply'] if apply else [])), patch('db_utils.get_connection', return_value=connection), patch('db_utils.get_table_name', return_value='news'):
        home_index_cli.main()
    mutations = [call.args[0] for call in cursor.execute.call_args_list if call.args[0].startswith('ALTER')]
    assert len(mutations) == int(apply and not existing)
    connection.close.assert_called_once()


def test_index_cli_does_not_fallback_after_ddl_error():
    from unittest.mock import MagicMock
    import home_index_cli
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {'version': '8.0'}
    cursor.fetchall.side_effect = [[{'Field': 'fetchdate', 'Type': 'date'}], []]
    def execute(sql):
        if sql.startswith('ALTER'):
            raise RuntimeError('LOCK=NONE unsupported')
    cursor.execute.side_effect = execute
    with patch('sys.argv', ['home_index_cli.py', '--apply']), patch('db_utils.get_connection', return_value=connection), patch('db_utils.get_table_name', return_value='news'), pytest.raises(RuntimeError):
        home_index_cli.main()
    assert sum(call.args[0].startswith('ALTER') for call in cursor.execute.call_args_list) == 1
    connection.close.assert_called_once()
