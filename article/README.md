# Qiita記事原稿

- 本文: [qiita.md](qiita.md)
- 公開JSONから再計算した全12試行の数値: [generated-results.md](generated-results.md)
- 検査対象と入力ハッシュ: [evidence-check.json](evidence-check.json)
- PRレビュー: [pr1-review.md](pr1-review.md)

タイトル案は本文のものを第一候補とする。タグ案は `Keycloak`、`OpenIDConnect`、`OAuth`、`負荷試験`、`Docker`。狙いは消費者向けサービスのバックエンド/SRE/テックリードに、フロー別負荷の比較方法を持ち帰ってもらうこと。最大性能・独自発見・本番実績を示唆しない。

## 数値と図の再生成

公開済みの集約値だけを読むため、Mac miniの生ログは不要。

```sh
python3 scripts/build_article.py
```

図の生成はmatplotlib 3.10.6を使用。依存関係を分離したPython環境で実行する。

```sh
python3 -m venv .venv-article
.venv-article/bin/python -m pip install matplotlib==3.10.6
.venv-article/bin/python scripts/build_article.py --figures
```

`figures/` にSVG、Git対象外の `.preview/` にPNGを生成する。既存のtraffic/resource図はPRの提供物で、生ログなしに時系列を独立再計算したものではない。本文は新規2図と既存traffic図の3枚を使用する。

## 投稿前の仕上げ

本文・表・図は作成済み。Qiitaにはまだ投稿していない。

1. Qiitaエディタへ本文を移し、画像3枚をアップロードして相対画像リンクを置換する。新規図のPNGは `.preview/`、既存traffic図は必要ならSVGからPNGへ変換する。
2. Mermaidの構成図・シーケンス・概念ER図と、表の描画をQiitaプレビューで確認する。
3. コード例は結果PRの固定コミットを指定している。mainへのマージ状態にかかわらず測定対象を参照できる。リンクを変える場合も実験の来歴を残す。
4. 探索での27ms/45.63%という差、時系列の粗さ、生データ非公開の限界は削らない。

記事はPR #1の集約結果をレビューした原稿。測定の追試やPRのマージ承認を行ったという意味ではない。
