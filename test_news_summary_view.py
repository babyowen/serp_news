"""Real relational query boundaries for on-demand full summaries."""
import sqlite3
from pathlib import Path
from unittest.mock import patch
import pytest
from flask import Flask
from routes import views
from topic_config import TOPIC

@pytest.fixture
def page():
    db=sqlite3.connect(":memory:"); db.row_factory=sqlite3.Row
    db.execute("CREATE TABLE news (id INTEGER PRIMARY KEY,keyword TEXT,title TEXT,link TEXT,source TEXT,fetchdate TEXT,sourceapi TEXT,score INTEGER,short_summary TEXT,search_keyword TEXT)")
    for number in range(1,52):
        db.execute("INSERT INTO news VALUES (?,?,?,?,?,?,?,?,?,?)",
            (number,TOPIC,f"新闻{number}","https://example.test","来源","2026-10-01","api",None if number==1 else 3,
             "<script>alert(1)</script>\n"+"完整摘要"*100 if number==1 else None,"公物仓"))
    db.execute("INSERT INTO news VALUES (99,'已删除','隐藏','','','','',4,'不可见','')")
    statements=[]
    class Cursor:
        def execute(self,sql,params=()):
            statements.append((sql,params))
            self.cursor=db.execute(sql.replace("%s","?").replace("LEFT(short_summary, 101)","substr(short_summary,1,101)"),params)
        def fetchall(self): return [dict(r) for r in self.cursor.fetchall()]
        def fetchone(self):
            row=self.cursor.fetchone()
            return dict(row) if row else None
        def close(self): pass
    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass
    app=Flask(__name__,template_folder=str(Path(__file__).parent/"templates"),static_folder=str(Path(__file__).parent/"static"))
    app.register_blueprint(views.views_bp); app.config["TESTING"]=True
    with patch.object(views,"get_connection",return_value=Connection()),patch.object(views,"get_table_name",return_value="news"),patch.object(views,"value",return_value=[TOPIC,"空主题"]):
        yield app.test_client(),statements
    db.close()

def test_summary_returns_full_text_and_first_search_term(page):
    client,statements=page
    result=client.get("/api/news/1/summary")
    assert result.status_code==200
    assert len(result.json["short_summary"])>400 and result.json["search_keyword"]=="公物仓"
    assert "content" not in result.json
    assert "keyword IN" in statements[-1][0] and TOPIC in statements[-1][1]

@pytest.mark.parametrize("rid",[99,999])
def test_summary_unavailable_or_hidden_topic_is_404(page,rid):
    assert page[0].get(f"/api/news/{rid}/summary").status_code==404

def test_no_summary_keeps_null_in_response(page):
    result=page[0].get("/api/news/2/summary")
    assert result.status_code==200 and result.json["short_summary"] is None

def test_topic_pagination_and_null_label(page):
    client,statements=page
    prefix="/?date_from=2026-10-01&date_to=2026-10-01&keyword="+TOPIC
    first=client.get(prefix).get_data(as_text=True)
    second=client.get(prefix+"&page=2").get_data(as_text=True)
    assert first.count('class="news-summary"')==50
    assert second.count('class="news-summary"')==1
    assert "未完成评分" in second
    assert "<script>alert(1)</script>" not in second
    assert "完整摘要"*100 not in second
    assert "查看摘要/检索来源" in first
    assert all("LIMIT %s OFFSET %s" in sql for sql,_ in statements if sql.startswith("SELECT id, keyword, title, link"))

def test_empty_topic_keeps_visible_zero_state(page):
    result=page[0].get("/?date_from=2026-10-01&date_to=2026-10-01&keyword=空主题")
    assert result.status_code==200
    assert '共 0 条' in result.get_data(as_text=True)
