"""fetch_news の中継URL解決まわりのテスト（python -m unittest test_fetch_news）。"""

import json
import unittest

import fetch_news


def _google_response(url):
    """news.google.com の batchexecute 応答を模した文字列を作る。

    実際の応答と同じく、内側の JSON 文字列が外側でもう一度エスケープされ、
    "=" と "&" は \\u003d / \\u0026 になる。
    """
    inner = json.dumps(["garturlres", url, 1], separators=(",", ":"))
    row = json.dumps([["wrb.fr", "Fbv4je", inner, None, None, None, "generic"],
                      ["di", 42], ["af.httprm", 41, "-123", 7]],
                     separators=(",", ":"))
    row = row.replace("=", "\\u003d").replace("&", "\\u0026")
    return ")]}'\n\n" + str(len(row)) + "\n" + row + "\n25\n[[\"e\",4,null,null,139]]\n"


class ExtractGarturlresTest(unittest.TestCase):
    def test_returns_full_url_with_equals_and_ampersand(self):
        url = "https://www.nli-research.co.jp/report/detail/id=86895?site=nli&x=1"
        res = _google_response(url)
        # 前提：応答中では "=" "&" がエスケープされている（旧実装はここで切れていた）
        self.assertIn("\\u003d", res)
        self.assertIn("\\u0026", res)
        self.assertEqual(fetch_news._extract_garturlres(res), url)

    def test_query_only_url(self):
        url = "https://kabutan.jp/news/marketnews/?b=n202609251041"
        self.assertEqual(fetch_news._extract_garturlres(_google_response(url)), url)

    def test_returns_none_without_garturlres(self):
        self.assertIsNone(fetch_news._extract_garturlres(")]}'\n\n10\n[[\"e\",4]]\n"))


class LooksCompleteTest(unittest.TestCase):
    def test_accepts_complete_urls(self):
        for url in ("https://www.nli-research.co.jp/report/detail/id=86895?site=nli",
                    "https://kabutan.jp/news/marketnews/?b=n202609251041",
                    "https://example.com/a/b"):
            self.assertTrue(fetch_news._looks_complete(url), url)

    def test_rejects_truncated_or_relay_urls(self):
        for url in ("https://kabutan.jp/news/marketnews/?",
                    "https://example.com/?a=1&",
                    "https://example.com/?a=",
                    "https://example.com/id\\u003d1",
                    "https://news.google.com/rss/articles/CBMi",
                    "ftp://example.com/x"):
            self.assertFalse(fetch_news._looks_complete(url), url)


if __name__ == "__main__":
    unittest.main()
