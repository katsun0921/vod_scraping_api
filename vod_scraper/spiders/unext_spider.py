import scrapy
from vod_scraper.items import VodScraperItem

class UnextSpider(scrapy.Spider):
    name = "unext_spider"
    allowed_domains = ["video.unext.jp"]
    start_urls = ["https://video.unext.jp/title/SID0044927"]

    def parse(self, response):
        yield VodScraperItem(
            slug="joker-2019",
            title="ジョーカー",
            url="https://video.unext.jp/title/SID0044927",
            service="available",  # 定額視聴可能
            price="free",
        )
