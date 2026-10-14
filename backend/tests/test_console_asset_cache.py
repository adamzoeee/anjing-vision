"""控制台静态资源缓存回归（2026-10-14 复盘：用户刷新后仍看到旧前端）。

服务端必须保证前端改动"刷新即生效"：
1. `/console` 的 index.html 里资产 URL 带文件版本（?v=mtime），文件一改 URL 就变；
2. `/console`、`/console-static/*`、`/preview-static/*` 一律 `Cache-Control: no-cache`
   （每次使用前回源校验），不允许浏览器在用户不知情的情况下长期拿旧前端。
"""


def test_console_page_versions_assets_and_revalidates(client):
    page = client.get("/console")
    assert page.status_code == 200
    assert "js/app.js?v=" in page.text, "入口资产 URL 必须带文件版本"
    assert "css/views.css?v=" in page.text, "样式同样要带版本"
    assert page.headers.get("cache-control") == "no-cache"


def test_console_static_assets_are_no_cache(client):
    for path in ("/console-static/js/app.js", "/console-static/css/views.css"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert res.headers.get("cache-control") == "no-cache", f"{path} 必须回源校验"
