import scrapy
import json
from pathlib import Path
from vod_scraper.items import VodScraperItem

class UnextSpider(scrapy.Spider):
    name = "unext_spider"
    allowed_domains = ["video.unext.jp"]
    start_urls = ["https://video.unext.jp/title/SID0044927"]

    def start_requests(self):
        summary_path = Path("outputs/vod_summary.json")
        if summary_path.exists():
            try:
                with summary_path.open(encoding="utf-8") as f:
                    summary = json.load(f)
                    if summary.get("joker-2019", {}).get("unext", {}).get("url"):
                        self.logger.info("🔁 Skipping U-NEXT (already exists in vod_summary.json)")
                        return
            except json.JSONDecodeError:
                self.logger.warning("⚠️ Failed to parse vod_summary.json, continuing crawl...")

        for url in self.start_urls:
            yield scrapy.Request(url, callback=self.parse)

    def parse(self, response, **kwargs):
        service_status = "available" if 200 <= response.status < 400 else "disable"

        yield VodScraperItem(
            slug="joker-2019",
            title="ジョーカー",
            url=response.url,
            service=service_status,
            price="free" if service_status == "available" else None,
        )
