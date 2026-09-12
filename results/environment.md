# 実験環境

- 実施日: 2026-09-12 (JST)
- Git HEAD（開始時）: `f06b29eff4b69e57403418e27368dc05faa07b00`
- ホスト: Mac mini / Apple M4 / 10 CPU cores / 24 GB memory / arm64
- macOS: 26.6.2
- Docker Engine: 29.2.1
- Docker Compose: v5.0.2
- Docker Desktop: 4.63.0
- Docker VM: aarch64 / 4 CPU / 約12.0 GB（`docker info` の `MemTotal=12001992704`）
- Docker仮想ディスク上限: 開始時120 GB。空き48 MiBでKeycloak DB migrationが失敗したため、他プロジェクトのデータを削除せず160 GBへ拡張。拡張後のVM空きは約40.2 GB（`df`表示約38 GiB）。
- Python: 3.10.5
- Node.js: 24.13.1（Homebrew側Node 25.8.1は共有ライブラリ不整合のため、既存nvm版をコマンドPATHで明示）
- ホスト空き容量: 実験開始前 約69 GiB、Docker再起動後 約84 GiB
- 背景負荷: 実験開始前に既存コンテナを停止。実験中は `caffeinate` でスリープを抑止する。
- コンテナイメージ: Keycloak 26.7.3、PostgreSQL 17.6、k6 1.8.1はいずれも`linux/arm64`を確認。
- 実効パスワードハッシュ: Argon2id v1.3、iterations 5、memory 7168、parallelism 1、hashLength 32。秘密ハッシュ本体は取得・記録していない。

ホスト名、シリアル番号、他プロジェクト名、完全な環境変数一覧は記録しない。
