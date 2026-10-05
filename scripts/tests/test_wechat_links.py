import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import unittest

import wechat_links as wl


class TestExtractLinks(unittest.TestCase):
    def test_full_and_short_links(self):
        text = ("看这篇 https://mp.weixin.qq.com/s?__biz=MzIwMTQwMzMzNQ==&mid=2247483758&idx=1&sn=abc&chksm=x#rd\n"
                "还有 https://mp.weixin.qq.com/s/tQfvf-WCVzrFKb55VEPi8g 。")
        self.assertEqual(wl.extract_links(text), [
            "https://mp.weixin.qq.com/s?__biz=MzIwMTQwMzMzNQ==&mid=2247483758&idx=1&sn=abc&chksm=x",
            "https://mp.weixin.qq.com/s/tQfvf-WCVzrFKb55VEPi8g",
        ])

    def test_http_upgraded_and_amp_unescaped(self):
        self.assertEqual(wl.extract_links("http://mp.weixin.qq.com/s?__biz=A&amp;mid=1&amp;idx=1&amp;sn=z"),
                         ["https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&sn=z"])

    def test_ignores_other_urls(self):
        self.assertEqual(wl.extract_links("https://weixin.sogou.com/link?url=x https://example.com"), [])


class TestInternalLinks(unittest.TestCase):
    def test_link_key(self):
        self.assertEqual(wl.link_key("https://mp.weixin.qq.com/s?__biz=MzIwMTQwMzMzNQ==&mid=2247488535&idx=2&sn=da8b&chksm=x"),
                         "MzIwMTQwMzMzNQ==_2247488535_2")
        self.assertIsNone(wl.link_key("https://mp.weixin.qq.com/s/_2kC-fXw7UjneZSrsC9CVQ"))  # 短链拿不到去重键
        self.assertIsNone(wl.link_key("https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1"))  # 缺 sn 打不开

    def test_same_account_links_from_saved_page(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "fixtures", "wx_article_sample.html")
        with open(path, encoding="utf-8", errors="ignore") as f:
            html = f.read()
        keys = {wl.link_key(u) for u in wl.internal_links(html, {"MjM5MTU4MjcyMQ=="})}
        self.assertTrue({"MjM5MTU4MjcyMQ==_2650025074_1", "MjM5MTU4MjcyMQ==_2650025601_1",
                         "MjM5MTU4MjcyMQ==_2650026037_1"} <= keys)
        self.assertNotIn(None, keys)
        self.assertEqual(wl.internal_links(html, {"OTHER=="}), [])



class TestVerifyPage(unittest.TestCase):
    def test_detects_wechat_verification_page(self):
        import os
        import wechat_sogou as ws
        fix = os.path.join(os.path.dirname(__file__), "fixtures")
        with open(os.path.join(fix, "wx_verify_page.html"), encoding="utf-8") as f:
            self.assertTrue(ws.is_wechat_verify(f.read()))
        self.assertFalse(ws.is_wechat_verify("<html><div id=\"js_content\">正文</div></html>"))

    def test_article_interval_is_gentle(self):
        import wechat_sogou as ws
        self.assertGreaterEqual(ws.INTERVALS["mp.weixin.qq.com"][0], 8)


class TestAlbum(unittest.TestCase):
    def test_parse_album_page(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "fixtures", "wx_album_page1.json")
        with open(path, encoding="utf-8") as f:
            items, more, title, total = wl.parse_album(f.read())
        self.assertEqual(title, "新法速递")
        self.assertEqual(total, 28)
        self.assertTrue(more)
        self.assertEqual(len(items), 20)
        self.assertTrue(all(wl.link_key(i["url"]) for i in items))
        self.assertTrue(items[0]["url"].startswith("https://"))
        self.assertIn("msgid", items[-1])

    def test_album_ids_from_page(self):
        html = 'xx album_id=2747264301501136896&amp;scene yy "album_id":"3106558135978311680" zz'
        self.assertEqual(wl.album_ids(html), ["2747264301501136896", "3106558135978311680"])


class TestJsEscapedLinks(unittest.TestCase):
    def test_x26_escaped_links_in_script(self):
        text = 'link:"http:\\/\\/mp.weixin.qq.com\\/s?__biz=MzIwMTQwMzMzNQ==\\x26amp;mid=2247489883\\x26amp;idx=1\\x26amp;sn=01cd362a8194307ff0a4317af8953d46\\x26amp;scene=21"'
        self.assertEqual(wl.link_key(wl.extract_links(text)[0]), "MzIwMTQwMzMzNQ==_2247489883_1")


class TestExternalLinks(unittest.TestCase):
    PAGE = (
        '<link href="https://res.wx.qq.com/x.css">'
        '<script>var msg_source_url = \'https://www.chinatax.gov.cn/chinatax/n363/c5211371/content.html\';</script>'
        '<div class="rich_media_content" id="js_content">'
        '<a href="https://mp.weixin.qq.com/s?__biz=MzU5MDUzMTk5Nw==&amp;mid=1&amp;idx=1&amp;sn=aa">案例</a>'
        '<a href="https://mp.weixin.qq.com/s?__biz=MzIwMTQwMzMzNQ==&amp;mid=2&amp;idx=1&amp;sn=bb">自家旧文</a>'
        '<a href="http://szs.mof.gov.cn/zhengcefabu/202308/t20230802_3899800.htm">财政部</a>'
        '<img data-src="https://mmbiz.qpic.cn/a.jpg">'
        '</div><div id="js_pc_qr_code"><a href="https://example.com/footer">footer</a></div>'
    )

    def test_split_other_wechat_and_web(self):
        other, web = wl.external_links(self.PAGE, {"MzIwMTQwMzMzNQ=="})
        self.assertEqual([wl.link_key(u) for u in other], ["MzU5MDUzMTk5Nw==_1_1"])
        self.assertEqual(web, ["https://www.chinatax.gov.cn/chinatax/n363/c5211371/content.html",
                               "http://szs.mof.gov.cn/zhengcefabu/202308/t20230802_3899800.htm"])

    def test_external_file_name(self):
        self.assertEqual(wl.external_filename(3, "http://szs.mof.gov.cn/zhengcefabu/t2023.htm", "text/html"),
                         "03_szs.mof.gov.cn_zhengcefabu_t2023.htm.html")
        self.assertTrue(wl.external_filename(1, "https://x.cn/a/paper", "application/pdf").endswith(".pdf"))


class TestAlbumSingle(unittest.TestCase):
    def test_single_article_album_returns_dict(self):
        import json
        text = json.dumps({"getalbum_resp": {"article_list": {"url": "http://mp.weixin.qq.com/s?__biz=A&amp;mid=1&amp;idx=1&amp;sn=z",
                           "title": "t", "create_time": "1", "msgid": 1, "itemidx": 1},
                           "continue_flag": "0", "base_info": {"title": "x", "article_count": "1"}}})
        items, more, title, total = wl.parse_album(text)
        self.assertEqual([i["url"] for i in items], ["https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&sn=z"])
        self.assertFalse(more)


if __name__ == "__main__":
    unittest.main()
