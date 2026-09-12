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

## 2026-09-12：対象Macでの受入と比較実験

上記「未実施」は引き継ぎ時点の履歴として残す。2026-09-12にMac mini（Apple M4、arm64、24GB）、Docker VM 4 CPU/約12GBで、Keycloak 26.7.3、PostgreSQL 17.6、k6 1.8.1の全イメージが`linux/arm64`で動作することを確認した。

- 最初のsmokeはDocker仮想ディスク120GBの空き不足により、Keycloak DB migrationで起動前失敗した。失敗runを残し、既存Dockerデータを削除せず上限を160GBへ拡張した。
- 再smokeは終了コード0、1/1成功。認可画面、資格情報、Code + PKCE S256、refreshまで実Keycloakで成功した。
- 低負荷L=1/s、R=5/s、60秒でlogin 61/61、refresh 300/300、dropped 0。KC/DB/k6、JVM/HTTP/Agroalを取得し、観測間隔6.217〜7.277秒、連続観測エラーなしを確認した。
- 単独探索後にL=20 login flows/s、R=100 refresh requests/sを固定。login単独、refresh単独、一定混在を各3試行、同じ平均更新数で0〜200/sへ変動させる混在を3試行した。
- 本比較12試行は全フロー成功率100%、dropped 0、完全サンプル。login p99中央値は単独75ms、一定混在76ms、変動混在78ms。refresh p99中央値は単独5ms、一定/変動混在6ms。
- Keycloak CPU中央値はlogin単独126.59%、refresh単独30.02%、一定混在145.75%、変動混在146.36%。全試行のDB waiting peakとAgroal awaiting peakは0。
- k6 peak CPUは本比較最大7.30%で、生成器が先に詰まった兆候はなかった。Keycloakは2 CPU上限に対しpeak最大181.16%で、容量限界は探索していない。

実行コードは本比較で`040928d71e3e0085bf2d653c31f31c45e6d6ca70`、worktree clean。Python 3.10でk6の可変桁RFC3339小数秒を処理する修正と、測定区間の資源集計・manifest provenanceを追加した。認証フローや負荷閾値は変更していない。

公開可能な全試行台帳、3試行の値、図、解釈と限界は [final-report.md](../results/final-report.md) を根拠とする。run単位の生ログ・tokenを含み得るデータはGit対象外のまま保全する。
