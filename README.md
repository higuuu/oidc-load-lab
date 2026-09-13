# OIDC Load Lab

**ログイン数だけで認証基盤をサイジングしてよいのか？**

Keycloakのログイン・Refresh Token更新・混在を、Mac mini / メモリ24GB / 土日で比較するための公開用実験キットです。認証処理とトークン更新の負荷を扱います。ドメイン権限判定の性能評価は含みません。

2026-09-12にMac mini 24GBで実Keycloakへのsmokeと比較実験を実施しました。対象機で確認した範囲は [validation.md](docs/validation.md)、全試行・集計・限界は [最終報告](results/final-report.md) を参照してください。これは単一Mac上の比較であり、本番容量や他環境の性能を保証しません。

## 最初に用意するもの

- Mac mini 24GB、Docker Desktop + Compose、Python 3.10以上。静的テストにはNode.js 22以上。
- Docker VMはメモリ12GB・CPU 4個を初期条件にする。変更した場合は全比較で統一して記録する。
- コンテナ上限はKeycloak 3GB/2CPU、PostgreSQL 2GB/1CPU、k6 2GB/1CPU。合計7GB/4CPUに加えてVM等の余白が必要。
- 空きディスク20GBを目安に確保。これは所要量の実測値ではありません。
- Keycloak 26.7.3、PostgreSQL 17.6、k6 1.8.1を固定。ARM64ネイティブでの起動とイメージIDは対象機で確認する。最新バージョンを自動追従しない。

## 実行

このディレクトリで実行します。最初のコマンドはネットワークへ接続せず、ローカルの架空ユーザーとランダムな実験用パスワードを生成します。

```sh
python3 scripts/lab.py init
python3 scripts/lab.py check
```

Docker Desktopを起動してから、まず1ログイン＋1更新の疎通だけを行います。

```sh
python3 scripts/lab.py run smoke
```

**`run` は毎回、このComposeプロジェクト専用のDBボリュームを削除して初期化します。** 実験の初期状態を揃えるためです。このプロジェクトに実データを入れないでください。既存の別プロジェクトのボリュームは対象にしません。初回のイメージ取得・ビルド・1,000人のパスワードハッシュ生成は測定に含めません。

疎通が成功したら、低負荷から進めます。以下の5/s・20/sは初期入力値であり、耐えられることを確認した値ではありません。

```sh
python3 scripts/lab.py run login --login-rate 5
python3 scripts/lab.py run refresh --refresh-rate 20
python3 scripts/lab.py run mixed --login-rate 5 --refresh-rate 20
python3 scripts/lab.py run mixed-burst --login-rate 5 --refresh-rate 20
```

各負荷実験はセッション準備後、ウォームアップ60秒＋測定180秒＋最大35秒の完了待ちです。更新セッションは準備中のメモリだけに保持し、コード交換から取得します。ログイン用アカウントと更新用アカウントは分離しています。

出力された実験フォルダ名を使って集計します。

```sh
python3 scripts/report.py <results内の実験フォルダ名>
python3 scripts/lab.py stop
```

`report.md` / `aggregate.json` は集計、`timeline.svg` は5秒ごとの試行開始数・成功完了数/秒とp99の図、`samples.json` は時系列、`host.jsonl` はコンテナとDB統計、`metrics-*.prom` はKeycloakメトリクスです。各runの生データはGit対象外です。閾値違反でも集計を実行し、失敗した試行を残します。`summary.json` がなければ、まず `console.log` をローカルで確認してください。

公開レビュー用の集約結果は `results/final-report.md` と `results/public/` に限定しています。ローカルの全runから同じ集約を作るには次を実行します。

```sh
python3 scripts/analyze_results.py
```

## 何を比較できるか

| モード | ログイン到着 | 更新到着 | 目的 |
|---|---|---|---|
| `login` | 一定 | なし | ログイン単独の基準 |
| `refresh` | なし | 一定 | 更新単独の基準 |
| `mixed` | 一定 | 一定 | 同じ基盤を共有したときの干渉 |
| `refresh-burst` | なし | 周期的な増減 | 更新だけの負荷形状の影響 |
| `mixed-burst` | 一定 | 周期的な増減 | 更新の集中が新規ログインに与える影響 |

更新の変動は60秒周期で平均R、ピーク2R、底0になる三角形です。同じR・測定時間なら一定負荷と予定更新件数を揃えられます。**実クライアントの期限切れによる自動更新・同時発火・ジッター制御を再現するモデルではなく、更新リクエストの到着形状を比較する実験です。** 発生原因まで検証したとは書きません。

ログイン1回は認可リクエスト、パスワード送信、コード交換の3 HTTPリクエストです。`flows/s` とHTTP `requests/s` は異なります。ブラウザの描画・人の入力・TLS・外部IdP・MFAは計測外です。

## 公開するファイル

```sh
python3 scripts/public_bundle.py
python3 scripts/public_bundle.py --export
```

`dist/oidc-load-lab/` に公開対象だけを書き出します。**後で作るGitリポジトリには、この中身を入れてください。** 研究用親ディレクトリや `results/` をまとめてコミットしないでください。書き出し済みディレクトリが存在する場合は上書きせず停止します。

- 公開対象を明示列挙し、秘密鍵・JWT・一部クラウドトークン・個人ホームパスのパターンを検査。
- `.env`、インポートデータ、鍵を含みうるDB、生ログ、run単位の観測データを除外。レビュー済みの集約結果だけを明示列挙。
- 環境変数一覧・完全なDocker inspect・個人のホスト名を収集しない。
- 検査はすべての機密を検出できる保証ではありません。結果公開時の手順は [publication.md](docs/publication.md)。

## 文書

- [Mac mini：認証・認可の実装から実験完了までの指示](docs/mac-mini-authz-handoff.md)

- [OSS認証・認可アーキテクチャの記事原稿](article/qiita.md) / [認証実験の詳細](article/oidc-measurement.md) / [PRレビューと検証範囲](article/pr1-review.md)
- [Mac mini側の担当者・エージェントへの実験指示書](docs/mac-mini-handoff.md)
- [実験設計・非機能要件・土日の進め方](docs/experiment.md)
- [構成図・認証シーケンス・概念ER図](docs/architecture.md)
- [Qiitaで伝える内容と公開手順](docs/publication.md)
- [検証済み範囲と対象機での受入項目](docs/validation.md)

この構成はローカル実験専用です。HTTP、固定の内部issuer、単一DB、ブルートフォース保護無効など、負荷を切り分ける条件を明示しています。ポートはホストのloopbackだけに公開し、実運用には使用しません。
