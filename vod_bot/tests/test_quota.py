"""weekly_patch.py の優先順位ソート・クォータ選択ロジックのユニットテスト。

外部APIへのアクセスは一切行わない。
"""

from datetime import date

from weekly_patch import (
    BATCH_COUNT,
    _DATE_FAR_FUTURE,
    _all_updated_at_empty,
    _has_any_streaming,
    _min_updated_at,
    _select_targets,
    _sort_key_phase1,
    _sort_key_phase2,
    get_batch_for_date,
    get_post_badge,
)


def _post(post_id: int, **services_or_meta) -> dict:
    """テスト用 post を生成する。

    例: _post(1, release_year=2020, netflix={"status": "streaming", "updated_at": "2025-01-01"})
    """
    acf: dict = {}
    for key, val in services_or_meta.items():
        acf[key] = val
    return {"id": post_id, "acf": acf}


# ---------------------------------------------------------------------------
# _all_updated_at_empty
# ---------------------------------------------------------------------------

class TestAllUpdatedAtEmpty:
    def test_全サービス空欄_true(self):
        post = _post(1, netflix={}, amazon_prime_video={"updated_at": ""})
        assert _all_updated_at_empty(post) is True

    def test_一部サービスに値あり_false(self):
        post = _post(2, netflix={"updated_at": "2025-01-01"}, amazon_prime_video={})
        assert _all_updated_at_empty(post) is False

    def test_全サービスに値あり_false(self):
        post = _post(
            3,
            netflix={"updated_at": "2025-01-01"},
            amazon_prime_video={"updated_at": "2025-02-01"},
        )
        assert _all_updated_at_empty(post) is False

    def test_acf自体が空_true(self):
        assert _all_updated_at_empty({"id": 4, "acf": {}}) is True

    def test_acfがnone_true(self):
        assert _all_updated_at_empty({"id": 5}) is True


# ---------------------------------------------------------------------------
# _has_any_streaming
# ---------------------------------------------------------------------------

class TestHasAnyStreaming:
    def test_配信中あり_true(self):
        post = _post(1, netflix={"status": "streaming"})
        assert _has_any_streaming(post) is True

    def test_配信中なし_false(self):
        post = _post(2, netflix={"status": "unavailable"}, hulu={"status": "ended"})
        assert _has_any_streaming(post) is False

    def test_空ステータス_false(self):
        post = _post(3, netflix={"status": ""})
        assert _has_any_streaming(post) is False

    def test_rental_purchaseは配信中ではない(self):
        post = _post(4, netflix={"status": "rental"}, hulu={"status": "purchase"})
        assert _has_any_streaming(post) is False


# ---------------------------------------------------------------------------
# _min_updated_at
# ---------------------------------------------------------------------------

class TestMinUpdatedAt:
    def test_複数サービスの最古日付を返す(self):
        post = _post(
            1,
            netflix={"updated_at": "2025-03-01"},
            amazon_prime_video={"updated_at": "2024-06-15"},
            hulu={"updated_at": "2025-12-31"},
        )
        assert _min_updated_at(post) == date(2024, 6, 15)

    def test_日時形式の文字列も先頭10文字で解釈(self):
        post = _post(1, netflix={"updated_at": "2025-03-01 12:34:56"})
        assert _min_updated_at(post) == date(2025, 3, 1)

    def test_全サービス空欄なら遠未来(self):
        post = _post(1, netflix={"updated_at": ""})
        assert _min_updated_at(post) == _DATE_FAR_FUTURE

    def test_不正形式はスキップ(self):
        post = _post(
            1,
            netflix={"updated_at": "invalid"},
            hulu={"updated_at": "2025-01-01"},
        )
        assert _min_updated_at(post) == date(2025, 1, 1)


# ---------------------------------------------------------------------------
# _sort_key_phase1: release_year ASC（空欄=9999最後尾）, min_updated_at ASC
# ---------------------------------------------------------------------------

class TestSortKeyPhase1:
    def test_古い作品が先頭(self):
        posts = [
            _post(10, release_year=2020),
            _post(11, release_year=2010),
            _post(12, release_year=2000),
        ]
        ids = [p["id"] for p in sorted(posts, key=_sort_key_phase1)]
        assert ids == [12, 11, 10]

    def test_空欄は最後尾(self):
        posts = [
            _post(20, release_year=2020),
            _post(21),  # release_year なし
            _post(22, release_year=2010),
        ]
        ids = [p["id"] for p in sorted(posts, key=_sort_key_phase1)]
        assert ids == [22, 20, 21]

    def test_release_year_0も空欄扱いで最後尾(self):
        posts = [
            _post(30, release_year=2020),
            _post(31, release_year=0),
            _post(32, release_year=2010),
        ]
        ids = [p["id"] for p in sorted(posts, key=_sort_key_phase1)]
        assert ids == [32, 30, 31]

    def test_release_year不正値は最後尾(self):
        posts = [
            _post(40, release_year=2020),
            _post(41, release_year="invalid"),
            _post(42, release_year=2010),
        ]
        ids = [p["id"] for p in sorted(posts, key=_sort_key_phase1)]
        assert ids == [42, 40, 41]

    def test_同年は古いupdated_atが先頭(self):
        posts = [
            _post(50, release_year=2020, netflix={"updated_at": "2025-06-01"}),
            _post(51, release_year=2020, netflix={"updated_at": "2025-01-01"}),
        ]
        ids = [p["id"] for p in sorted(posts, key=_sort_key_phase1)]
        assert ids == [51, 50]


# ---------------------------------------------------------------------------
# _sort_key_phase2: 配信中先頭, release_year DESC（空欄=最後尾）, min_updated_at ASC
# ---------------------------------------------------------------------------

class TestSortKeyPhase2:
    def test_配信中が最優先(self):
        posts = [
            _post(10, release_year=2025, netflix={"status": "unavailable"}),
            _post(11, release_year=2010, netflix={"status": "streaming"}),
        ]
        ids = [p["id"] for p in sorted(posts, key=_sort_key_phase2)]
        assert ids == [11, 10], "古くても配信中が先頭"

    def test_同じ配信状態なら新作優先(self):
        posts = [
            _post(20, release_year=2010, netflix={"status": "unavailable"}),
            _post(21, release_year=2025, netflix={"status": "unavailable"}),
            _post(22, release_year=2020, netflix={"status": "unavailable"}),
        ]
        ids = [p["id"] for p in sorted(posts, key=_sort_key_phase2)]
        assert ids == [21, 22, 20]

    def test_release_year空欄は最後尾(self):
        posts = [
            _post(30, release_year=2020, netflix={"status": "unavailable"}),
            _post(31, netflix={"status": "unavailable"}),  # year なし
            _post(32, release_year=2010, netflix={"status": "unavailable"}),
        ]
        ids = [p["id"] for p in sorted(posts, key=_sort_key_phase2)]
        assert ids == [30, 32, 31]

    def test_配信中優先_新作優先_updated_at古い順の総合(self):
        posts = [
            _post(40, release_year=2023, netflix={"status": "unavailable", "updated_at": "2025-01-01"}),
            _post(41, release_year=2010, netflix={"status": "streaming", "updated_at": "2025-03-01"}),
            _post(42, release_year=2025, netflix={"status": "unavailable", "updated_at": "2025-02-01"}),
            _post(43, netflix={"status": "unavailable"}),  # year なし
        ]
        ids = [p["id"] for p in sorted(posts, key=_sort_key_phase2)]
        assert ids[0] == 41, f"配信中が先頭: {ids}"
        assert ids[1] == 42, f"次に新作2025: {ids}"
        assert ids[2] == 40, f"次に新作2023: {ids}"
        assert ids[3] == 43, f"年不明が最後尾: {ids}"

    def test_同年同状態ならupdated_at古い順(self):
        posts = [
            _post(50, release_year=2020, netflix={"status": "unavailable", "updated_at": "2025-06-01"}),
            _post(51, release_year=2020, netflix={"status": "unavailable", "updated_at": "2025-01-01"}),
        ]
        ids = [p["id"] for p in sorted(posts, key=_sort_key_phase2)]
        assert ids == [51, 50]


# ---------------------------------------------------------------------------
# _select_targets: フェーズ1消化 → 残り枠をフェーズ2で補完
# ---------------------------------------------------------------------------

class TestSelectTargets:
    def test_フェーズ1のみで枠が埋まる(self):
        candidates = [
            _post(1, release_year=2010),
            _post(2, release_year=2020),
            _post(3, release_year=2000),
        ]
        targets, p1, p2 = _select_targets(candidates, quota=2)
        assert len(targets) == 2
        assert p1 == 2
        assert p2 == 0
        assert [t["id"] for t in targets] == [3, 1], "古い順"

    def test_フェーズ1で枠が余ればフェーズ2で補完(self):
        candidates = [
            _post(1, release_year=2020),  # phase1（updated_at 空）
            _post(2, release_year=2010),  # phase1
            _post(3, release_year=2025, netflix={"updated_at": "2025-01-01", "status": "streaming"}),  # phase2
            _post(4, release_year=2024, netflix={"updated_at": "2025-02-01", "status": "unavailable"}),  # phase2
        ]
        targets, p1, p2 = _select_targets(candidates, quota=3)
        assert len(targets) == 3
        assert p1 == 2, "phase1全件取得"
        assert p2 == 1, "残り1枠をphase2で補完"
        ids = [t["id"] for t in targets]
        assert ids[:2] == [2, 1], "phase1: 古い順"
        assert ids[2] == 3, "phase2: 配信中優先"

    def test_フェーズ1対象0件なら全てフェーズ2(self):
        candidates = [
            _post(1, release_year=2020, netflix={"updated_at": "2025-01-01", "status": "unavailable"}),
            _post(2, release_year=2025, netflix={"updated_at": "2025-02-01", "status": "unavailable"}),
            _post(3, release_year=2010, netflix={"updated_at": "2025-03-01", "status": "streaming"}),
        ]
        targets, p1, p2 = _select_targets(candidates, quota=10)
        assert p1 == 0
        assert p2 == 3
        ids = [t["id"] for t in targets]
        assert ids[0] == 3, "配信中が先頭"
        assert ids[1] == 2, "新作優先"
        assert ids[2] == 1

    def test_quota未満なら全件返す(self):
        candidates = [_post(1, release_year=2020), _post(2, release_year=2010)]
        targets, p1, p2 = _select_targets(candidates, quota=10)
        assert len(targets) == 2
        assert p1 == 2
        assert p2 == 0

    def test_quota_0で空リスト(self):
        candidates = [_post(1, release_year=2020), _post(2, release_year=2010)]
        targets, p1, p2 = _select_targets(candidates, quota=0)
        assert targets == []
        assert p1 == 0
        assert p2 == 0

    def test_空候補で空リスト(self):
        targets, p1, p2 = _select_targets([], quota=30)
        assert targets == []
        assert p1 == 0
        assert p2 == 0

    def test_フェーズ1とフェーズ2が同じpostを選ばない(self):
        candidates = [
            _post(1),  # phase1
            _post(2, release_year=2025, netflix={"updated_at": "2025-01-01", "status": "streaming"}),  # phase2
        ]
        targets, p1, p2 = _select_targets(candidates, quota=10)
        ids = [t["id"] for t in targets]
        assert len(ids) == len(set(ids)), "重複なし"
        assert sorted(ids) == [1, 2]


# ---------------------------------------------------------------------------
# _build_front_url
# ---------------------------------------------------------------------------

class TestBuildFrontUrl:
    """フロントエンド URL 組み立てのテスト。"""

    _CAT_MAP = {3: "anime", 5: "movie"}

    def test_日本語作品のフロントURL(self):
        from weekly_patch import _build_front_url
        post = {"slug": "john-wick", "categories": [5], "link": "https://wp.example.com/?p=1"}
        assert _build_front_url(post, "ja", self._CAT_MAP) == "https://katsumascore.blog/ja/movie/john-wick"

    def test_英語作品のフロントURL(self):
        from weekly_patch import _build_front_url
        post = {"slug": "frieren", "categories": [3], "link": "https://wp.example.com/?p=2"}
        assert _build_front_url(post, "en", self._CAT_MAP) == "https://katsumascore.blog/en/anime/frieren"

    def test_複数カテゴリは最初に解決できたslugを使う(self):
        from weekly_patch import _build_front_url
        post = {"slug": "dual", "categories": [99, 3], "link": ""}
        assert _build_front_url(post, "ja", self._CAT_MAP) == "https://katsumascore.blog/ja/anime/dual"

    def test_カテゴリ未解決時はWPリンクにフォールバック(self):
        from weekly_patch import _build_front_url
        post = {"slug": "no-cat", "categories": [99], "link": "https://wp.example.com/?p=3"}
        assert _build_front_url(post, "ja", self._CAT_MAP) == "https://wp.example.com/?p=3"

    def test_slug欠落時はWPリンクにフォールバック(self):
        from weekly_patch import _build_front_url
        post = {"categories": [3], "link": "https://wp.example.com/?p=4"}
        assert _build_front_url(post, "ja", self._CAT_MAP) == "https://wp.example.com/?p=4"


# ---------------------------------------------------------------------------
# get_batch_for_date / get_post_badge
# ---------------------------------------------------------------------------

class TestGetBatchForDate:
    """バッチ番号算出（基準日からの経過週数 % BATCH_COUNT）のテスト。

    月の日数差（28〜31日）に依存せず、週次実行を続ける限り
    BATCH_COUNT 週（=2ヶ月）で全バッチを必ず一巡することを検証する。
    """

    def test_基準日はbatch0(self):
        assert get_batch_for_date(date(2024, 1, 1)) == 0

    def test_1週間後はbatch1(self):
        assert get_batch_for_date(date(2024, 1, 8)) == 1

    def test_BATCH_COUNT週間後はbatch0に戻る(self):
        d = date(2024, 1, 1)
        for _ in range(BATCH_COUNT):
            d = date.fromordinal(d.toordinal() + 7)
        assert get_batch_for_date(d) == 0

    def test_月境界をまたいでも週次実行なら全バッチを一巡する(self):
        """毎週月曜に実行し続けた場合、BATCH_COUNT 週で 0..BATCH_COUNT-1 が
        重複・欠落なくちょうど1回ずつ出現することを確認する（月の日数差の影響を受けない）。"""
        start = date(2024, 1, 1)
        batches = [
            get_batch_for_date(date.fromordinal(start.toordinal() + 7 * i))
            for i in range(BATCH_COUNT * 3)  # 3周分（半年相当）検証
        ]
        for cycle in range(3):
            cycle_batches = batches[cycle * BATCH_COUNT:(cycle + 1) * BATCH_COUNT]
            assert sorted(cycle_batches) == list(range(BATCH_COUNT))

    def test_非月曜日でも同じ7日ウィンドウ内なら同じバッチ(self):
        # 基準日(月曜)から1〜6日後は依然として batch0 のウィンドウ内
        for offset in range(1, 7):
            d = date.fromordinal(date(2024, 1, 1).toordinal() + offset)
            assert get_batch_for_date(d) == 0


class TestGetPostBadge:
    """投稿のバッジ番号（post_id % BATCH_COUNT）のテスト。"""

    def test_post_idのモジュロで割り当てられる(self):
        for post_id in range(BATCH_COUNT * 2):
            assert get_post_badge({"id": post_id}) == post_id % BATCH_COUNT

    def test_id未設定なら0扱い(self):
        assert get_post_badge({}) == 0
