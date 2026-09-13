# Qiita記事への認可実験反映案

## 記事で先に出す結論

OIDC基盤を「ピーク時の新規ログイン数」だけでサイジングしない。一般消費者向けサービスでは、ログイン後のrefresh、画面/API操作ごとの認可、共有関係の変更、権限解除の反映、依存障害からの回復が独立して発生する。今回の単一Mac実験は本番容量を決めるものではないが、負荷モデルを少なくとも login / refresh / protected API / authorization change に分ける必要性を実測で示した。

## 採用条件として書けること

- 架空ユーザー1,000人・アルバム1,000件、許可80%/拒否20%、OpenFGA cache無効・higher consistencyの条件で、直接DB認可とOpenFGA認可を比較した。
- 25/100 protected API req/s、共有5/50の正式比較は両モード各3回成立し、accuracy 100%、誤許可0、drop 0だった。
- 低混合はAPI25 + login1 + refresh5、高混合はAPI25 + login5 + refresh20を各3回実行し、login/refresh成功率100%、誤許可0だった。
- 共有解除の完了後誤許可0、古いeventによる権限復活0。変更中は安全側拒否となり可用性コストが発生した。
- OpenFGA、Keycloak、業務API、共有DBを各60秒停止する試験を3回ずつ行い、誤許可0、再開後40秒で3つの10秒窓が連続成立した。
- 古いbackupは古い権限も復元する。backup後に解除した権限が復活するリスクを検出したため、restore後のreconcileを再開条件にする必要がある。
- E7はAPI 90,001件、login 3,600件、refresh 18,001件を1時間で完了し、全成功・誤許可0・drop 0。API p99 8.409 ms、login p99 99 ms、refresh p99 14 msだった。
- E7の後半メモリ傾きはDB +12.17 MiB/h、API +8.41 MiB/h、k6 +30.05 MiB/hだった。上限到達はないが、長期保証やmemory leak不存在とは書かない。

## 記事で避ける表現

- 「100 req/sがMac miniの限界」：200 req/sはk6 VU不足であり、server境界は未到達。
- 「2倍負荷から30秒で回復」：25→50 req/sで劣化せず、過負荷に到達していない。30秒は3窓確認に必要な最短時間。
- 「共有数を増やしても性能は変わらない」：単純model・1,000 album・共有50までの限定結果。
- 「PostgreSQL failoverを確認」：確認したのは単一container再起動だけ。
- 「DR/RPOを達成」：local logical dump/restoreだけ。
- 「1時間成功したのでmemory leakなし」：1時間の線形傾向を観測しただけ。
- 「OpenFGAが直接DBより優れている/劣っている」：遅延差だけでなく、policy表現、整合性、運用、障害点、監査性を含む設計判断である。

## 残る採用課題

本番候補では、実traffic比率、token lifetime、refresh jitter、MFA/外部IdP、bot/credential stuffing、より深いrelation graph、cache方針、複数OpenFGA/Keycloak node、DB分離・replica、rolling deploy、backup暗号化・PITR、複数時間/日 soakを追加する。容量計画は「ログイン/s」一つではなく、各flowの到着率、1 flowあたりHTTP/DB/認可回数、失敗時retry、必要headroomを入力にする。
