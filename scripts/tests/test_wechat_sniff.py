import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import json
import os
import tempfile
import unittest

import wechat_sniff as sn

ART = "http://mp.weixin.qq.com/s?__biz=MzIwMTQwMzMzNQ==&amp;mid=%d&amp;idx=%d&amp;sn=s%d&amp;chksm=c#rd"


def _item(mid, ts, title, subs=()):
    ext = {"title": title, "content_url": ART % (mid, 1, mid), "author": "菜花"}
    if subs:
        ext["is_multi"] = 1
        ext["multi_app_msg_item_list"] = [{"title": t, "content_url": ART % (mid, i + 2, mid * 10 + i)}
                                          for i, t in enumerate(subs)]
    return {"comm_msg_info": {"id": mid, "type": 49, "datetime": ts}, "app_msg_ext_info": ext}


class TestGetmsgJson(unittest.TestCase):
    def test_single_and_multi_items(self):
        lst = {"list": [_item(100, 1700000000, "头条A"), _item(101, 1700086400, "头条B", ["次条B1", "次条B2"])]}
        body = json.dumps({"ret": 0, "errmsg": "ok", "general_msg_list": json.dumps(lst, ensure_ascii=False),
                           "can_msg_continue": 1, "next_offset": 20})
        recs = sn.extract("/mp/profile_ext?action=getmsg&__biz=x&offset=10", body)
        self.assertEqual([r["title"] for r in recs], ["头条A", "头条B", "次条B1", "次条B2"])
        self.assertEqual(recs[0]["url"], "https://mp.weixin.qq.com/s?__biz=MzIwMTQwMzMzNQ==&mid=100&idx=1&sn=s100&chksm=c")
        self.assertEqual(recs[3]["url"], "https://mp.weixin.qq.com/s?__biz=MzIwMTQwMzMzNQ==&mid=101&idx=3&sn=s1011&chksm=c")
        self.assertEqual([r["ts"] for r in recs], [1700000000, 1700086400, 1700086400, 1700086400])

    def test_non_article_items_skipped(self):
        lst = {"list": [{"comm_msg_info": {"id": 1, "type": 1, "datetime": 1}},  # 纯文字消息，没有 app_msg_ext_info
                        _item(7, 2, "只有这篇")]}
        body = json.dumps({"ret": 0, "general_msg_list": json.dumps(lst, ensure_ascii=False)})
        self.assertEqual([r["title"] for r in sn.extract("/mp/profile_ext?action=getmsg", body)], ["只有这篇"])


class TestHomeHtml(unittest.TestCase):
    def test_msglist_var(self):
        lst = {"list": [_item(55, 1600000000, "首页第一篇")]}
        escaped = json.dumps(lst, ensure_ascii=False).replace("&", "&amp;").replace('"', "&quot;")
        body = "<html><script>var msgList = '%s';\nvar nickname = \"菜花来了\";</script></html>" % escaped
        recs = sn.extract("/mp/profile_ext?action=home&__biz=x", body)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["title"], "首页第一篇")
        self.assertEqual(recs[0]["url"], "https://mp.weixin.qq.com/s?__biz=MzIwMTQwMzMzNQ==&mid=55&idx=1&sn=s55&chksm=c")


class TestFallback(unittest.TestCase):
    def test_any_full_links_in_unknown_body(self):
        body = ('{"x":"https:\\/\\/mp.weixin.qq.com\\/s?__biz=A\\u0026mid=1\\u0026idx=1\\u0026sn=z",'
                '"y":"https://mp.weixin.qq.com/s?__biz=A&mid=2&idx=1",'  # 缺 sn，打不开，忽略
                '"z":"https://mp.weixin.qq.com/s/short"}')  # 短链没有去重键，忽略
        recs = sn.extract("/mp/whatever", body)
        self.assertEqual([r["url"] for r in recs], ["https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&sn=z"])
        self.assertIsNone(recs[0]["title"])

    def test_template_placeholders_ignored(self):
        body = "https://mp.weixin.qq.com/s?__biz=${window.biz}&mid=${window.mid}&idx=${window.idx}&sn=${window.sn}"
        self.assertEqual(sn.extract("/s", body), [])

    def test_dedupe_within_response(self):
        body = "a https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&sn=z b https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&sn=z#rd"
        self.assertEqual(len(sn.extract("/mp/x", body)), 1)


class TestQueue(unittest.TestCase):
    def test_append_and_read_incrementally(self):
        with tempfile.TemporaryDirectory() as d:
            q = os.path.join(d, "q.txt")
            n = sn.append_queue(q, [{"url": "https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&sn=z", "title": "t", "ts": 1},
                                    {"url": "https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&sn=z", "title": "t", "ts": 1}])
            self.assertEqual(n, 1)  # 同一链接只入队一次
            urls, pos = sn.read_queue(q, 0)
            self.assertEqual(urls, ["https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&sn=z"])
            sn.append_queue(q, [{"url": "https://mp.weixin.qq.com/s?__biz=A&mid=2&idx=1&sn=y", "title": None, "ts": None}])
            urls2, pos2 = sn.read_queue(q, pos)
            self.assertEqual(urls2, ["https://mp.weixin.qq.com/s?__biz=A&mid=2&idx=1&sn=y"])
            self.assertGreater(pos2, pos)

    def test_redact_query(self):
        self.assertEqual(sn.redact("/mp/profile_ext?action=getmsg&__biz=MzI=&uin=777&key=secret&pass_ticket=p&offset=10"),
                         "/mp/profile_ext?action=getmsg&__biz=MzI=&uin=*&key=*&pass_ticket=*&offset=10")


class TestWhitelist(unittest.TestCase):
    def test_filter_by_biz(self):
        recs = [{"url": "https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&sn=z", "title": None, "ts": None},
                {"url": "https://mp.weixin.qq.com/s?__biz=B&mid=1&idx=1&sn=z", "title": None, "ts": None}]
        self.assertEqual([r["url"] for r in sn.filter_biz(recs, {"A"})], ["https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&sn=z"])
        self.assertEqual(len(sn.filter_biz(recs, set())), 2)  # 空白名单不过滤

    def test_load_bizset_from_example_config(self):
        example = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "accounts.example.json")
        self.assertEqual(sn.load_bizset(example), {"<__biz>"})
        self.assertEqual(sn.load_bizset("/nonexistent.json"), set())


class TestSpool(unittest.TestCase):
    PAGE = ('<html><head><meta property="og:title" content="T"></head><body><div id="js_content">x</div>'
            '<script>var biz = "A"; var mid = "1"; var idx = "1"; var sn = "z";</script></body></html>')

    def test_is_article_page(self):
        self.assertTrue(sn.is_article_page("/s?__biz=A&mid=1&idx=1&sn=z", self.PAGE))
        self.assertTrue(sn.is_article_page("/s/short", self.PAGE))
        self.assertFalse(sn.is_article_page("/mp/jsmonitor?x=1", self.PAGE))
        self.assertFalse(sn.is_article_page("/s?__biz=A", "<html>验证页，没有正文</html>"))

    def test_spool_writes_file_once(self):
        with tempfile.TemporaryDirectory() as d:
            f1 = sn.spool_page(d, "/s?__biz=A&mid=1&idx=1&sn=z", self.PAGE)
            self.assertTrue(f1 and os.path.exists(f1))
            self.assertEqual(os.path.basename(f1), "A_1_1.html")  # 按 URL 里的 biz_mid_idx 命名
            self.assertTrue(os.path.basename(sn.spool_page(d, "/s/short", self.PAGE)).startswith("page_"))
            self.assertEqual(open(f1, encoding="utf-8").read(), self.PAGE)
            self.assertIsNone(sn.spool_page(d, "/s?__biz=A&mid=1&idx=1&sn=z", self.PAGE))  # 同一篇不重复落盘
            self.assertEqual(len(os.listdir(d)), 2)


if __name__ == "__main__":
    unittest.main()
