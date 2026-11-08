# 型付きGUIとfoot専用機能の統合境界

2026-11-08、Build175準備。通常GUI配線を窓グループ実装へ統合した際、
`window_launch::correlate`がGUIのreceipt/cookieにも成功を返すようになった。
この一般化された成功だけでfootへ分類すると、正常に起動したGUIまでfootのグループへ入ってしまう。

`inspect_foot`は既存のsession/nonce/Board/fulltarget/一意な起動相関を維持し、
一致したlaunch IDのreceiptが`mode=terminal`であることも要求する。
通常IMEのeligibilityもterminal receiptを要求し、footに似たapp_idだけでは許可しない。
GUI用receiptとGUI用cookieがともに正しくても、foot分類への許可にはしない。

native回帰fixtureで、同じ共有foot sessionにある正常なGUIを除き、元footだけをcaptureすることを確認した。
KotlinにはGUI/未知/欠落/不正型modeの拒否を追加し、統合APKのJVM検証対象とした。
旧group fixtureの`TerminalLaunch`と終了`Finished`にも、新しいmode/GUI回収fieldの明示初期値を補った。
初回のfixture field不足によるcompile失敗は作業ログに保持している。

host検証はmodel106件、transport66件、追加したcore境界1件、workspace all-target clippyがPASS。
Python442件中435件実行・7件skip、Termux114件もPASS。
これらを通常GUIの実機起動・入力・SDK回収や、グループの実機アニメーションの合格には数えない。
