# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

import scrapy


class VodScraperItem(scrapy.Item):
    slug = scrapy.Field()     # WordPressなどのURLスラッグ
    title = scrapy.Field()    # 作品タイトル
    url = scrapy.Field()      # 作品URL（各サービスのページ）
    service = scrapy.Field()  # available || rental || unavailable
    price = scrapy.Field()    # free || 数値 || None
    pass
