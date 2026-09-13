# Qiita記事原稿

- 主記事：[OSS認証・認可アーキテクチャ](qiita.md)
- 実測の詳細：[認証負荷試験の方法・結果](oidc-measurement.md)
- 公開JSONから再計算した全12試行：[generated-results.md](generated-results.md)
- 入力ハッシュと検査結果：[evidence-check.json](evidence-check.json)
- 結果PRのレビュー：[pr1-review.md](pr1-review.md)
- 編集の履歴：[readability-review.md](readability-review.md)

次の実験はクラウドを使わずMac miniで完結させる。[実行指示と完了条件](../docs/mac-mini-authz-handoff.md)に従い、実測結果が出てから本文の合否を更新する。クラウド図は参考設計として残す。

## 読者と記事の位置づけ

一般消費者向けサービスで、ログイン実装から認可・可用性・運用へ進もうとしているバックエンドエンジニアとテックリード向け。

写真アルバムの共有を一貫した例に使い、Keycloak・OpenFGA・業務APIの責任分担、権限変更の整合性、ローカルとGCP/AWS/Azureへの配置、障害時の影響、試験の順序を説明する。既存の認証実験は、構成の一部を実際に測った例として活用する。

「大規模認証の性能を証明した」「3クラウドで構築・検証した」とは書かない。認可モデル・クラウド配置は設計案で、実装やIaCは提供していない。読者が構成を選び、実装・試験の順序を決められることを記事の価値とする。

タグ案：`Keycloak`、`OpenFGA`、`認証`、`認可`、`アーキテクチャ`。

## 数値とグラフの再生成

```sh
python3 scripts/build_article.py
```

公開集約値だけを読み、Mac miniの生ログは不要。日本語グラフはmatplotlib 3.10.6とHiragino Sans、Noto Sans CJK JP、IPAexGothicのいずれかを使う。

```sh
python3 -m venv .venv-article
.venv-article/bin/python -m pip install matplotlib==3.10.6
.venv-article/bin/python scripts/build_article.py --reader-figures
```

SVGは `figures/`、PNGはGit対象外の `.preview/` に生成する。主記事では比較図1枚、実測詳細では比較図と予備実験との差の2枚を使う。構成図8枚は本文内のMermaidが原本で、構成・サービス間通信・アクセスの流れ・ER・ローカル・GCP・AWS・Azureを表す。

## 投稿時の確認

Qiitaには未投稿。

1. 主記事のグラフ1枚をアップロードし、画像リンクを置換する。
2. 8枚のMermaidと表をQiitaプレビューで確認する。クラウド図は同じ順序・役割で描いている。
3. 相対リンクをGitHubの各ページへの絶対リンクに置換する。実測詳細は補足資料としてリンクする。
4. 冒頭の実装・未実装の区分、予備実験との差、監視収集エラー、測定範囲の制約を残す。
5. クラウド案の実装時は採用リージョン・SKU・バージョン・ネットワーク設定を再確認する。公式資料の確認日は2026-09-13。

10回の通読記録は以前の実験中心の記事に対するもの。主題変更後の改稿を、同じ版の10回レビューと扱わない。
