"""New topic collection/summary integration without live services."""
import importlib
import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import main
import news_item_summarizer as summary
from topic_config import TOPIC

def test_partial_collection_stays_failed_on_resume(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "SEARCH_KEYWORDS", {TOPIC:["机关事务管理","碳普惠","公物仓"]})
    monkeypatch.setattr("runtime_config.get_snapshot",lambda:SimpleNamespace(token="test:1"))
    calls=[]
    def fetch(command,*a,**k):
        argv=shlex.split(command); term=argv[2]; calls.append(term)
        assert argv[argv.index("--main_keyword")+1] == TOPIC
        if term=="碳普惠": return False
        output=Path(argv[argv.index("--output")+1]); output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps([{"title":"同一新闻","link":"https://test/1"}]))
        return True
    monkeypatch.setattr(main,"safe_subprocess_run",fetch)
    assert main.execute_news_fetching("2099-01-01",TOPIC) is False
    rows=json.loads(Path(f"output/2099-01-01/2099-01-01_{TOPIC}.json").read_text())
    assert len(rows)==1 and rows[0]["search_keyword"]=="机关事务管理" and rows[0]["keyword"]==TOPIC
    diagnostic=Path("output/2099-01-01/diagnostics/government_affairs_fetch.json")
    assert diagnostic.is_file()
    record=json.loads(diagnostic.read_text())
    assert [r["status"] for r in record["searches"]]==["ok","failed","ok"]
    assert main.execute_news_fetching("2099-01-01",TOPIC) is False
    assert len(calls)==3

def test_empty_success_is_not_search_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main,"SEARCH_KEYWORDS",{TOPIC:["公物仓"]})
    monkeypatch.setattr("runtime_config.get_snapshot",lambda:SimpleNamespace(token="test:1"))
    def fetch(command,*a,**kw):
        argv=shlex.split(command); path=Path(argv[-1]); path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text("[]"); return True
    monkeypatch.setattr(main,"safe_subprocess_run",fetch)
    assert main.execute_news_fetching("2099-01-01",TOPIC) is True
    assert main.execute_news_fetching("2099-01-01",TOPIC) is True
    diagnostic=json.loads(Path("output/2099-01-01/diagnostics/government_affairs_fetch.json").read_text())
    assert diagnostic["searches"][0]["status"]=="empty"

class SummaryCursor:
    def __init__(self, text):
        self.rows=[(1,"标题",text,TOPIC)]
        self.queries=[]; self.written=[]
    def execute(self,sql,params=None):
        self.queries.append((sql,params))
        if sql.startswith("UPDATE"):
            self.written.append(params[0]); self.rows=[]
    def fetchall(self): return self.rows
    def close(self): pass

@pytest.mark.parametrize("length,answers,want", [(500,[],True),(501,[None,None,None],False),(501,[None,"忠实摘要"],True)])
def test_summary_final_failure_and_short_content(tmp_path,monkeypatch,length,answers,want):
    monkeypatch.chdir(tmp_path); Path("output").mkdir()
    cursor=SummaryCursor("文"*length)
    conn=Mock(); conn.cursor.return_value=cursor
    monkeypatch.setattr(summary,"get_conn",lambda:conn)
    monkeypatch.setattr(summary,"prepare_batch",lambda *a,**k:(None,[TOPIC]))
    monkeypatch.setattr(summary,"table_has_region_column",lambda *a:False)
    monkeypatch.setattr(summary,"table_has_business_types_column",lambda *a:False)
    monkeypatch.setattr(summary.time,"sleep",lambda _:None)
    queue=iter(answers)
    monkeypatch.setattr(summary,"call_llm",lambda *a:next(queue))
    monkeypatch.setattr(sys,"argv",["news_item_summarizer.py","2099-01-01","--keyword",TOPIC])
    assert summary.main() is want
    assert (len(cursor.written)==1) is want
    if length==500: assert cursor.written==["文"*500]
    assert "score>=3" in cursor.queries[0][0] and cursor.queries[0][1][-1]==TOPIC

def test_no_history_is_explicitly_reported(capsys):
    import news_volume_alert as alert
    sent=[]
    assert alert.run("2099-01-01",[TOPIC],query_fn=lambda *a:(0,0),send_fn=sent.append)==[]
    assert sent==[]
    assert "暂无历史基线" in capsys.readouterr().out
