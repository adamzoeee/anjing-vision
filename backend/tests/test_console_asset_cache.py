"""控制台静态资源缓存回归（2026-10-14 复盘：用户刷新后仍看到旧前端）。

服务端必须保证前端改动"刷新即生效"：
1. `/console` 的 index.html 里资产 URL 带文件版本（?v=mtime），文件一改 URL 就变；
2. `/console` 使用 `Cache-Control: no-cache` 回源校验入口；
   `/console-static/*`、`/preview-static/*` 使用 `no-store`，不存储静态响应。
"""


def test_console_page_versions_assets_and_revalidates(client):
    page = client.get("/console")
    assert page.status_code == 200
    assert "js/app.js?v=" in page.text, "入口资产 URL 必须带文件版本"
    assert "css/views.css?v=" in page.text, "样式同样要带版本"
    assert page.headers.get("cache-control") == "no-cache"


def test_console_static_assets_are_no_store(client):
    for path in ("/console-static/js/app.js", "/console-static/css/views.css", "/console-static/css/assistant.css", "/console-static/js/views/assistant.js"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert res.headers.get("cache-control") == "no-store", f"{path} 不得缓存旧资源"


def test_console_es_modules_use_javascript_mime_even_with_windows_registry(client, monkeypatch):
    import mimetypes

    # Windows can register .mjs as text/plain: browsers then reject the whole
    # import graph even though the HTTP requests return 200.
    mimetypes.init()
    monkeypatch.setitem(mimetypes.types_map, '.mjs', 'text/plain')
    for path in ('/console-static/js/home-plan.mjs',
                 '/console-static/js/views/assistant_model.mjs',
                 '/console-static/js/viewer/scheme3d.mjs'):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers['content-type'].split(';')[0] in {
            'text/javascript', 'application/javascript',
        }
