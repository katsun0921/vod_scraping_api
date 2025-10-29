import scrapy
from vod_scraper.items import VodScraperItem

class PrimevideoSpider(scrapy.Spider):
    name = "primevideo_spider"  # ← 修正！
    allowed_domains = ["amazon.co.jp"]
    start_urls = ["https://www.amazon.co.jp/dp/B08271NYW7"]

    def parse(self, response):
        yield VodScraperItem(
            slug="joker-2019",
            title="ジョーカー",
            url="https://www.amazon.co.jp/dp/B08271NYW7",
            service="rental",
            price=400,
        )
