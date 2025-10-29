# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html

import json
from pathlib import Path
from itemadapter import ItemAdapter

class MergeVodPipeline:
    """
    各スパイダー（Netflix / Amazon Prime Video / U-NEXT）から送られる item を統合し、
    slug（WordPress記事などのURL識別子）ごとにまとめてJSON出力します。
    """

    SERVICE_MAP = {
        "netflix": "netflix",
        "primevideo": "primevideo",
        "unext": "unext",
    }

    def __init__(self):
        self.data = {}

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        slug = adapter.get("slug")
        title = adapter.get("title")
        raw_name = spider.name.replace("_spider", "")
        service_name = self.SERVICE_MAP.get(raw_name, raw_name)

        # まだタイトルが登録されていない場合、新規エントリ作成
        if slug not in self.data:
            self.data[slug] = {
                "title": title,
                "netflix": {"url": None, "service": "unavailable", "price": None},
                "primevideo": {"url": None, "service": "unavailable", "price": None},
                "unext": {"url": None, "service": "unavailable", "price": None},
            }

        # 現在のスパイダーから受け取った情報を統合
        self.data[slug][service_name] = {
            "url": adapter.get("url"),
            "service": adapter.get("service"),
            "price": adapter.get("price"),
        }

        return item

    def close_spider(self, spider):
        """
        全てのスパイダーの処理終了後、1つのJSONに出力。
        """
        output_path = Path("outputs/vod_summary.json")
        output_path.parent.mkdir(exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
