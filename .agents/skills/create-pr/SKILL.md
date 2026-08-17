---
name: create-pr
description: Validate, commit, push, and open a GitHub pull request for the current repository branch. Use when the user says create-pr, asks to create or open a PR, or wants the current local changes published for review. Do not use for PR review or for merging an existing PR.
---

# Create PR

現在のブランチの意図した変更だけを検証、コミット、pushし、`main` 向けDraft PRを作成する。

## 手順

1. `CLAUDE.md` を最後まで読み、リポジトリ固有の規約を確認する。
2. `git status --short --branch`、`git diff`、`git diff --cached`、`git log origin/main..HEAD` を確認する。
3. 次の条件を満たさない場合は停止して理由を報告する。
   - 現在のブランチが `main` ではない。
   - PRに含める変更または `origin/main` より先行したコミットが存在する。
   - `.env`、認証情報、APIキー、パスワードなどの秘密情報が含まれていない。
   - 意図不明な既存変更が混在していない。混在している場合はユーザーに対象を確認する。
4. `gh auth status` と `git remote -v` でGitHub認証とpush先を確認する。認証が無効なら停止し、`gh auth login` を案内する。
5. 変更範囲に応じて検証する。
   - 常に `git diff --check` を実行する。
   - `news_bot/` を変更した場合は `python -m pytest news_bot/tests -q` を実行する。
   - `vod_bot/` を変更した場合は `python -m pytest vod_bot/tests -q` を実行する。
   - `.github/workflows/` を変更した場合は利用可能なら `actionlint` を実行する。無ければYAMLを構文解析し、変更目的に応じた静的確認を行う。
   - 失敗した検証を無視しない。修正が依頼範囲内なら修正して再実行し、範囲外なら停止して報告する。
6. 未コミットの変更がある場合は、対象ファイルだけを明示的にstageする。`git add .` は使わない。
7. 差分を要約した簡潔なコミットメッセージを作り、コミットする。既存コミットを勝手にamendしない。
8. `gh pr view --json url,state,isDraft` で現在のブランチに既存PRがないか確認する。既存PRがあれば新規作成せず、そのURLを返す。
9. `git push -u origin HEAD` で現在のブランチをpushする。force pushしない。
10. 変更内容からPRタイトルと本文を作り、`gh pr create --draft --base main` でDraft PRを作成する。
    - タイトルは変更の目的を1行で表す。
    - 本文に `Summary` と `Tests` を含める。
    - 実行していない検証を実行済みと書かない。
    - ユーザーがReady for reviewを明示した場合のみDraftを外す。
11. `gh pr view --json url,title,isDraft,state` で作成結果を確認し、URL、コミット、検証結果を報告する。

## 禁止事項

- `main` へ直接pushしない。
- 無関係な変更をstage、commit、破棄しない。
- `git reset --hard`、force push、既存PRのclose/mergeを行わない。
- 検証失敗や未コミット変更を隠さない。
