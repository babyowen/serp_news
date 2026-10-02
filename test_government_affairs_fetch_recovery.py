import json
from pathlib import Path
from unittest.mock import Mock
import pytest
import government_affairs_pipeline as pipeline
import news_fetcher as fetcher
import fetch_and_filter as filtering

def test_resume_after_content_enrichment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rows=[{"title":"title","link":"https://finance.people.com.cn/a"},{"title":"video","link":"https://tv.cctv.com/a"}]
    def search(term,path):
        Path(path).write_text(json.dumps(rows))
        return True
    assert pipeline.collect("2099-01-01",["公物仓"],search)
    path=Path("output/2099-01-01/2099-01-01_江苏机关事务.json")
    enriched=json.loads(path.read_text())[:1]
    enriched[0].update(content="正文",wordcount=2,custom_grab=False)
    enriched[0]["link"] = "http://finance.people.com.cn/a"
    path.write_text(json.dumps(enriched))
    assert pipeline.collect("2099-01-01",["公物仓"],Mock(side_effect=AssertionError("must reuse collection")))
    enriched[0]["link"]="https://unrelated.test"
    path.write_text(json.dumps(enriched))
    assert not pipeline.collect("2099-01-01",["公物仓"],Mock())

@pytest.mark.parametrize("name",["google","baidu","bing","duckduckgo"])
@pytest.mark.parametrize("mode",["timeout","api_error"])
def test_real_fetcher_marks_exhausted_provider_failures(monkeypatch,name,mode):
    monkeypatch.setattr(fetcher.time,"sleep",lambda _:None)
    monkeypatch.setattr(fetcher,"log_error",lambda *a:None)
    response=Mock()
    response.json.return_value={"error":"provider unavailable"}
    monkeypatch.setattr(fetcher.requests,"get",Mock(side_effect=TimeoutError() if mode=="timeout" else None, return_value=response))
    result=getattr(fetcher,f"fetch_serpapi_{name}_news")("term")
    assert result.get("_fetch_failed") is True

def test_partial_provider_output_is_saved_but_task_fails(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_LOG_PATH",str(tmp_path/"run.log"))
    for name in ["google","baidu","bing","duckduckgo"]:
        monkeypatch.setattr(filtering,f"fetch_serpapi_{name}_news",lambda *a,**k: {})
    monkeypatch.setattr(filtering,"fetch_serpapi_google_news",lambda *a,**k: {"_fetch_failed":True,"news_results":[{"title":"资产","link":"https://test/a","date":"2026-10-01"}]})
    monkeypatch.setattr("sys.argv",["fetch_and_filter.py","公物仓","2026-10-01","--main_keyword","江苏机关事务","--output","rows.json"])
    assert filtering.main() is False
    assert len(json.loads(Path("rows.json").read_text()))==1
