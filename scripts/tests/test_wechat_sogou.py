import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import unittest

import wechat_sogou as ws

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name):
    with open(os.path.join(FIX, name), encoding="utf-8", errors="ignore") as f:
        return f.read()


class TestParseSearch(unittest.TestCase):
    def test_rows(self):
        rows = ws.parse_search(_read("sogou_search_sample.html"))
        self.assertEqual(len(rows), 8)
        first = rows[0]
        self.assertEqual(first["account"], "菜花来了")
        self.assertEqual(first["title"], "自古“忠义”难两全 —— 也评鲁南制药股权争议")
        self.assertEqual(first["ts"], 1627459777)
        self.assertTrue(first["link"].startswith("/link?url="))
        self.assertNotIn("&amp;", first["link"])

    def test_captcha_detection(self):
        self.assertTrue(ws.is_captcha('<form action="/antispider/thank.php">'))
        self.assertFalse(ws.is_captcha(_read("sogou_search_sample.html")))


class TestResolveLink(unittest.TestCase):
    def test_js_concatenation(self):
        page = """<script>var url = '';
            url += 'https://mp.';
            url += 'weixin.qq.c';
            url += 'om/s?src=11&timest@amp=1&sig@nature=abc';
            url.replace("@", "");
            window.location.replace(url)</script>"""
        self.assertEqual(ws.resolve_sogou_redirect(page), "https://mp.weixin.qq.com/s?src=11&timestamp=1&signature=abc")

    def test_no_url(self):
        self.assertIsNone(ws.resolve_sogou_redirect("<html></html>"))


ARTICLE_FIX = os.path.exists(os.path.join(FIX, "wx_article_sample.html"))  # 文章样本涉及版权，不随仓库分发


@unittest.skipUnless(ARTICLE_FIX, "需要本地文章样本 tests/fixtures/wx_article_sample.html")
class TestParseArticle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = ws.parse_article(_read("wx_article_sample.html"))

    def test_identity(self):
        a = self.a
        self.assertEqual(a["nickname"], "明税")
        self.assertEqual(a["biz"], "MjM5MTU4MjcyMQ==")
        self.assertEqual(a["mid"], "2650026054")
        self.assertEqual(a["idx"], "1")
        self.assertEqual(a["sn"], "e54c7f021301e9ad14054b2f3f8eb9dc")
        self.assertEqual(a["key"], "MjM5MTU4MjcyMQ==_2650026054_1")
        self.assertEqual(a["perm_url"],
                         "https://mp.weixin.qq.com/s?__biz=MjM5MTU4MjcyMQ==&mid=2650026054&idx=1"
                         "&sn=e54c7f021301e9ad14054b2f3f8eb9dc")

    def test_title_time_body(self):
        a = self.a
        self.assertEqual(a["title"], "刚刚曝光的5起骗取出口退税及税务人员违纪违法案件查处细节来了！")
        self.assertEqual(a["ts"], 1790757253)
        self.assertIn("9月28日，税务部门曝光5起骗取出口退税及税务人员违纪违法案件", a["text"])
        self.assertGreater(len(a["text"]), 3000)
        self.assertTrue(all(u.startswith("http") for u in a["images"]))
        self.assertGreater(len(a["images"]), 0)

    def test_markdown_references_images_in_order(self):
        md = self.a["markdown"]
        self.assertIn("![](images/01.", md)
        self.assertNotIn("<", md.split("\n")[0])


@unittest.skipUnless(ARTICLE_FIX, "需要本地文章样本")
class TestNoSn(unittest.TestCase):
    def test_page_without_sn_has_no_perm_url(self):
        # 搜狗临时签名链接打开的页面里 sn 为空，拼出的链接会"参数错误"，必须留空
        html = _read("wx_article_sample.html").replace(
            'var sn = "e54c7f021301e9ad14054b2f3f8eb9dc"', 'var sn = ""')
        a = ws.parse_article(html)
        self.assertEqual(a["sn"], "")
        self.assertEqual(a["perm_url"], "")
        self.assertEqual(a["key"], "MjM5MTU4MjcyMQ==_2650026054_1")  # 去重键不受影响


class TestSafeName(unittest.TestCase):
    def test_strips_path_chars(self):
        self.assertEqual(ws.safe_name('a/b\\c:d*e?"f<g>h|i'), "a_b_c_d_e__f_g_h_i")
        self.assertLessEqual(len(ws.safe_name("税" * 200)), 60)


if __name__ == "__main__":
    unittest.main()
