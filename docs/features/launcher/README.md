# Launcher UI

このディレクトリは、アプリ起動の既存計画と、手首メニュー・空間ランチャーの比較計画の入口です。
**手動の文書索引です。managed feature manifest は未登録です。**
下記の LX00–LX07 は新計画内の作業 ID であり、docs resolver の登録済み task ではありません。

| 読む目的 | 文書 | 状態 |
| --- | --- | --- |
| 手首ラジアル、空間へ切り離す一覧、drag-out、テーマ、複数案の実装と選別 | [Spatial Launcher UI 比較計画](spatial-launcher-experiment-plan-20261121.md) | proposed。製品実装・Quest 受入の証拠ではない |
| 同じ APK で比較し、採用案を記録する | [Quest 比較手順と記録テンプレート](spatial-launcher-evaluation-20261121.md) | runbook / template。測定結果は未記入 |
| 既存ランチャーのカタログ・ファイル指定・テーマとの境界 | [2026-11-07 のランチャー計画](application-launcher-plan-20261107.md) | 当時の実装追記と未接続の設計が混在。現行 source と照合する |
| Linux Desktop v3 の起動・所有・回収契約 | [統合計画](../linux-desktop-v3/linux-real-app-v3-integration-plan-20261111.md)、[I5 詳細設計](../linux-desktop-v3/linux-real-app-v3-i5-detailed-design.md) | UI 比較で契約を弱めない |

## 最初に読む範囲

1. [全体の文書入口](../../overview/README.md)と repository の AGENTS.md を読む。
2. 比較計画の「現行との差分」「比較する三つのプリセット」「実装順」を読む。
3. 実装する LX 作業に関係する source と、起動・入力・テーマの既存契約を確認する。
4. 実機で比較する前に、評価手順の必須条件と記録欄を確認する。

新計画は **手首の近くに置く円盤状の quick menu** を共通の入口にし、詳細一覧の呼び出し・配置を
三案で比較します。円／六角形は別のテーマ軸です。操作方式の採用と skin の採用を混同しません。

コードの存在、Host/JVM 試験、Android build、Quest での手入力・使いやすさは別々に記録します。
採用が決まった後に不要なプリセットを削除し、古い保存設定を採用案へ移行するまでが計画の範囲です。
