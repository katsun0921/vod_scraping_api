# 劇場情報源 候補リスト（日本限定・初期調査）

作成: 2026-07-17
対象: `docs/feature/theater-release-calendar-spec.md` レイヤー1データソース（未確定事項#1）

## 調査方法・限界

このセッションのネットワークポリシーにより外部サイトへの直接アクセスが全面ブロックされている
（`WebFetch`・`curl`ともに主要ドメイン宛の接続が403で拒否される。`example.com`宛でも同様のため
サイト固有のブロックではなく環境側の制限）。そのため以下は**Web検索のスニペットのみ**に基づく
一次調査であり、以下は未検証。

- RSSフィードが実際に稼働しているか
- フィードの中身が「公開日付き作品一覧」として構造化されているか、単なるサイト更新通知か
- 利用規約の正確な文言・スクレイピング可否

**「劇場情報源」シートに登録する前に、必ず人間がブラウザ・RSSリーダーで実際に開いて内容と
利用規約を確認すること**（仕様書7.「利用条件：利用規約を確認する」に対応）。

## A. 保留（撤回）: TMDb API

| 候補 | 概要 | 取得方式 | 備考 |
|---|---|---|---|
| **TMDb API** `/discover/movie`（`region=JP` + `with_release_type=2,3`） | 公式APIで劇場公開日（type=2先行公開/3劇場公開）を国別に取得できる | `tmdb`（`news_bot/fetch_theater.py`に実装済みだが**現状未使用**） | 実装自体は完了しているが、下記の理由で「劇場情報源」シートには登録しない方針 |

**2026-07-17時点の判断（撤回済み）**: 一時、Redditのコミュニティ実例を根拠に無償利用前提で
採用を決定し実装したが、**Katsumascoreが既にGoogle AdSenseを掲載しており現在収益を得ている
ことが判明したため撤回した**。TMDb APIの「Personal Use」申請フォームには
「Your use is non-commercial and generates no revenue」「You will not use TMDB data in
any business or commercial environment without prior approval」という明示的な誓約があり、
虚偽申告には「immediate termination」「revocation」「potential reporting to TMDB」という
明記された結果が伴う。AdSense掲載は以前の調査で確認した「広告表示によるサイトへの収益化」に
該当するため、この誓約は事実に反する。

**今後TMDbを使う場合の選択肢**（未着手）:
1. TMDB公式へ商用利用として問い合わせ、Commercial APIプラン（年商$1M未満で$149/月）を契約する
2. 上記が難しい場合は他候補（B/C/E節）を優先する

`fetch_theater.py`の`_fetch_tmdb()`実装はコードとして残しているが、商用ライセンスを取得する
までは「劇場情報源」シートに`取得方式=tmdb`の行を登録しないこと。

### TMDb APIの費用（要最終確認）

| 利用形態 | 費用 |
|---|---|
| 非商用利用 | 無料。TMDB出典表示（attribution）義務あり |
| 商用利用（広告収益等でマネタイズしている場合） | 別途ライセンス契約が必要。年間売上$1M未満は $149/月（Commercial APIプラン）、$1M超は個別見積もり |
| レート制限 | 従量課金ではなくIPベースで秒間約40リクエストの上限のみ |

**確定事項**: KatsumascoreはGoogle AdSenseを掲載しており「広告表示によるサイトへの集客・収益化」に
該当する。TMDB担当者のフォーラム回答には「広告自体は問題ない」という揺れた発言も見られるが、
公式の誓約文言（Personal Use申請フォーム）とは矛盾するため、無償利用は不可と判断する。
商用利用する場合は`api@themoviedb.org`へ問い合わせて確認すること。この判定は
`docs/feature/coming-soon-pipeline.md`（同じ`TMDB_API_KEY`を使う想定）にも影響する。

## B. RSS候補（存在は示唆されるが内容・規約が未検証）→ 再調査結果：見送り

| 候補 | URL | 備考 |
|---|---|---|
| 映画.com 新着情報RSS | `https://eiga.com/information/rss.xml` | [利用規約](https://eiga.com/info/kiyaku/)を再確認：「複製、編集、改変、掲載、転載、公衆送信、配布、販売、提供、翻訳その他あらゆる利用または使用」を明示的に禁止し、違反時は利益相当額を請求できるとまで明記。RSS配信していること自体は購読目的の許諾と解釈できても、それをシステムに取り込み自動転記する行為は上記の「複製」「利用」に該当しうる。**AdSenceで収益化しているKatsumascoreがこの規約に反するリスクを取る理由がないため見送り** |
| シネマトゥデイ index.xml | `https://www.cinematoday.jp/index.xml` | 個別の利用規約全文はWeb検索では未取得（要ブラウザでの直接確認）。非公式RSS転載ボットが稼働している実例はあるが、それが規約上許諾されているかは不明。フィードの中身も一般ニュースRSSで「公開日」の構造化データではない可能性が高い。**規約未確認のまま採用はしない** |

映画.comは規約文言が確認できた時点で「明示的に禁止」と判定できるため除外を確定。シネマトゥデイは
規約文言そのものが確認できていないため「不明」のまま。人間がブラウザで規約ページを開いて確認しない
限りレイヤー1候補には昇格させない。

## C. HTML一覧ページ（RSSなし・スクレイパー実装が必要）

| 候補 | URL | 備考 |
|---|---|---|
| MOVIE WALKER PRESS 公開予定一覧 | `https://press.moviewalker.jp/list/coming/` | 655作品規模の公開予定一覧（KADOKAWAグループ媒体）。RSS未確認。無断複製・転載禁止規定がある可能性が高く要確認 |
| ぴあ映画（旧ぴあ映画生活の後継） 近日公開の映画 | `https://lp.p.pia.jp/eiga/upcomming/` | RSSなし。旧ぴあ映画生活は2022年3月31日にサービス終了済み |
| 映画の時間（ジョルダン） 近日公開予定 | `https://movie.jorudan.co.jp/film/pre/` | [about](https://movie.jorudan.co.jp/about/)ページに無断複製・改変・転載禁止の記載あり。RSSなし |

いずれも仕様書7.の方針「HTMLスクレイピングは必要最小限にする」との整合、および利用規約上の
リスクを踏まえると、E節のPR TIMES企業別RSSより優先度は低い（RSSではなくスクレイパー実装が
必要な分、規約確認・保守コストが高い）。PR TIMESだけでは網羅性が不足すると分かった場合の
補完候補として保留する。

## E. 配給会社一次情報（レイヤー2向け・レイヤー1の主軸には不向き） / PR TIMES企業別RSS（再調査：レイヤー1候補に昇格）

東宝・東映・松竹・ワーナー ブラザース ジャパン・ディズニー・ギャガ・KADOKAWAを調査したが、
**いずれも配給会社公式サイトにRSS/構造化データは無く**、公開作品一覧はHTMLページのみ提供。
加えて利用規約が明確に確認できた2社は複製・転載を強く禁止している。

| 配給会社 | 公開作品一覧URL | 利用規約 |
|---|---|---|
| 東宝 | `https://www.toho.co.jp/movie/lineup` | [ご利用条件](https://www.toho.co.jp/company/term_of_use)で無断複製・公衆送信を法律により禁止と明記 |
| 東映 | `https://www.toei.co.jp/entertainment/movie/index.html` | [サイトポリシー](https://www.toei.co.jp/site-policy/index.html)で「私的・商用の目的に拘わらず、原則著作物の利用許諾はいたしません」と最も強い禁止文言 |
| 松竹 | `https://www.shochiku.co.jp/cinema/lineup/` | 未確認（同様の可能性が高い） |
| ワーナー ブラザース ジャパン | `https://www.warnerbros.co.jp/movie/` | 未確認 |
| ディズニー | `https://www.disney.co.jp/movie` | 未確認 |
| ギャガ | `https://gaga.ne.jp/pt/comingsoon/` | 未確認 |
| KADOKAWA | `https://www.kadokawa.co.jp/category/movie/` | 未確認 |

配給会社公式サイトを個別スクレイパーで直接取得する方式は仕様書7.の「HTMLスクレイピングは必要
最小限にする」方針に反し、利用規約リスクも高いため**不採用**（レイヤー2の個別作品情報源としてのみ
将来使う可能性を残す）。

### PR TIMES企業別RSS（2026-07-20 再調査で company_id を主要6社追加確認）

配給会社が自ら配信するプレスリリースサービス「PR TIMES」には企業別RSSが存在し、
`https://prtimes.jp/companyrdf.php?company_id={ID}` の形式で購読できる（`feedparser`で
既存の`_fetch_rss()`実装がそのまま使える標準的なRSS/RDF形式）。

| 配給会社 | company_id | RSS URL |
|---|---|---|
| 東宝 | `27367` | `https://prtimes.jp/companyrdf.php?company_id=27367` |
| 東映 | `52513` | `https://prtimes.jp/companyrdf.php?company_id=52513` |
| 松竹 | `71115` | `https://prtimes.jp/companyrdf.php?company_id=71115` |
| ワーナー ブラザース ジャパン | `63715` | `https://prtimes.jp/companyrdf.php?company_id=63715` |
| ウォルト・ディズニー・ジャパン | `2021` | `https://prtimes.jp/companyrdf.php?company_id=2021` |
| ギャガ | `22989` | `https://prtimes.jp/companyrdf.php?company_id=22989` |
| KADOKAWA | `7006` | `https://prtimes.jp/companyrdf.php?company_id=7006` |

**規約面**: PR TIMES [利用規約](https://prtimes.jp/main/html/kiyaku)本文の逐条確認はできていないが、
Web検索で確認できた範囲では以下の点でA節（映画.com）・E節上部（配給会社公式サイト）より
リスクが低いと判断できる。

- PR TIMES自身が「企業別RSSをFeedlyなどのRSSリーダーに登録する」利用法を自社メディア記事で
  案内しており、n8n連携等の自動収集事例も一般に流通している（＝RSS購読・自動取得はサービスの
  想定利用形態に含まれる）
- 映画.com・配給会社公式サイトのような「複製・転載を明示的に禁止する」条項は検索範囲内では
  見つからなかった（ただし規約全文の逐条確認はできていないため断定はできない）

**内容面の制約**（引き続き有効）:

- 中身は「公開作品一覧」ではなく個別プレスリリース（新作発表・公開延期・興行成績・タイアップ
  告知・グッズ販売等が混在）で、`fetch_theater.extract_release_date()`と同様のテキスト解析
  （タイトル抽出・公開日抽出・「劇場公開」関連リリースかどうかの判定）が必要
- 週次で必ず新作が流れるとは限らないため、単独では網羅性に欠ける可能性がある（他ソースとの併用
  が前提）

**結論**: レイヤー1の単独候補としては網羅性に懸念があるが、規約リスクが相対的に低く
`_fetch_rss()`をそのまま流用できるため、**「劇場情報源」シートへの登録候補として最有力**。
ただし正式採用前に人間がPR TIMES利用規約全文をブラウザで確認すること（仕様書7.の運用ルール）。

## D. 使用不可・調査対象から除外

| 候補 | 除外理由 |
|---|---|
| Yahoo!映画 | 2023年7月31日にサービス終了。現在はYahoo!検索へ機能統合されており、独立した公開予定一覧・RSSは存在しない |
| ぴあ映画生活 RSS（`http://cinema.pia.co.jp/rdf/news.rdf`） | 母体サービスが2022年3月31日に終了済みのため、フィードも停止していると推測される |

## 次のアクション（人間側での確認事項）

1. TMDb API は AdSense掲載により無償利用不可と判断（A.節）。使う場合はCommercial APIプラン
   （$149/月〜）の契約が必要。**現時点では見送り**
2. 映画.com（B.節）は利用規約の明示的な複製・転載禁止条項により**見送り確定**。シネマトゥデイ
   （B.節）・C.節（HTML一覧）は規約未確認または実装コストが高く、**保留**（PR TIMESで網羅性が
   不足すると分かった場合の補完候補）
3. **[このセッションで完了]** PR TIMES企業別RSS（E.節）のcompany_idを東宝・東映・松竹・
   ワーナー ブラザース ジャパン・ディズニー・ギャガ・KADOKAWAの7社分確認済み
4. **次にやること**: 人間がPR TIMES利用規約全文（`https://prtimes.jp/main/html/kiyaku`）を
   ブラウザで確認し、企業別RSSの自動取得・保存が規約上問題ないか判断する。問題なければ
   「劇場情報源」シートに`取得方式=rss`で7社分を登録する（`規約確認済み`="済"にした行のみ
   `fetch_theater.py`の取得対象になる）
5. PR TIMES採用後、`fetch_theater.extract_release_date()`が個別プレスリリースの文面から
   「劇場公開」関連の告知かどうか・公開日を正しく拾えるか実データで検証する（新作発表と
   興行成績報告など無関係なリリースをどう除外するかの精度確認が必要）
