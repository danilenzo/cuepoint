"""Extended tests for db.py — CRUD operations, batch save, API results, metrics."""

from cuepoint import db as store


def test_get_all_artist_urls(tmp_db):
    store.save_artist_url("a1", {"name": "One"})
    store.save_artist_url("a2", {"name": "Two"})
    all_urls = store.get_all_artist_urls()
    assert len(all_urls) == 2
    assert all_urls["a1"]["name"] == "One"
    assert all_urls["a2"]["name"] == "Two"


def test_get_all_artist_urls_empty(tmp_db):
    assert store.get_all_artist_urls() == {}


def test_has_cached_artist(tmp_db):
    assert store.has_cached_artist("x") is False
    store.save_cached_artist("x", {"name": "X"})
    assert store.has_cached_artist("x") is True


def test_delete_cached_artist(tmp_db):
    store.save_cached_artist("x", {"name": "X"})
    assert store.get_cached_artist("x") is not None
    store.delete_cached_artist("x")
    assert store.get_cached_artist("x") is None


def test_batch_save_enriched(tmp_db):
    items = [
        ("a1", {"name": "One", "sc_followers": 100}, 100, 10),
        ("a2", {"name": "Two", "sc_followers": 200}, 200, 20),
    ]
    store.batch_save_enriched(items)

    c1 = store.get_cached_artist("a1")
    assert c1 is not None
    assert c1[0]["name"] == "One"

    m1 = store.get_artist_metrics("a1")
    assert m1 is not None
    assert m1[0] == 100
    assert m1[1] == 10

    m2 = store.get_artist_metrics("a2")
    assert m2 is not None
    assert m2[0] == 200


def test_batch_save_enriched_empty(tmp_db):
    store.batch_save_enriched([])


def test_save_and_get_artist_metrics(tmp_db):
    assert store.get_artist_metrics("a1") is None
    store.save_artist_metrics("a1", 500, 30)
    result = store.get_artist_metrics("a1")
    assert result is not None
    sc, dc, _recorded_at = result
    assert sc == 500
    assert dc == 30


def test_save_artist_metrics_none_values(tmp_db):
    store.save_artist_metrics("a1", None, None)
    result = store.get_artist_metrics("a1")
    assert result is not None
    assert result[0] is None
    assert result[1] is None


def test_save_and_get_api_results(tmp_db):
    events = [{"id": "e1", "title": "Event 1"}, {"id": "e2", "title": "Event 2"}]
    store.save_api_results("berlin", events)
    result = store.get_api_results("berlin")
    assert result is not None
    assert len(result) == 2
    assert result[0]["title"] == "Event 1"


def test_get_api_results_unknown_city(tmp_db):
    assert store.get_api_results("narnia") is None


def test_save_api_results_replaces(tmp_db):
    store.save_api_results("berlin", [{"id": "e1"}])
    store.save_api_results("berlin", [{"id": "e2"}, {"id": "e3"}])
    result = store.get_api_results("berlin")
    assert result is not None
    assert len(result) == 2
    assert result[0]["id"] == "e2"


def test_close_db_idempotent(tmp_db):
    store.close_db()
    store.close_db()


def test_close_db_reopens(tmp_db):
    store.save_artist_url("a1", {"name": "Test"})
    store.close_db()
    result = store.get_artist_url("a1")
    assert result is not None
    assert result["name"] == "Test"


def test_artist_url_upsert(tmp_db):
    store.save_artist_url("a1", {"name": "Old"})
    store.save_artist_url("a1", {"name": "New"})
    result = store.get_artist_url("a1")
    assert result["name"] == "New"
