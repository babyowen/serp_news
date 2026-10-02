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
@pytest.mark.parametrize("mode",["timeout","api_error","search_error","http_error"])
def test_real_fetcher_marks_exhausted_provider_failures(monkeypatch,name,mode):
    monkeypatch.setattr(fetcher.time,"sleep",lambda _:None)
    monkeypatch.setattr(fetcher,"log_error",lambda *a:None)
    response=Mock()
    response.json.return_value={"error":"provider unavailable"}
    if mode == "search_error":
        response.json.return_value["search_metadata"] = {"status": "Error"}
    if mode == "http_error":
        response.raise_for_status.side_effect = fetcher.requests.HTTPError("429 rate limit")
    request = Mock(side_effect=TimeoutError() if mode=="timeout" else None, return_value=response)
    monkeypatch.setattr(fetcher.requests,"get",request)
    result=getattr(fetcher,f"fetch_serpapi_{name}_news")("term")
    assert result.get("_fetch_failed") is True
    assert request.call_count == 3


def test_partial_provider_output_is_saved_but_task_fails(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_LOG_PATH",str(tmp_path/"run.log"))
    for name in ["google","baidu","bing","duckduckgo"]:
        monkeypatch.setattr(filtering,f"fetch_serpapi_{name}_news",lambda *a,**k: {})
    monkeypatch.setattr(filtering,"fetch_serpapi_google_news",lambda *a,**k: {"_fetch_failed":True,"news_results":[{"title":"资产","link":"https://test/a","date":"2026-10-01"}]})
    monkeypatch.setattr("sys.argv",["fetch_and_filter.py","公物仓","2026-10-01","--main_keyword","江苏机关事务","--output","rows.json"])
    assert filtering.main() is False
    assert len(json.loads(Path("rows.json").read_text()))==1


def empty_search_response(name):
    result_type = "organic" if name in ("baidu", "bing") else "news"
    return {
        "search_metadata": {"status": "Success"},
        "search_information": {f"{result_type}_results_state": "Fully empty"},
        "error": f"{name} hasn't returned any results for this query.",
    }


@pytest.mark.parametrize("name", ["google", "baidu", "bing", "duckduckgo"])
@pytest.mark.parametrize("explicit_empty_list", [False, True])
def test_successful_empty_search_is_not_retried_or_failed(monkeypatch, name, explicit_empty_list):
    payload = empty_search_response(name)
    result_key = "organic_results" if name in ("baidu", "bing") else "news_results"
    if explicit_empty_list:
        payload[result_key] = []
    response = Mock()
    response.json.return_value = payload
    request = Mock(return_value=response)
    sleep = Mock()
    log_error = Mock()
    monkeypatch.setattr(fetcher.requests, "get", request)
    monkeypatch.setattr(fetcher.time, "sleep", sleep)
    monkeypatch.setattr(fetcher, "log_error", log_error)

    # Use the normal pagination limit: the first empty page ends the search.
    result = getattr(fetcher, f"fetch_serpapi_{name}_news")("term")

    assert not result.get("_fetch_failed")
    assert result.get(result_key, []) == []
    assert request.call_count == 1
    sleep.assert_not_called()
    log_error.assert_not_called()


@pytest.mark.parametrize("name", ["google", "duckduckgo"])
def test_successful_empty_later_page_preserves_prior_results(monkeypatch, name):
    rows = [{"title": "资产管理", "link": "https://example.test/news", "date": "2026-10-01"}]
    response = Mock()
    response.json.side_effect = [
        {"search_metadata": {"status": "Success"}, "news_results": rows},
        empty_search_response(name),
    ]
    request = Mock(return_value=response)
    sleep = Mock()
    log_error = Mock()
    monkeypatch.setattr(fetcher.requests, "get", request)
    monkeypatch.setattr(fetcher.time, "sleep", sleep)
    monkeypatch.setattr(fetcher, "log_error", log_error)

    result = getattr(fetcher, f"fetch_serpapi_{name}_news")("term", max_pages=3)

    assert not result.get("_fetch_failed")
    assert result["news_results"] == rows
    assert request.call_count == 2
    sleep.assert_not_called()
    log_error.assert_not_called()


@pytest.mark.parametrize("empty_engine", ["all", "google", "baidu", "bing", "duckduckgo"])
def test_successful_empty_search_allows_collection_and_resume(tmp_path, monkeypatch, empty_engine):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_LOG_PATH", str(tmp_path / "run.log"))
    monkeypatch.setattr(fetcher.time, "sleep", lambda _: None)
    monkeypatch.setattr(fetcher, "log_error", lambda *args: None)

    def search_response(url, *, params, timeout):
        name = params["engine"].removesuffix("_news")
        if empty_engine in ("all", name) or params.get("start", 0) > 0:
            payload = empty_search_response(name)
        else:
            result_key = "organic_results" if name in ("baidu", "bing") else "news_results"
            payload = {
                "search_metadata": {"status": "Success"},
                result_key: [{"title": f"资产管理 {name}", "link": f"https://example.test/{name}",
                              "date": "2026-10-01"}],
            }
        response = Mock()
        response.json.return_value = payload
        return response

    monkeypatch.setattr(fetcher.requests, "get", search_response)

    def run_search(term, path):
        monkeypatch.setattr("sys.argv", ["fetch_and_filter.py", term, "2026-10-01",
                                       "--main_keyword", "江苏机关事务", "--output", path])
        return filtering.main()

    assert pipeline.collect("2026-10-01", ["公物仓"], run_search)
    folder = Path("output/2026-10-01")
    rows = json.loads((folder / "2026-10-01_江苏机关事务.json").read_text())
    expected_count = 0 if empty_engine == "all" else 3
    assert len(rows) == expected_count
    diagnostic = json.loads((folder / "diagnostics/government_affairs_fetch.json").read_text())
    assert diagnostic["searches"] == [{"search_keyword": "公物仓",
                                       "status": "empty" if empty_engine == "all" else "ok",
                                       "count": expected_count, "error": None}]

    # A completed empty search is reusable, without another provider request.
    resumed_search = Mock(side_effect=AssertionError("must reuse successful collection"))
    assert pipeline.collect("2026-10-01", ["公物仓"], resumed_search)
    resumed_search.assert_not_called()
