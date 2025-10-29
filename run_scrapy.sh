#!/bin/bash
# Scrapy 並列実行スクリプト

echo "=============================="
echo "🚀 Start running all spiders..."
echo "=============================="

# Conda環境を有効化（必要なら環境名を変更）
source /opt/anaconda3/bin/activate scrapy-env

# プロジェクトルートへ移動
cd "$(dirname "$0")"

# Pythonスクリプトを実行
python run_all_spiders.py

# 終了メッセージ
echo "=============================="
echo "✅ Finished all scrapy spiders"
echo "=============================="
