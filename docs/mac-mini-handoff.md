# Mac miniで実行する担当者・エージェントへの指示

この文書を実験の引き継ぎとして読み、環境確認から実測・分析・報告まで進めてください。これは実験の実行指示であり、既に測定が成功したという記録ではありません。

## 1. 背景と最終目的

公開リポジトリ: https://github.com/higuuu/oidc-load-lab

最終目的は、一般消費者向けサービスの認証基盤について、再現可能な実測に基づくQiita記事を書くことです。想定読者はOIDCの基礎を知るバックエンドエンジニア、SRE、テックリード。著者が「OSSの設定だけでなく、認証フローの負荷をモデル化し、測定条件と限界を説明できる」と伝わる成果にします。

中心の問いは **「ログイン数だけでOIDC認証基盤をサイジングしてよいのか？」** です。

比較するのは、Keycloakのログイン単独、Refresh Token更新単独、両者の混在、同じ平均更新数で更新到着率を変動させた混在です。最大TPSの記録や、本番数百万ユーザーをこのMacで検証したという主張を目的にしません。

以前は会員停止・4秒キャッシュを中心に検討しましたが、その方向は採用していません。キャッシュTTL、会員DB、OpenFGA、B2Bテナント分離へ中心を戻さないでください。今回はOIDCログインとトークン更新です。厳密にはトークン更新はOAuthの処理であり、本記事ではOIDC利用基盤の負荷として扱います。

ハードウェアは **Mac mini / メモリ24GB**。作業は **土日、約14時間** を目安にします。SoC世代・CPUコア数・Docker実行環境は未確認なので、現物を確認してください。

## 2. 引き継ぎ時点の状態

初回実装のコミットは `54af46c`。実行時はこの指示書を含むcheckoutのHEADを記録し、初回コミットへ戻さないでください。

準備側で確認済みなのは、Compose構成、構文、JavaScriptの設定/モックテスト4件、Python集計テスト1件、公開用ソースの既知機密パターン検査です。

**未確認なのは、実Keycloakとの疎通、コンテナイメージの対象機での取得・ARM64動作、実負荷、監視データの完全性、実測グラフです。** 準備側ではDockerデーモンが起動していませんでした。最初の担当作業は、この境界を実際のE2Eで越えることです。

不具合が見つかることは想定内です。実験キット内の小さな修正は原因を記録して進めてください。修正前の失敗記録を残し、修正後のコードと条件で比較対象を取り直してください。

読む順番:

1. この文書、`README.md`
2. `docs/experiment.md`、`docs/architecture.md`
3. `compose.yaml`、`Containerfile`
4. `scripts/lab.py`、`load/config.mjs`、`load/oidc.js`
5. `scripts/report.py`、`docs/validation.md`、`docs/publication.md`

文書と実装が食い違ったら、実装を調べて差を報告し、実験条件としてどちらを採用したか明記します。意味を変える修正を黙って行わないでください。

## 3. ローカル作業と公開の範囲

この依頼で進める作業は、専用checkout内での環境確認、コンテナ取得・ビルド・実行、架空データ作成、実験、通常の不具合修正、集計、記事素材の作成です。承認済みの範囲を毎段階で聞き直す必要はありません。

新規の有料サービス、別マシン、クラウド構築、実ユーザーデータは不要です。他のプロジェクトを停止・削除したり、Mac全体の設定を勝手に変更したりしないでください。Docker VMの配分変更で他の作業が影響を受ける場合だけ調整を依頼します。

**このrepoはpublicです。実験の実行許可を、生ログや結果の無審査公開と解釈しないでください。** 最終成果をローカルでレビュー可能な状態にして報告します。追加の指示がなければ、実測結果のpushやQiita投稿は行いません。

- `.env`、`.runtime/`、`results/` はGit対象外。実在アカウント、メール、秘密鍵を入れない。
- パスワード、JWT、認可コード、セッションID、Cookie、個人のホームパスをチャットや公開文書へ出さない。
- `docker compose config` は秘密値を展開し得るので、構成検査は `config --quiet`。完全な環境変数一覧やDocker inspectを出力しない。
- 原因調査で生ログを読む場合も、報告は秘密値を除いたエラー分類と必要最小限の内容にする。
- `git add .`、`git add -f results`、DBダンプの追加は行わず、変更対象を明示して確認する。
- 実験を複数のcheckout/プロセスから同時実行しない。Composeのproject名は共通で、ファイルロックはcheckout内にしか効かない。

## 4. 環境確認とcheckout

新規なら専用作業ディレクトリ内で次を実行します。

```sh
git clone https://github.com/higuuu/oidc-load-lab.git
cd oidc-load-lab
git status --short --branch
git rev-parse HEAD
```

既存checkoutなら変更を保全して状態を確認します。未コミット変更をreset/stashして上書きしません。必要に応じてfetchし、cleanな場合だけfast-forwardします。

```sh
uname -m
sysctl -n machdep.cpu.brand_string
sysctl -n hw.memsize
sysctl -n hw.ncpu
sw_vers -productVersion
docker version --format '{{.Client.Version}} / {{.Server.Version}}'
docker compose version
docker info --format 'arch={{.Architecture}} cpus={{.NCPU}} memory={{.MemTotal}}'
python3 --version
node --version
```

Docker Desktopを起動し、VMの初期配分を12GB/4CPUに合わせます。Pythonは3.10以上、Node.jsは22以上。k6をホストへインストールする必要はありません。ホストの負荷生成器を追加しないでください。

空きディスク、他の重い処理、メモリ圧迫、スリープの影響を確認します。測定中はスリープを避け、同じ背景負荷に揃えます。ホスト名、シリアル番号、他プロジェクトのコンテナ一覧は公開記録へ残しません。

ARM64でのネイティブ実行を確認します。ホストがARM64でもイメージがamd64エミュレーションなら条件が違います。イメージのArchitectureだけを絞ってinspectし、確認した値を記録します。

## 5. 固定する実験条件

| 項目 | 初期条件 |
|---|---|
| Keycloak | 26.7.3、3GB/2CPU、heap初期1GB/最大2GB |
| PostgreSQL | 17.6、2GB/1CPU、shared_buffers 256MB |
| k6 | 1.8.1、2GB/1CPU |
| KC DBプール | 初期/最小10、最大20 |
| 架空ユーザー | 1,000人、同じランダムな実験用パスワード |
| パスワード処理 | Argon2、hashIterations 5。その他の実効設定を確認 |
| OIDC client | public、Code + PKCE S256、scope openid |
| 無効なフロー | password grant、implicit、service account |
| RT | rotation有効、maxReuse 0、VUごとに保持 |
| AT寿命 | 300秒。負荷生成の更新周期はこの値と連動しない |
| Session | idle 7,200秒、max 14,400秒 |
| 生成器 | 各シナリオ100VU、事前セッション200件 |
| 各run | setup → warmup 60秒 → 測定180秒 → 完了待ち最大35秒 |
| 通信 | Docker内部HTTP、ホストへの公開はloopbackのみ |

これらは「本番推奨値」ではなく、比較の初期条件です。イメージ取得に失敗した場合、エラーと公式の配布情報を確認します。無条件にlatestや別アーキテクチャへ変更しないでください。版を変更した場合は実験条件・文書・manifestも揃えます。

## 6. 初期化とsmoke：ここを通るまで性能を測らない

新規cloneでは次を実行します。

```sh
python3 scripts/lab.py init
python3 scripts/lab.py check
python3 scripts/lab.py run smoke
```

既に初期化済みの場合、`init` が停止するのは保護動作です。秘密値を表示せず `.env` と `.runtime/import/realm.json` が両方存在するか確認します。片方だけある状態を、設定を推測して補完しないでください。

**`run` は毎回 `oidc-load-lab` 専用DBを削除・再作成します。** インポートとパスワードハッシュ生成は測定外です。別プロジェクトのDB、Docker全体のprune、共有ボリューム削除は不要です。

smokeの合格は、認可画面取得 → パスワード送信 → code受領 → PKCEによる交換 → refresh成功、かつ終了コード0です。1回の機能試験なのでp99や容量を論じません。

実装は302のLocationを読み、コールバックへHTTPアクセスしません。`127.0.0.1:18081` にサーバーを起動する必要はありません。issuerはDocker内部の `http://keycloak:8080/realms/oidc-lab`。ホスト向けURLへ不用意に書き換えないでください。

注意: state/nonce/issuer/audience/expiryは確認していますが、生成器はID Tokenの署名検証をしていません。完全なOIDC RP適合性試験ではありません。

## 7. 観測の受入：低負荷を1回だけ実行

```sh
python3 scripts/lab.py run mixed --login-rate 1 --refresh-rate 5 --seconds 60 --vus 100
```

このrunは計測経路の確認用です。本番比較の180秒runと混ぜません。出力されたフォルダ名を指定します。

```sh
python3 scripts/report.py <results内の実験フォルダ名>
```

次を確認してください。

- `manifest.json`、`summary.json`、`samples.json` が存在し、入力条件と終了コードが合う。
- `lab_attempts` / `lab_completed` / `lab_latency_ms` を `phase=measure`、フロー別に集計できる。
- `host.jsonl` にKC・DB・k6の観測がある。one-offのk6が欠けていない。
- `metrics-*.prom` が複数あり、使えるJVM/HTTP/DBプール系列を特定できる。系列名は実物を見て選ぶ。
- `observation_error` が連続していない。実際のサンプリング間隔を計算する。5秒の待ち時間に各収集コマンドの時間が加わるため、厳密な5秒周期ではない。
- 集計の件数と成功率を元サンプルで照合し、途中終了を成功扱いしていない。
- `timeline.svg` を表示して文字・軸・欠測・単位を確認する。これは成功件数/秒とp99であり、成功割合のグラフではない。

ここで観測の不足を直します。監視方法を比較途中で変えた場合は、影響する条件を取り直します。

## 8. 単独負荷を探索し、本比較のL/Rを決める

最初はlogin 1→5→10→20 flows/s、refresh 5→20→50→100 requests/sなど、小さい値から段階を上げます。これは探索の候補で、到達保証ではありません。必ず各runを集計してから次へ進めます。

```sh
python3 scripts/lab.py run login --login-rate 1 --seconds 180 --vus 100
python3 scripts/lab.py run refresh --refresh-rate 5 --seconds 180 --vus 100
```

判断基準は各フロー成功率99%以上、login p99 < 2,000ms、refresh p99 < 500ms、dropped_iterations=0です。研究用の暫定基準であり、実サービスのSLOと称しません。

閾値を初めて超えたら、上の段階へ進まず、サーバ・生成器・設定不備を切り分けます。k6に余裕があり単独で基準を満たすL/Rの組を本比較用に固定します。余裕の大きい低負荷しか試していない場合は、その範囲で差がなかったという結論に留めます。

最初の探索と本比較は区別し、選定理由を記録します。結果を見て都合のよい試行だけを選びません。

## 9. 同じL/Rで比較、各3試行

以下の5と20は例です。探索で選んだL/Rを代入して使用します。

```sh
python3 scripts/lab.py run login --login-rate 5 --seconds 180 --vus 100
python3 scripts/lab.py run refresh --refresh-rate 20 --seconds 180 --vus 100
python3 scripts/lab.py run mixed --login-rate 5 --refresh-rate 20 --seconds 180 --vus 100
python3 scripts/lab.py run mixed-burst --login-rate 5 --refresh-rate 20 --seconds 180 --vus 100
```

順序は、1巡目login→refresh→mixed→mixed-burst、2巡目は逆順、3巡目refresh→mixed-burst→login→mixed。毎回集計し、失敗も台帳に残します。時間が足りなければburstを後回しにし、単独2条件＋混在の3条件×3試行を完成させます。

識別する点:

- `LOGIN_RATE` は完了ログインを目指すフローの到着数。通常1ログインは3 HTTPリクエスト。更新は1 HTTPリクエスト。
- 一定到着率の入力は応答が遅くなっても予定どおり開始する設計。VU不足で開始できなければdroppedが増える。
- `mixed-burst` は15秒ずつ R→2R→R→0→R、合計60秒。一定負荷と予定更新数を揃える。瞬間的な全端末同時更新やTTLによる同期の再現ではない。
- VU数は利用者数ではない。単独では100VU、混在では各100VUで合計200VUが事前割当される。生成器の仕事量も違うのでk6資源を比較する。
- 全条件のsetupで200セッションを作るが、warmup開始後はログイン成功分だけ増える。測定開始時点のセッション数が全条件同じ、とは書かない。
- ログイン単独のsetupにも200ログインがある。総 `http_reqs` はsetup/warmupを含み得るため、測定180秒の要求数にそのまま使わない。

## 10. 失敗時の切り分け

| 症状 | 最初に確認すること |
|---|---|
| Docker接続不可 | Docker Desktopが起動しているか、正しいcontextか。別環境へ自動切替しない |
| image取得/ビルド失敗 | タグ、registry到達性、アーキテクチャ。負荷限界とは無関係 |
| readiness失敗 | KC/DBの起動、import、メモリ、DB接続。監視開始前の失敗ではmanifestがないこともある |
| authorize/login_form/credentials_http | テーマのform selector、リダイレクト、ユーザーのrequired action、import状態 |
| callback_state/id_claims | state/nonce、issuer、client audience、時刻。検証を外して先へ進めない |
| http_400 / session_lost | 最初の更新失敗、RT所有権、rotation、セッション寿命。再ログインで隠さない |
| dropped_iterations | VU不足、k6 CPU/メモリ、対象遅延、VM全体。droppedだけでKC限界と決めない |
| 極端に短い失敗latency | 壊れたセッションのローカル拒否がないか。速い失敗を性能改善に数えない |
| 観測器のエラー | one-off k6の検出、DB統計権限、metricsの到達性。欠測を0として補完しない |
| 終了コードが非0 | 閾値違反とスクリプト/起動エラーを分ける。summaryとローカルログを確認 |

RT更新失敗後はそのVUが `broken` となり、後続の試行をネットワーク送信せず失敗にします。従って `lab_attempts` が多くても、同数のHTTP更新を送ったとは限りません。`session_lost` の発生以降は正常な継続更新の容量試験として解釈せず、最初の失敗とHTTPリクエスト数を調べます。

直す範囲は実験に必要な最小限にします。password grantへの置換、PKCE無効化、ハッシュ負荷の低減、閾値の緩和で成功させないでください。原因確認のため条件を変えたrunは、別の診断試行として記録します。

VM/VU/CPU配分、コード、版、ハッシュ設定を変えた場合は比較セットを取り直します。VUSは初期セッション数も変えるので、単純な生成器調整ではありません。

## 11. 集計・図・解釈

ローカルの `results/experiment-ledger.md` に、runフォルダ、コードHEAD/変更の有無、入力条件、試行順、合否、失敗理由、採用区分を記録します。`results/environment.md` には個人情報を除いた環境条件を記録します。現行manifestはコードHEAD・SoC・VM配分の全部を自動記録しません。

最低限の表:

| run | mode | L/s | 平均R/s | peak R/s | 予定/実行/成功件数 | 成功割合 | p95/p99 | dropped | KC/DB/k6資源 | 判定 |
|---|---|---|---|---|---|---|---|---|---|---|
| 実測後記入 | | | | | | | | | | |

3試行それぞれの値と、その中央値・最小最大を出します。各runのp99を平均して「全体のp99」と呼ばないでください。少数サンプルのp99は不安定なのでサンプル件数も併記します。

作成する図:

1. 条件ごとのlogin/refresh p99と成功割合。3試行のばらつきを表示。
2. 予定到着率・実際の試行開始数・成功完了数の時系列。
3. 同じ時間軸のKC/DB/k6 CPU/メモリ、DB待ち、必要に応じてGC/プール。

既存 `timeline.svg` は図2の一部と遅延を描く簡易図です。予定曲線、複数run比較、資源との重ね合わせは未実装なので、分析時に必要な最小限の図を追加してください。

`samples.json` の時刻と `host.jsonl` の `unix_s` で対応させます。監視の `elapsed_s=0` はsetup前で、測定開始ではありません。公開する時間軸は測定開始からの相対秒に変換します。

試行開始と完了のバケットは異なります。5秒バケットの端数、完了待ち、遅延による後ろずれを考慮します。集計のcompleted/sは測定区間に開始した仕事の完了数を予定秒数で割った値で、完了待ち込みの実時間throughputとは区別します。`complete_samples=false` のrunを正常完了と扱いません。

CPUやDB負荷と遅延が同時に増えただけでは因果は確定しません。追加実験で切り分けられなければ「相関を観測、原因は未確定」と書きます。差が出ない、生成器が先に詰まる、条件内では余裕がある、いずれも有効な結果です。

## 12. 最終成果と終了条件

まず `results/final-report.md` に次を揃えて報告してください。

1. 結論。何を観測し、どこまで言えるか。
2. 実機環境、実行したコード版、変更内容、実験条件。
3. smoke結果、観測の受入、全runの台帳。
4. 各条件の3試行の数値・図、失敗/除外の理由。
5. 暫定SLOを満たした範囲と、生成器が制約した範囲。
6. 未確認事項と、次の一つの実験を選ぶなら何か。
7. Qiita用の見出し構成と、実測に基づく導入文の案。
8. 秘密値を除いた公開候補ファイルの明示リスト。

本番のHA/RPO/RTO、TLS、MFA、外部IdP、攻撃耐性、実利用者数への換算は検証していません。単一ホスト・短時間・合成負荷・生成器同居の制約を最終報告にも残します。

実測が成立したら `docs/validation.md` に実施日・実行条件・根拠を追記してよいですが、以前の未実施状態を実績として書き換えないでください。ソース修正は差分レビュー可能な状態にし、公開前に機密検査を実行します。

```sh
python3 scripts/public_bundle.py
git diff --check
git status --short
python3 scripts/lab.py stop
```

`public_bundle.py` は明示されたソースファイルだけを検査します。新しい結果ファイルが安全だという証明にはならないため、公開候補は別途確認します。生ログやトークンが検査結果に現れた場合、内容をそのままチャットへ貼らないでください。

終了時はこの実験のコンテナを停止し、ローカルの観測データと修正を保全します。実行できない場合も、到達段階・直接の阻害要因・必要な操作を具体的に報告してください。「Dockerが動けばできるはず」で終わらず、対象機で可能なところまで検証します。
