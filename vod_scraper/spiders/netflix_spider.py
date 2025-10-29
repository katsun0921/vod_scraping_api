import scrapy
from vod_scraper.items import VodScraperItem

class NetflixSpider(scrapy.Spider):
    name = "netflix_spider"
    allowed_domains = ["netflix.com"]
    start_urls = ["https://www.netflix.com/jp/title/81092221"]

    def start_requests(self):
        """URLに直接アクセスし、HTTPステータスで配信可否を判断"""
        for url in self.start_urls:
            yield scrapy.Request(
                url,
                callback=self.parse,
                errback=self.handle_error,
                dont_filter=True,
            )

    def parse(self, response):
        """200番台なら配信中と判定"""
        yield VodScraperItem(
            slug="joker-2019",
            title="ジョーカー",
            url=response.url,
            service="available",
            price="free",
        )

    def handle_error(self, failure):
        """404/403などのエラー時は配信停止とする"""
        request = failure.request
        status = getattr(failure.value.response, "status", None)

        self.logger.warning(f"Netflix access failed: {request.url} (status: {status})")

        yield VodScraperItem(
            slug="joker-2019",
            title="ジョーカー",
            url=request.url,
            service="disable",
            price=None,
        )
