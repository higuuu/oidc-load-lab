# 検証状態

## 準備環境で確認する項目

2026-09-10: 以下の静的検査、JavaScriptのモック/設定テスト4件、Python集計テスト1件が成功。公開対象18ファイルの既知機密パターン検査も成功。これは実Keycloakの疎通成功を示すものではない。

- Python構文、Node.jsによるJavaScript構文。
- Docker Compose設定の整合性。サービス起動を意味しない。
- 負荷形状の面積が一定負荷と等しく、比較条件の初期セッション数が等しいこと。
- モックHTTPでS256 challengeとverifierの対応、state不一致の拒否、更新時のRT差し替え。
- 集計でwarmupと失敗を成功件数へ混ぜず、任意の元タグを出力しないこと。
- 公開用allowlistの機密パターン検査と、実行時ファイルが書き出されないこと。

## 未実施：対象Macで必要な受入

1. Docker Desktopを起動し、ARM64イメージの取得・Keycloakのビルドが成功する。
2. 1,000ユーザーをインポートし、health/discoveryが成功する。
3. `run smoke` が終了コード0、smoke成功率100%となる。
4. 低負荷runで `summary.json` と `samples.json` が生成される。`host.jsonl` にKC/DB/k6が入り、metricsにJVM/HTTP/DBプール等の利用可能な系列がある。
5. `report.py` の試行数・成功数・p99がsummary/サンプルに整合する。
6. 独立したRTで更新が継続し、`invalid_grant`相当の400や`session_lost`が出ない。
7. k6自身のCPU/メモリ余裕、dropped=0、対象の資源を確認してから負荷を上げる。

準備環境ではDockerデーモンが稼働していないため、コンテナ起動・実Keycloak互換性・ARM64動作・性能は確認していない。モックテスト成功をE2E成功と呼ばない。Mermaidはテキストで同梱しており、Qiita側の描画確認は投稿準備時に行う。
