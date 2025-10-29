import asyncio
import json
from pathlib import Path
import sys

SPIDERS = {
    "netflix_spider": "netflix.json",
    "primevideo_spider": "primevideo.json",
    "unext_spider": "unext.json",
}

OUTPUT_DIR = Path("outputs")
SUMMARY_FILE = OUTPUT_DIR / "vod_summary.json"

async def run_spider(spider_name: str, output_file: str):
    """Scrapyスパイダーを非同期で実行し、個別JSONに出力"""
    print(f"\n🚀 Running spider: {spider_name}")

    process = await asyncio.create_subprocess_exec(
        "scrapy", "crawl", spider_name,
        f"-o={OUTPUT_DIR / (output_file + ':jsonlines')}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    stdout = stdout.decode("utf-8", errors="ignore")
    stderr = stderr.decode("utf-8", errors="ignore")

    print(f"--- {spider_name} output ---")
    print(stdout)
    if stderr:
        print(f"⚠️ {spider_name} error:\n{stderr}", file=sys.stderr)

    if process.returncode == 0:
        print(f"✅ {spider_name} completed successfully.")
    else:
        print(f"❌ {spider_name} failed with code {process.returncode}")

    return process.returncode


def read_json_lines(file_path: Path):
    """ScrapyのJSON Lines出力を安全に読み込む"""
    items = []
    try:
        with file_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"⚠️ Skipping invalid JSON line in {file_path.name}: {e}")
    except FileNotFoundError:
        print(f"⚠️ {file_path.name} not found.")
    return items

def load_existing_summary():
    """既存のvod_summary.jsonを読み込み"""
    if SUMMARY_FILE.exists():
        try:
            with SUMMARY_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("⚠️ vod_summary.json の読み込みに失敗しました。スキップ判定を無効化します。")
    return {}


def should_skip_spider(spider_name: str, summary_data: dict) -> bool:
    """既存データに有効なVOD情報があればスクレイピングをスキップ"""
    service = spider_name.replace("_spider", "")
    for slug, info in summary_data.items():
        vod_info = info.get(service, {})
        if vod_info.get("service") and vod_info["service"] != "unavailable":
            print(f"⏩ {spider_name} skipped: '{slug}' already has {service} data.")
            return True
    return False

def merge_json_files():
    """個別スパイダーの出力を統合してvod_summary.jsonを生成"""
    print("\n🔄 Merging JSON results...")
    merged = {}
    OUTPUT_DIR.mkdir(exist_ok=True)

    for spider_name, filename in SPIDERS.items():
        file_path = OUTPUT_DIR / filename
        data = read_json_lines(file_path)
        if not data:
            print(f"⚠️ No valid data found in {filename}.")
            continue

        for item in data:
            slug = item["slug"]
            title = item["title"]
            service = filename.replace(".json", "")
            if slug not in merged:
                merged[slug] = {
                    "title": title,
                    "netflix": {"url": None, "service": "unavailable", "price": None},
                    "primevideo": {"url": None, "service": "unavailable", "price": None},
                    "unext": {"url": None, "service": "unavailable", "price": None},
                }
            merged[slug][service] = {
                "url": item.get("url"),
                "service": item.get("service"),
                "price": item.get("price"),
            }

    with SUMMARY_FILE.open("w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"✅ 統合完了: {SUMMARY_FILE.resolve()}\n")


async def main():
    print("=== Scrapy Multi-Spider Runner (async + skip + merge) ===\n")

    summary_data = load_existing_summary()
    if SUMMARY_FILE.exists():
        backup = SUMMARY_FILE.with_name("vod_summary_backup.json")
        SUMMARY_FILE.rename(backup)
        print(f"📦 Backup created: {backup}")

    # スキップ条件をチェックして実行対象のみ選択
    spiders_to_run = [
        (name, file)
        for name, file in SPIDERS.items()
        if not should_skip_spider(name, summary_data)
    ]

    if not spiders_to_run:
        print("⏹ すべてのスパイダーがスキップされました。")
    else:
        results = await asyncio.gather(*(run_spider(name, file) for name, file in spiders_to_run))
        if all(r == 0 for r in results):
            print("\n🎉 All executed spiders finished successfully.")
        else:
            print("\n⚠️ Some spiders failed. Check logs above.")

    merge_json_files()


if __name__ == "__main__":
    asyncio.run(main())
