import scrapy
import json
from pathlib import Path
from vod_scraper.items import VodScraperItem

class PrimevideoSpider(scrapy.Spider):
    name = "primevideo_spider"
    allowed_domains = ["amazon.co.jp"]
    start_urls = ["https://www.amazon.co.jp/dp/B08271NYW7"]

    def parse(self, response, **kwargs):
        slug = "joker-2019"
        title = "ジョーカー"
        output_path = Path("outputs/vod_summary.json")

        # すでにJSONにデータが存在する場合、スキップ
        if output_path.exists():
            try:
                with output_path.open(encoding="utf-8") as f:
                    existing_data = json.load(f)
                    if slug in existing_data and existing_data[slug].get("primevideo", {}).get("url"):
                        self.logger.info(f"🟢 Skipping Prime Video crawl for {title} (already in JSON)")
                        return
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to read existing JSON: {e}")

        # ステータスコードで判定
        if 200 <= response.status < 400:
            service = "rental"
            price = 400
        else:
            service = "disable"
            price = None

        yield VodScraperItem(
            slug=slug,
            title=title,
            url=response.url,
            service=service,
            price=price,
        )
